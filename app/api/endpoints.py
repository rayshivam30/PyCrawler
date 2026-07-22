from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func, text, or_
from pydantic import BaseModel, HttpUrl
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.database.database import get_db
from app.models.models import Website, Page, QueueItem, SearchHistory, Word
from app.queue.queue_manager import QueueManager
from app.queue.redis_queue import redis_queue
from app.crawler.crawler import crawler_manager
from app.ranking.ranker import Ranker
from app.ranking.pagerank import PageRankCalculator
from app.search.embeddings import EmbeddingEngine

router = APIRouter()

# --- Request/Response Models ---
class SeedCreate(BaseModel):
    url: str

class SeedResponse(BaseModel):
    id: int
    domain: str
    robots_checked: bool
    crawl_delay: int

    class Config:
        from_attributes = True

class SearchResult(BaseModel):
    id: int
    url: str
    title: str
    snippet: str
    score: float
    language: Optional[str] = "en"
    crawl_time: Optional[str] = None

# --- Seed Endpoints ---
@router.post("/seed", response_model=Dict[str, Any])
async def add_seed(seed: SeedCreate, db: AsyncSession = Depends(get_db)):
    success, msg = await QueueManager.add_seed_url(db, seed.url)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "success", "message": msg}

@router.get("/seed", response_model=List[SeedResponse])
async def get_seeds(db: AsyncSession = Depends(get_db)):
    stmt = select(Website).order_by(Website.id.desc())
    result = await db.execute(stmt)
    return result.scalars().all()

@router.delete("/seed/{id}", response_model=Dict[str, Any])
async def delete_seed(id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Website).where(Website.id == id)
    site = (await db.execute(stmt)).scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Seed not found")
    
    # Delete associated queue items for this domain
    domain = site.domain
    q_stmt = delete(QueueItem).where(
        or_(
            QueueItem.url.like(f"http://{domain}%"),
            QueueItem.url.like(f"https://{domain}%"),
            QueueItem.url.like(f"http://www.{domain}%"),
            QueueItem.url.like(f"https://www.{domain}%")
        )
    )
    await db.execute(q_stmt)
    
    await db.delete(site)
    await db.commit()
    return {"status": "success", "message": f"Domain {site.domain} and all associated crawled data deleted"}

# --- Crawl Control Endpoints ---
@router.post("/crawl/start")
async def start_crawl():
    await crawler_manager.start()
    return {"status": "success", "message": "Crawler workers started in background"}

@router.post("/crawl/stop")
async def stop_crawl():
    await crawler_manager.stop()
    return {"status": "success", "message": "Crawler workers stopped"}

@router.post("/crawl/recrawl")
async def recrawl_all_pages(db: AsyncSession = Depends(get_db)):
    """
    Incremental recrawl: re-queues all previously indexed pages for freshness checking.
    Workers will send ETag / If-Modified-Since headers and skip pages that return 304.
    Only changed pages (200 response) will be re-downloaded and re-indexed.
    """
    # Fetch all existing page URLs from the database
    stmt = select(Page.url)
    result = await db.execute(stmt)
    urls = result.scalars().all()

    if not urls:
        return {"status": "info", "message": "No pages indexed yet. Run a normal crawl first."}

    # Clear only the visited set so workers can re-visit existing pages
    # The task queue is left intact to avoid dropping any pending URLs
    await redis_queue.flush_visited()

    # Push all existing pages back into the Redis queue at depth=0 with medium priority
    queued = 0
    for url in urls:
        await redis_queue.mark_visited(url)  # pre-mark so new links from pages don't re-add them
        await redis_queue.push(url, depth=0, priority=5)
        queued += 1

    # Start crawler if not already running
    if not crawler_manager.is_running:
        await crawler_manager.start()

    return {
        "status": "success",
        "message": f"Queued {queued} pages for incremental recrawl",
        "pages_queued": queued,
    }

@router.get("/crawl/status")
async def get_crawl_status():
    return {
        "is_running": crawler_manager.is_running,
        "stats": crawler_manager.stats
    }

# --- PageRank Endpoint ---
@router.post("/pagerank/compute")
async def compute_pagerank(db: AsyncSession = Depends(get_db)):
    """
    Runs the iterative PageRank algorithm over all crawled pages and
    their discovered link graph. Updates Page.pagerank scores in the DB.
    Call this after a significant crawl batch for best results.
    """
    result = await PageRankCalculator.compute(db)
    return {
        "status": "success",
        "message": f"PageRank computed for {result['pages_computed']} pages in {result['iterations']} iterations.",
        **result
    }

# --- Search Endpoints ---
@router.get("/search")
async def search(q: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db)):
    # Record query in history
    history = SearchHistory(query=q)
    db.add(history)
    await db.commit()
    
    results = await Ranker.search(db, q)
    return {
        "query": q,
        "results_count": len(results),
        "results": results
    }

@router.get("/suggest")
async def suggest(q: str = Query(..., min_length=1), db: AsyncSession = Depends(get_db)):
    q_clean = q.strip().lower()
    
    # 1. Fetch matching words from search index
    word_stmt = select(Word.word).where(Word.word.like(f"{q_clean}%")).limit(5)
    word_res = await db.execute(word_stmt)
    words = word_res.scalars().all()

    # 2. Fetch matching queries from history
    hist_stmt = select(SearchHistory.query).distinct().where(SearchHistory.query.like(f"{q_clean}%")).limit(5)
    hist_res = await db.execute(hist_stmt)
    histories = hist_res.scalars().all()

    # Combine recommendations and remove duplicates
    suggestions = list(set(words + histories))[:8]
    return {
        "query": q,
        "suggestions": suggestions
    }

