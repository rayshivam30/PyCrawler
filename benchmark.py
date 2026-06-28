"""
PyCrawler Benchmark & Metrics Utility

Measures and logs performance metrics for the PyCrawler stack:
  1. REST API Availability check.
  2. Database stats (unique words, total pages, link edges).
  3. Real-time crawl progress polling (pages/min, queue throughput, 304 hits).
  4. Search latency benchmarks (min, max, average, and p95 latency).

Usage:
  .venv/Scripts/python benchmark.py
"""

import sys
import os
import time
import asyncio
import httpx
from datetime import datetime
from sqlalchemy import select, func

# Ensure we can load app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load .env variables into os.environ before importing app settings
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Override POSTGRES_HOST to localhost when running benchmark on the host machine
# so it connects to the Docker mapped port on localhost instead of container hostname "db"
os.environ["POSTGRES_HOST"] = "localhost"

# Override port to the host-mapped port (e.g. 5435) to avoid local 5432 port conflicts
if "POSTGRES_HOST_PORT" in os.environ:
    os.environ["POSTGRES_PORT"] = os.environ["POSTGRES_HOST_PORT"]

from app.database.database import SessionLocal
from app.models.models import Page, Word, Link, QueueItem, SearchHistory

API_BASE = "http://127.0.0.1:8080/api"


async def fetch_api_stats() -> dict:
    """Fetch live telemetry from the FastAPI stats endpoint."""
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(f"{API_BASE}/stats", timeout=5.0)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            print(f"[-] API connection failed: {e}")
    return {}


async def get_db_metrics() -> dict:
    """Query PostgreSQL directly for structural details."""
    async with SessionLocal() as db:
        try:
            # Unique indexed words
            words_count = (await db.execute(select(func.count(Word.id)))).scalar() or 0
            
            # Total hyperlink edges
            links_count = (await db.execute(select(func.count(Link.id)))).scalar() or 0
            
            # Average links per page
            pages_count = (await db.execute(select(func.count(Page.id)))).scalar() or 0
            avg_links_per_page = links_count / pages_count if pages_count > 0 else 0.0
            
            # Queue status distribution
            completed = (await db.execute(select(func.count(QueueItem.id)).where(QueueItem.status == 'completed'))).scalar() or 0
            failed = (await db.execute(select(func.count(QueueItem.id)).where(QueueItem.status == 'failed'))).scalar() or 0

            return {
                "unique_words": words_count,
                "link_edges": links_count,
                "avg_links_per_page": round(avg_links_per_page, 2),
                "db_completed": completed,
                "db_failed": failed,
            }
        except Exception as e:
            print(f"[-] Database query failed: {e}")
            return {"unique_words": 0, "link_edges": 0, "avg_links_per_page": 0.0, "db_completed": 0, "db_failed": 0}


