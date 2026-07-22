"""
QueueManager — Seed URL management and Redis-backed URL scheduling.

The hot crawl path now runs entirely through Redis:
  - add_to_queue()    → Redis PUSH + SET (O(1) dedup)
  - add_seed_url()    → creates Website DB record, then pushes to Redis

PostgreSQL queue table is kept for stats/history display but is no longer
on the critical path of the crawler.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from urllib.parse import urlparse
import logging
from typing import Optional, Tuple
from app.models.models import QueueItem, Page, Website
from app.queue.redis_queue import redis_queue

logger = logging.getLogger(__name__)


class QueueManager:

    @staticmethod
    async def add_to_queue(
        db: AsyncSession,
        url: str,
        depth: int = 0,
        priority: int = 0
    ) -> bool:
        """
        Push a URL to the Redis distributed queue if not already visited.

        Deduplication is handled by the Redis visited SET (O(1) lookup),
        avoiding expensive DB queries on the hot crawl path.
        Falls back gracefully if Redis is unavailable.
        """
        url = url.strip()
        if not url:
            return False

        try:
            # Fast O(1) dedup check via Redis SET
            if await redis_queue.is_visited(url):
                return False
            # Mark as visited before pushing to prevent race-condition duplicates
            await redis_queue.mark_visited(url)
            await redis_queue.push(url, depth=depth, priority=priority)
        except Exception as e:
            logger.warning(f"Redis unavailable during queue push ({e}); falling back to DB-only queue.")
            # Fall through — seed is still recorded in the DB below

        return True

    @staticmethod
    async def add_seed_url(db: AsyncSession, url: str) -> Tuple[bool, str]:
        """
        Register a seed URL: create/verify the Website DB record,
        then push the URL to the Redis queue.
        """
        url = url.strip()
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "Invalid URL structure"

        domain = parsed.netloc.lower()

        # Upsert Website record in PostgreSQL
        stmt = select(Website).where(Website.domain == domain)
        result = await db.execute(stmt)
        site = result.scalar_one_or_none()

        if not site:
            site = Website(domain=domain, robots_checked=False, crawl_delay=0)
            db.add(site)
            await db.commit()
            await db.refresh(site)

        # Also persist a QueueItem so the crawler can pick it up from DB
        # if Redis was unavailable during the push above.
        existing_q = await db.execute(
            select(QueueItem).where(QueueItem.url == url).limit(1)
        )
        if not existing_q.scalar_one_or_none():
            db.add(QueueItem(url=url, depth=0, priority=10, status="pending"))
            await db.commit()

        # Push to Redis queue (depth=0, seed priority=10)
        added = await QueueManager.add_to_queue(db, url, depth=0, priority=10)
        if added:
            logger.info(f"Seed URL queued: {url}")
            return True, "Seed URL added successfully"
        return False, "URL already exists in queue or has been crawled"

    @staticmethod
    async def sync_pending_to_redis(db: AsyncSession) -> int:
        """
        Fetch all 'pending' QueueItems from DB and push them to Redis.
        Ensures seeds are populated in Redis even after restart or if Redis was temporarily offline.
        """
        stmt = select(QueueItem).where(QueueItem.status == "pending")
        items = (await db.execute(stmt)).scalars().all()
        pushed = 0
        for item in items:
            try:
                if not await redis_queue.is_visited(item.url):
                    await redis_queue.push(item.url, depth=item.depth, priority=item.priority)
                    pushed += 1
            except Exception as e:
                logger.warning(f"Failed to sync {item.url} to Redis: {e}")
        return pushed

    @staticmethod
    async def pop_db_task(db: AsyncSession) -> Optional[Dict[str, Any]]:
        """
        Fallback task retriever when Redis is unavailable or closed.
        Picks the oldest 'pending' QueueItem from PostgreSQL, marks it as 'crawling',
        and returns {url, depth, priority}.
        """
        try:
            stmt = (
                select(QueueItem)
                .where(QueueItem.status == "pending")
                .order_by(QueueItem.priority.desc(), QueueItem.id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            item = (await db.execute(stmt)).scalar_one_or_none()
            if item:
                item.status = "crawling"
                await db.commit()
                return {"url": item.url, "depth": item.depth, "priority": item.priority}
        except Exception as e:
            logger.debug(f"DB queue pop fallback error: {e}")
        return None

    @staticmethod
    async def reset_crawling_tasks(db: AsyncSession) -> None:
        """
        Resets any stuck 'crawling' status in the PostgreSQL queue table.
        Kept for backward compatibility with any stats queries.
        """
        stmt = (
            update(QueueItem)
            .where(QueueItem.status == "crawling")
            .values(status="pending")
        )
        await db.execute(stmt)
        await db.commit()