@router.get("/page/{id}")
async def get_page(id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Page).where(Page.id == id)
    page = (await db.execute(stmt)).scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return {
        "id": page.id,
        "url": page.url,
        "title": page.title,
        "content": page.content,
        "html": page.html,
        "status_code": page.status_code,
        "crawl_time": page.crawl_time
    }

# --- Dashboard Stats Endpoints ---
@router.get("/stats")
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    # Total pages crawled
    pages_count_stmt = select(func.count(Page.id))
    pages_crawled = (await db.execute(pages_count_stmt)).scalar() or 0

    # Queue counts
    pending_stmt = select(func.count(QueueItem.id)).where(QueueItem.status == "pending")
    pending_count = (await db.execute(pending_stmt)).scalar() or 0

    failed_stmt = select(func.count(QueueItem.id)).where(QueueItem.status == "failed")
    failed_count = (await db.execute(failed_stmt)).scalar() or 0

    completed_stmt = select(func.count(QueueItem.id)).where(QueueItem.status == "completed")
    completed_count = (await db.execute(completed_stmt)).scalar() or 0

    total_queue_stmt = select(func.count(QueueItem.id))
    queue_size = (await db.execute(total_queue_stmt)).scalar() or 0

    processed = completed_count + failed_count
    progress_percentage = 0.0
    if queue_size > 0:
        progress_percentage = round((processed / queue_size) * 100, 1)

    # Top domains by pages count
    top_domains_stmt = (
        select(Website.domain, func.count(Page.id))
        .join(Page, Page.website_id == Website.id)
        .group_by(Website.domain)
        .order_by(func.count(Page.id).desc())
        .limit(5)
    )
    top_domains_res = await db.execute(top_domains_stmt)
    top_domains = [{"domain": row[0], "count": row[1]} for row in top_domains_res.all()]

    # Database Size Query (Postgres specific)
    db_size_bytes = 0
    try:
        size_res = await db.execute(text("SELECT pg_database_size(current_database())"))
        db_size_bytes = size_res.scalar() or 0
    except Exception:
        db_size_bytes = 0

    db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

    # Search query stats
    total_searches_stmt = select(func.count(SearchHistory.id))
    total_searches = (await db.execute(total_searches_stmt)).scalar() or 0

    top_queries_stmt = (
        select(SearchHistory.query, func.count(SearchHistory.id))
        .group_by(SearchHistory.query)
        .order_by(func.count(SearchHistory.id).desc())
        .limit(5)
    )
    top_queries_res = await db.execute(top_queries_stmt)
    top_queries = [{"query": row[0], "count": row[1]} for row in top_queries_res.all()]

    # Redis live stats
    redis_queue_size = 0
    redis_visited_count = 0
    redis_workers = 0
    try:
        redis_queue_size = await redis_queue.queue_size()
        redis_visited_count = await redis_queue.visited_count()
        redis_workers = await redis_queue.active_worker_count()
    except Exception:
        pass  # Redis may not be available during startup

    # Calculate average performance times safely
    d_count = max(1, crawler_manager.stats.get("download_count", 0))
    p_count = max(1, crawler_manager.stats.get("parse_count", 0))
    i_count = max(1, crawler_manager.stats.get("index_count", 0))

    return {
        "pages_crawled": pages_crawled,
        "pages_skipped": crawler_manager.stats.get("pages_skipped", 0),
        "pages_updated": crawler_manager.stats.get("pages_updated", 0),
        "urls_pending": pending_count,
        "urls_failed": failed_count,
        "queue_size": redis_queue_size,
        "redis_queue_size": redis_queue_size,
        "redis_visited_count": redis_visited_count,
        "progress_percentage": progress_percentage,
        "top_domains": top_domains,
        "database_size_mb": db_size_mb,
        "total_searches": total_searches,
        "top_queries": top_queries,
        "active_workers": redis_workers if redis_workers > 0 else crawler_manager.stats["active_workers"],
        "is_running": crawler_manager.is_running,
        "semantic_available": EmbeddingEngine._available,   # True, False, or None (loading)
        # Performance timing benchmarks (MS)
        "perf_metrics": {
            "avg_download_time_ms": round((crawler_manager.stats.get("total_download_time", 0.0) / d_count) * 1000, 2),
            "avg_parse_time_ms": round((crawler_manager.stats.get("total_parse_time", 0.0) / p_count) * 1000, 2),
            "avg_index_time_ms": round((crawler_manager.stats.get("total_index_time", 0.0) / i_count) * 1000, 2),
            "avg_response_bytes": round(crawler_manager.stats.get("total_response_bytes", 0) / d_count, 1),
        },
        # Status code metrics
        "status_codes": {
            "200": crawler_manager.stats.get("status_200", 0),
            "304": crawler_manager.stats.get("status_304", 0),
            "301_302": crawler_manager.stats.get("status_301_302", 0),
            "404": crawler_manager.stats.get("status_404", 0),
            "500": crawler_manager.stats.get("status_500", 0),
            "errors": crawler_manager.stats.get("status_other_error", 0),
        }
    }