async def run_search_benchmark(queries: list[str]) -> dict:
    """Run search queries sequentially and measure latency distribution."""
    latencies = []
    success_count = 0
    total_results = 0

    print(f"\n[*] Running Search Latency Benchmark (testing {len(queries)} queries)...")
    async with httpx.AsyncClient() as client:
        for q in queries:
            t0 = time.perf_counter()
            try:
                res = await client.get(f"{API_BASE}/search", params={"q": q}, timeout=5.0)
                t1 = time.perf_counter()
                if res.status_code == 200:
                    latencies.append((t1 - t0) * 1000)  # ms
                    success_count += 1
                    total_results += res.json().get("results_count", 0)
            except Exception:
                pass

    if not latencies:
        return {"avg_ms": 0, "min_ms": 0, "max_ms": 0, "median_ms": 0, "p95_ms": 0, "p99_ms": 0, "success_rate": 0.0, "avg_results": 0}

    latencies.sort()
    n = len(latencies)
    avg_ms = sum(latencies) / n
    min_ms = latencies[0]
    max_ms = latencies[-1]
    
    # Median
    if n % 2 == 1:
        median_ms = latencies[n // 2]
    else:
        median_ms = (latencies[(n // 2) - 1] + latencies[n // 2]) / 2.0

    # Percentiles
    p95_idx = min(int(n * 0.95), n - 1)
    p95_ms = latencies[p95_idx]
    
    p99_idx = min(int(n * 0.99), n - 1)
    p99_ms = latencies[p99_idx]

    return {
        "avg_ms": round(avg_ms, 2),
        "min_ms": round(min_ms, 2),
        "max_ms": round(max_ms, 2),
        "median_ms": round(median_ms, 2),
        "p95_ms": round(p95_ms, 2),
        "p99_ms": round(p99_ms, 2),
        "success_rate": round((success_count / len(queries)) * 100, 1),
        "avg_results": round(total_results / success_count, 1) if success_count > 0 else 0
    }


async def monitor_crawl(duration_sec: int = 15):
    """Poll crawl stats and log performance speed/throughput metrics."""
    print(f"\n[*] Monitoring crawl progress for {duration_sec} seconds...")
    
    start_time = time.time()
    initial_stats = await fetch_api_stats()
    if not initial_stats:
        print("[-] Could not retrieve initial stats. Is the backend running?")
        return

    # Keep track of delta crawling rates
    initial_crawled = initial_stats.get("pages_crawled", 0)
    initial_skipped = initial_stats.get("pages_skipped", 0)
    initial_updated = initial_stats.get("pages_updated", 0)
    
    last_crawled = initial_crawled
    last_time = start_time

    print(f"{'Elapsed (s)':<12}{'Crawled':<10}{'Skipped (304)':<15}{'Updated':<10}{'Queue Size':<12}{'Throughput (p/m)':<18}")
    print("-" * 80)

    for i in range(1, duration_sec + 1):
        await asyncio.sleep(1.0)
        current_stats = await fetch_api_stats()
        if not current_stats:
            continue

        elapsed = time.time() - start_time
        crawled = current_stats.get("pages_crawled", 0)
        skipped = current_stats.get("pages_skipped", 0)
        updated = current_stats.get("pages_updated", 0)
        q_size = current_stats.get("queue_size", 0)

        # Net changes
        delta_pages = crawled - initial_crawled
        pages_per_min = (delta_pages / elapsed) * 60 if elapsed > 0 else 0.0

        print(f"{int(elapsed):<12}{crawled:<10}{skipped:<15}{updated:<10}{q_size:<12}{pages_per_min:<18.2f}")

    print("-" * 80)


async def main():
    print("=" * 60)
    print("         PYCRAWLER PERFORMANCE BENCHMARK SUITE")
    print("=" * 60)

    # 1. API Status Check
    stats = await fetch_api_stats()
    if not stats:
        print("\n[-] Error: FastAPI server is not running on http://127.0.0.1:8080.")
        print("[*] Please run 'docker-compose up' before starting this script.")
        sys.exit(1)

    # 2. Extract telemetry
    db_metrics = await get_db_metrics()
    
    pages_crawled = stats.get("pages_crawled", 0)
    skipped_304 = stats.get("pages_skipped", 0)
    updated_pages = stats.get("pages_updated", 0)
    failed_count = stats.get("urls_failed", 0)
    visited_total = stats.get("redis_visited_count", 0)
    db_size = stats.get("database_size_mb", 0.0)
    workers = stats.get("active_workers", 0)
    is_running = stats.get("is_running", False)

    # Duplicate URLs skipped is the number of URLs marked visited in Redis
    # minus the ones that actually saved to DB (pages) and ones that returned 304 (skipped)
    dedup_skips = max(0, visited_total - (pages_crawled + skipped_304))

    # Calculate Cache hit ratio
    total_touches = pages_crawled + skipped_304
    cache_hit_ratio = (skipped_304 / total_touches) * 100 if total_touches > 0 else 0.0

    print("\n[+] SYSTEM CONFIGURATION & TELEMETRY:")
    print(f"    - Crawler Active Status   : {is_running}")
    print(f"    - Active Workers (Redis)  : {workers}")
    print(f"    - Database Size           : {db_size} MB")
    print(f"    - Semantic Search Enabled : {stats.get('semantic_available', False)}")

    print("\n[+] ENGINE & CACHE EFFICIENCY:")
    print(f"    - Total Pages Crawled     : {pages_crawled}")
    print(f"    - 304 Cache Hits (Skipped): {skipped_304}")
    print(f"    - Pages Updated (Recrawl) : {updated_pages}")
    print(f"    - Deduplicated Links      : {dedup_skips}")
    print(f"    - 304 Cache Hit Ratio     : {cache_hit_ratio:.1f}%")

    # Status Codes Breakdown
    sc = stats.get("status_codes", {})
    print("\n[+] HTTP RESPONSE STATUS CODES:")
    print(f"    - 200 OK                  : {sc.get('200', 0)}")
    print(f"    - 304 Not Modified        : {sc.get('304', 0)}")
    print(f"    - 301/302 Redirects       : {sc.get('301_302', 0)}")
    print(f"    - 404 Not Found           : {sc.get('404', 0)}")
    print(f"    - 500 Server Error        : {sc.get('500', 0)}")
    print(f"    - Other Errors/Timeouts   : {sc.get('errors', 0)}")

    # Performance Timings
    pm = stats.get("perf_metrics", {})
    print("\n[+] ENGINE PROCESSING SPEED:")
    print(f"    - Avg Page Download Time  : {pm.get('avg_download_time_ms', 0.0)} ms")
    print(f"    - Avg HTML Parse Time     : {pm.get('avg_parse_time_ms', 0.0)} ms")
    print(f"    - Avg DB Indexing Time    : {pm.get('avg_index_time_ms', 0.0)} ms")
    print(f"    - Avg Page Response Size  : {pm.get('avg_response_bytes', 0.0)} bytes")

    print("\n[+] INDEX & GRAPH STATS:")
    print(f"    - Unique Vocabulary Words : {db_metrics['unique_words']}")
    print(f"    - Discovered Link Edges   : {db_metrics['link_edges']}")
    print(f"    - Average Outlinks / Page : {db_metrics['avg_links_per_page']}")

    # 3. Benchmark search latency with 100 queries
    benchmark_queries = [
        "python", "tutorial", "asyncio", "crawler", "framework", "class", "method", "database", "redis", "search",
        "web", "page", "link", "url", "domain", "robots", "politeness", "delay", "queue", "status",
        "api", "route", "router", "endpoint", "request", "response", "json", "xml", "html", "css",
        "javascript", "fastapi", "uvicorn", "pydantic", "settings", "config", "session", "asyncpg", "sqlalchemy", "orm",
        "model", "field", "column", "table", "key", "index", "inverted", "tf", "idf", "tfidf",
        "pagerank", "embedding", "vector", "semantic", "cosine", "similarity", "numpy", "pytorch", "transformers", "huggingface",
        "docker", "compose", "container", "volume", "port", "network", "host", "client", "worker", "thread",
        "task", "concurrency", "rate", "limit", "polite", "conditional", "etag", "modified", "cache", "hit",
        "duplicate", "hash", "sha256", "content", "title", "snippet", "meta", "description", "keyword", "language",
        "heading", "image", "src", "href", "absolute", "relative", "redirect", "error", "exception", "latency"
    ]
    search_metrics = await run_search_benchmark(benchmark_queries)

    print("\n[+] SEARCH PERFORMANCE METRICS:")
    print(f"    - Average Query Latency   : {search_metrics['avg_ms']} ms")
    print(f"    - Median (P50) Latency    : {search_metrics['median_ms']} ms")
    print(f"    - P95 Query Latency       : {search_metrics['p95_ms']} ms")
    print(f"    - P99 Query Latency       : {search_metrics['p99_ms']} ms")
    print(f"    - Minimum Query Latency   : {search_metrics['min_ms']} ms")
    print(f"    - Maximum Query Latency   : {search_metrics['max_ms']} ms")
    print(f"    - Success Rate            : {search_metrics['success_rate']}%")
    print(f"    - Avg Results Per Query   : {search_metrics['avg_results']}")

    # 4. Monitor live crawl speed (if crawler is running)
    if is_running:
        await monitor_crawl(duration_sec=10)
    else:
        print("\n[*] Tip: Start the crawler from the UI dashboard, then run this script")
        print("    again to monitor real-time crawling throughput (pages/min)!")

    print("\n" + "=" * 60)
    print("                    BENCHMARK COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    # Check Python version
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
