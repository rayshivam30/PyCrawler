"""
RedisQueue — Distributed URL task queue and deduplication store.

Uses three Redis data structures:
  - LIST  pycrawler:queue     → pending URL tasks (LPUSH / BRPOP)
  - SET   pycrawler:visited   → seen URL deduplication (SADD / SISMEMBER)
  - STRING pycrawler:worker:{id} → worker heartbeat keys with TTL

This class is a singleton shared across all crawler workers within a process.
Multiple separate processes (or containers) can share the same Redis instance,
which is what enables true horizontal scaling.
"""

import json
import ssl
import logging
from typing import Optional, Dict, Any
import redis.asyncio as aioredis
from app.config.config import settings

logger = logging.getLogger(__name__)

QUEUE_KEY = "pycrawler:queue"
VISITED_KEY = "pycrawler:visited"
WORKER_KEY_PREFIX = "pycrawler:worker:"
WORKER_HEARTBEAT_TTL = 15  # seconds before a worker is considered dead


class RedisQueue:
    """
    Async Redis-backed distributed URL queue.

    Designed to be used as a singleton (`redis_queue` module-level instance).
    Call `connect()` on application startup and `disconnect()` on shutdown.
    """

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Initialize the async Redis connection pool."""
        url = settings.REDIS_URL
        # Upstash requires TLS (rediss://). Auto-convert if entered with single 's' (redis://).
        if "upstash.io" in url and url.startswith("redis://"):
            url = url.replace("redis://", "rediss://", 1)

        kwargs: dict = {
            "encoding": "utf-8",
            "decode_responses": True,
        }
        # rediss:// (TLS) — used by Upstash and other cloud Redis providers.
        if url.startswith("rediss://"):
            kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
            kwargs["ssl_check_hostname"] = False

        self._client = await aioredis.from_url(url, **kwargs)
        await self._client.ping()
        logger.info(f"Connected to Redis at {url}")

    async def disconnect(self) -> None:
        """Close the Redis connection pool gracefully."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("Redis connection closed.")

    # ── URL Queue Operations ───────────────────────────────────────────────

    async def push(self, url: str, depth: int = 0, priority: int = 0) -> None:
        """
        Push a URL task to the left of the queue list.
        Higher-priority items are pushed with LPUSH so they are popped first.
        Lower-priority items are pushed with RPUSH (appended to the end).
        """
        task = json.dumps({"url": url, "depth": depth, "priority": priority})
        if priority > 0:
            await self._client.lpush(QUEUE_KEY, task)
        else:
            await self._client.rpush(QUEUE_KEY, task)

    async def pop(self, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """
        Blocking pop from the right end of the queue (BRPOP).
        Returns a parsed task dict {url, depth, priority} or None on timeout.
        """
        result = await self._client.brpop(QUEUE_KEY, timeout=timeout)
        if result is None:
            return None
        _, raw = result  # brpop returns (key, value) tuple
        return json.loads(raw)

    async def queue_size(self) -> int:
        """Return the number of pending tasks in the queue."""
        return await self._client.llen(QUEUE_KEY)

    # ── Visited URL Deduplication ──────────────────────────────────────────

    async def is_visited(self, url: str) -> bool:
        """O(1) check: has this URL already been seen?"""
        return bool(await self._client.sismember(VISITED_KEY, url))

    async def mark_visited(self, url: str) -> None:
        """Add URL to the visited set."""
        await self._client.sadd(VISITED_KEY, url)

    async def visited_count(self) -> int:
        """Return the total number of distinct URLs seen."""
        return await self._client.scard(VISITED_KEY)

    # ── Worker Heartbeat ───────────────────────────────────────────────────

    async def ping_worker(self, worker_id: int) -> None:
        """
        Write/refresh a heartbeat key for this worker with a short TTL.
        If a worker crashes, its key expires and it disappears from active count.
        """
        key = f"{WORKER_KEY_PREFIX}{worker_id}"
        await self._client.set(key, "alive", ex=WORKER_HEARTBEAT_TTL)

    async def remove_worker(self, worker_id: int) -> None:
        """Remove a worker's heartbeat key immediately when it stops."""
        key = f"{WORKER_KEY_PREFIX}{worker_id}"
        await self._client.delete(key)

    async def active_worker_count(self) -> int:
        """Count live workers by scanning heartbeat keys."""
        keys = await self._client.keys(f"{WORKER_KEY_PREFIX}*")
        return len(keys)

    # ── Queue Management ───────────────────────────────────────────────────

    async def flush(self) -> None:
        """
        Clear the queue and visited set.
        Called when starting a fresh crawl to avoid stale state.
        """
        await self._client.delete(QUEUE_KEY, VISITED_KEY)
        logger.info("Redis queue and visited set flushed.")

    async def flush_visited(self) -> None:
        """
        Clear only the visited set, keeping the task queue intact.
        Used by the recrawl endpoint to allow re-checking of existing pages
        without discarding any newly discovered pending URLs.
        """
        await self._client.delete(VISITED_KEY)
        logger.info("Redis visited set flushed for incremental recrawl.")


# Singleton instance shared across the application
redis_queue = RedisQueue()
