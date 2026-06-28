"""
CrawlerManager — Async distributed web crawler using Redis as the task queue.

Architecture:
  - Workers call redis_queue.pop() to get the next URL (BRPOP — blocking pop).
  - On success, child links are pushed back to Redis via QueueManager.add_to_queue().
  - Results (pages, inverted index) are written to PostgreSQL.
  - Each worker pings a Redis heartbeat key so the dashboard can count live workers.

Incremental Crawling (HTTP Conditional Requests):
  - If a URL was crawled before, its ETag / Last-Modified is stored in the Page record.
  - On re-crawl, those values are sent as If-None-Match / If-Modified-Since headers.
  - A 304 Not Modified response means the page hasn't changed — we skip re-indexing
    and only update the `last_checked` timestamp.
  - A 200 response means the page changed — we update all fields and re-index.

This design supports true horizontal scaling: run multiple containers or processes
and they all share the same Redis queue without any coordination logic.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin
from typing import Dict, Any, Optional

import aiohttp
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, exists

from app.config.config import settings
from app.database.database import SessionLocal
from app.models.models import Page, Website, Link
from app.queue.queue_manager import QueueManager
from app.queue.redis_queue import redis_queue
from app.crawler.robots_checker import RobotsChecker
from app.parser.parser import HTMLParser
from app.indexing.indexer import Indexer

logger = logging.getLogger(__name__)


class CrawlerManager:
    def __init__(self) -> None:
        self.is_running: bool = False
        self.robots_checker = RobotsChecker(
            user_agent=settings.USER_AGENT,
            http_timeout=settings.HTTP_TIMEOUT
        )
        self.last_crawl_time: Dict[str, float] = {}  # domain -> timestamp
        self.worker_tasks = []
        self.stats = {
            "pages_crawled": 0,
            "pages_skipped": 0,   # pages unchanged (304 Not Modified)
            "pages_updated": 0,   # pages re-crawled due to change
            "failed_crawled": 0,
            "active_workers": 0,
            "start_time": None,
            # Status code tracking
            "status_200": 0,
            "status_304": 0,
            "status_301_302": 0,
            "status_404": 0,
            "status_500": 0,
            "status_other_error": 0,
            # Performance timers
            "total_download_time": 0.0,
            "download_count": 0,
            "total_parse_time": 0.0,
            "parse_count": 0,
            "total_index_time": 0.0,
            "index_count": 0,
            "total_response_bytes": 0,
        }

    async def start(self) -> None:
        """Start the crawler: flush stale Redis state and launch async worker pool."""
        if self.is_running:
            return

        self.is_running = True
        self.stats["start_time"] = time.time()
        self.stats["pages_crawled"] = 0
        self.stats["failed_crawled"] = 0

        # Keep the existing queued seeds in Redis so they can be crawled

        # Re-seed Redis from PostgreSQL Website records so seeds survive restarts
        async with SessionLocal() as db:
            await QueueManager.reset_crawling_tasks(db)

        # Launch independent async workers
        self.worker_tasks = [
            asyncio.create_task(self._worker_loop(i))
            for i in range(settings.CONCURRENT_REQUESTS)
        ]
        logger.info(f"Started {settings.CONCURRENT_REQUESTS} Redis-backed crawler workers.")

    async def stop(self) -> None:
        """Gracefully cancel all worker tasks."""
        if not self.is_running:
            return
        self.is_running = False

        for task in self.worker_tasks:
            task.cancel()

        await asyncio.gather(*self.worker_tasks, return_exceptions=True)
        self.worker_tasks = []
        logger.info("All crawler workers stopped.")

    async def _worker_loop(self, worker_id: int) -> None:
        """
        Main loop for a single crawler worker.

        Continuously pops URLs from the Redis queue, crawls them,
        and pushes discovered child links back into Redis.
        """
        self.stats["active_workers"] += 1
        try:
            timeout = aiohttp.ClientTimeout(total=settings.HTTP_TIMEOUT)

            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": settings.USER_AGENT}
            ) as session:
                while self.is_running:
                    # Heartbeat: keep worker key alive in Redis
                    await redis_queue.ping_worker(worker_id)

                    # Pop next task from Redis (blocking with timeout)
                    task = await redis_queue.pop(timeout=2.0)

                    if task is None:
                        # Queue empty — wait briefly before retrying
                        await asyncio.sleep(0.5)
                        continue

                    url: str = task["url"]
                    depth: int = task.get("depth", 0)
                    priority: int = task.get("priority", 0)

                    try:
                        async with SessionLocal() as db:
                            await self._crawl_url(db, session, url, depth, priority)
                    except asyncio.CancelledError:
                        # Re-queue the URL so another worker can pick it up
                        await redis_queue.push(url, depth=depth, priority=priority)
                        raise
                    except Exception as e:
                        logger.error(f"Worker {worker_id} failed on {url}: {e}")
                        self.stats["failed_crawled"] += 1

                    await asyncio.sleep(0.05)
        finally:
            self.stats["active_workers"] -= 1
            # Clean up the worker heartbeat key in Redis immediately on exit
            try:
                await redis_queue.remove_worker(worker_id)
            except Exception:
                pass

    async def _crawl_url(
        self,
        db: AsyncSession,
        session: aiohttp.ClientSession,
        url: str,
        depth: int,
        priority: int,
    ) -> None:
        """
        Fetch, parse, and index a single URL.

        Incremental crawl flow:
          1. Look up existing Page record for this URL.
          2. If found, attach ETag / Last-Modified as conditional request headers.
          3. 304 Not Modified → page unchanged; only update last_checked, skip re-index.
          4. 200 OK → update or create page record, re-index content.
        """

        # ── Depth gate ────────────────────────────────────────────────────
        if depth > settings.MAX_DEPTH:
            logger.debug(f"Depth limit reached, skipping: {url}")
            return

        # ── Robots.txt compliance ─────────────────────────────────────────
        if not await self.robots_checker.is_allowed(url, session):
            logger.info(f"Blocked by robots.txt: {url}")
            return

        # ── Politeness delay per domain ───────────────────────────────────
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()

        delay = await self.robots_checker.get_crawl_delay(url, session)
        if delay == 0.0:
            delay = float(settings.DEFAULT_CRAWL_DELAY)

        elapsed = time.time() - self.last_crawl_time.get(domain, 0.0)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)

        self.last_crawl_time[domain] = time.time()

        # ── Look up existing Page record for conditional headers ───────────
        existing_page: Optional[Page] = (
            await db.execute(select(Page).where(Page.url == url))
        ).scalar_one_or_none()

        # Build conditional request headers (incremental crawl magic)
        conditional_headers: Dict[str, str] = {}
        if existing_page:
            if existing_page.etag:
                conditional_headers["If-None-Match"] = existing_page.etag
            if existing_page.last_modified:
                conditional_headers["If-Modified-Since"] = existing_page.last_modified

        # ── HTTP GET with conditional headers ─────────────────────────────
        t_download_start = time.perf_counter()
        try:
            async with session.get(
                url,
                allow_redirects=False,
                headers=conditional_headers,
            ) as response:
                status = response.status

                # ── 304 Not Modified — page hasn't changed ────────────────
                if status == 304:
                    self.stats["status_304"] += 1
                    if existing_page:
                        existing_page.last_checked = datetime.now(timezone.utc)
                        await db.commit()
                    self.stats["pages_skipped"] += 1
                    logger.info(f"Unchanged (304 Not Modified): {url}")
                    return

                # ── Redirects — push target into queue ────────────────────
                if status in (301, 302):
                    self.stats["status_301_302"] += 1
                    location = response.headers.get("Location")
                    if location:
                        redirect_url = urljoin(url, location)
                        await QueueManager.add_to_queue(
                            db, redirect_url, depth=depth, priority=priority
                        )
                        logger.info(f"Redirect {status}: {url} -> {redirect_url}")
                    return

                if status != 200:
                    if status == 404:
                        self.stats["status_404"] += 1
                    elif status >= 500:
                        self.stats["status_500"] += 1
                    else:
                        self.stats["status_other_error"] += 1
                    logger.warning(f"HTTP {status} for {url}")
                    self.stats["failed_crawled"] += 1
                    return

                self.stats["status_200"] += 1
                html_content = await response.text()
                
                # Record download metrics
                self.stats["total_download_time"] += (time.perf_counter() - t_download_start)
                self.stats["download_count"] += 1
                self.stats["total_response_bytes"] += len(html_content)

                # Capture freshness headers for next incremental check
                new_etag = response.headers.get("ETag")
                new_last_modified = response.headers.get("Last-Modified")

        except Exception as e:
            self.stats["status_other_error"] += 1
            logger.error(f"Network error fetching {url}: {e}")
            self.stats["failed_crawled"] += 1
            return

        # ── Parse HTML ────────────────────────────────────────────────────
        t_parse_start = time.perf_counter()
        parsed_data = HTMLParser.parse(html_content, url)
        self.stats["total_parse_time"] += (time.perf_counter() - t_parse_start)
        self.stats["parse_count"] += 1
        page_hash = parsed_data["page_hash"]

        # ── Resolve or create Website record ──────────────────────────────
        site_stmt = select(Website).where(Website.domain == domain)
        site = (await db.execute(site_stmt)).scalar_one_or_none()
        if not site:
            site = Website(domain=domain, robots_checked=False, crawl_delay=0)
            db.add(site)
            await db.flush()

        # ── Update existing page OR insert new page ───────────────────────
        now = datetime.now(timezone.utc)
        if existing_page:
            # Page content changed (200 on a known URL) — update in place
            existing_page.title = parsed_data["title"]
            existing_page.content = parsed_data["content"]
            existing_page.html = html_content
            existing_page.status_code = status
            existing_page.language = parsed_data["language"]
            existing_page.page_hash = page_hash
            existing_page.etag = new_etag
            existing_page.last_modified = new_last_modified
            existing_page.last_checked = now
            await db.flush()
            page_id = existing_page.id
            self.stats["pages_updated"] += 1
            logger.info(f"Page updated (content changed): {url}")
        else:
            # Content deduplication — skip if another page has the same hash
            hash_stmt = select(exists().where(Page.page_hash == page_hash))
            if (await db.execute(hash_stmt)).scalar():
                logger.debug(f"Duplicate content hash, skipping: {url}")
                return

            new_page = Page(
                url=url,
                title=parsed_data["title"],
                content=parsed_data["content"],
                html=html_content,
                status_code=status,
                language=parsed_data["language"],
                page_hash=page_hash,
                website_id=site.id,
                etag=new_etag,
                last_modified=new_last_modified,
                last_checked=now,
            )
            db.add(new_page)
            await db.flush()
            page_id = new_page.id
            self.stats["pages_crawled"] += 1
            logger.info(f"Crawled & indexed: {url}")

        # ── Index (or re-index) content keywords ──────────────────────────
        t_index_start = time.perf_counter()
        await Indexer.index_page(db, page_id, parsed_data["content"])
        self.stats["total_index_time"] += (time.perf_counter() - t_index_start)
        self.stats["index_count"] += 1

        # ── Build link graph for PageRank ─────────────────────────────────
        # One batch query: find which extracted links are already in the DB
        all_link_urls = parsed_data["links"]
        if all_link_urls:
            batch = all_link_urls[:200]  # cap to keep query fast
            known_stmt = select(Page.id, Page.url).where(Page.url.in_(batch))
            known_rows = (await db.execute(known_stmt)).all()
            known_url_to_id = {row.url: row.id for row in known_rows}

            for link_url, dest_id in known_url_to_id.items():
                if dest_id != page_id:  # skip self-links
                    try:
                        db.add(Link(source_page=page_id, destination_page=dest_id))
                        await db.flush()
                    except Exception:
                        await db.rollback()  # ignore UniqueConstraint violations
                        # Re-open the session state for the commit below
                        await db.begin()

        # ── Enqueue child links into Redis ────────────────────────────────
        next_depth = depth + 1
        if next_depth <= settings.MAX_DEPTH:
            for link_url in all_link_urls:
                link_domain = urlparse(link_url).netloc.lower()
                if link_domain:
                    ls = (await db.execute(
                        select(Website).where(Website.domain == link_domain)
                    )).scalar_one_or_none()
                    if not ls:
                        ls = Website(domain=link_domain, robots_checked=False, crawl_delay=0)
                        db.add(ls)
                        await db.flush()

                    await QueueManager.add_to_queue(
                        db,
                        url=link_url,
                        depth=next_depth,
                        priority=max(0, priority - 1),
                    )

        await db.commit()


# Singleton shared across the application
crawler_manager = CrawlerManager()

