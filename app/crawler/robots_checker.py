import aiohttp
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
import logging
from typing import Dict, Tuple
import time

logger = logging.getLogger(__name__)

class RobotsChecker:
    def __init__(self, user_agent: str, http_timeout: int = 10):
        self.user_agent = user_agent
        self.http_timeout = http_timeout
        # Cache structure: domain -> (RobotFileParser, timestamp)
        self.cache: Dict[str, Tuple[RobotFileParser, float]] = {}
        self.cache_ttl = 3600  # 1 hour cache TTL

    def _get_domain_and_robots_url(self, url: str) -> Tuple[str, str]:
        """Extract domain and standard robots.txt URL from a web target."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        scheme = parsed.scheme if parsed.scheme in ("http", "https") else "http"
        robots_url = f"{scheme}://{domain}/robots.txt"
        return domain, robots_url

    async def _fetch_and_cache(self, domain: str, robots_url: str, session: aiohttp.ClientSession) -> RobotFileParser:
        """Fetch robots.txt and parse its rules."""
        parser = RobotFileParser()
        try:
            async with session.get(robots_url, timeout=self.http_timeout) as response:
                if response.status == 200:
                    content = await response.text()
                    parser.parse(content.splitlines())
                else:
                    # Non-200 means no robots.txt configuration, default to allow
                    parser.parse([])
        except Exception as e:
            logger.warning(f"Error fetching robots.txt from {robots_url}: {e}")
            # Default to allow crawl on error
            parser.parse([])

        self.cache[domain] = (parser, time.time())
        return parser

    async def get_rules(self, url: str, session: aiohttp.ClientSession) -> RobotFileParser:
        """Retrieve robots.txt parser from cache or fetch from remote."""
        domain, robots_url = self._get_domain_and_robots_url(url)
        now = time.time()

        if domain in self.cache:
            parser, fetch_time = self.cache[domain]
            if now - fetch_time < self.cache_ttl:
                return parser

        return await self._fetch_and_cache(domain, robots_url, session)

    async def is_allowed(self, url: str, session: aiohttp.ClientSession) -> bool:
        """Check if crawling is allowed for the target URL."""
        parser = await self.get_rules(url, session)
        return parser.can_fetch(self.user_agent, url)

    async def get_crawl_delay(self, url: str, session: aiohttp.ClientSession) -> float:
        """Extract crawl delay configuration for User-Agent if configured."""
        parser = await self.get_rules(url, session)
        delay = parser.crawl_delay(self.user_agent)
        return float(delay) if delay is not None else 0.0
