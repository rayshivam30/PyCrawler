import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # --- Direct URL override (Render / cloud providers inject these) ---
    # When DATABASE_URL is set as an env var, it takes priority over the
    # individual POSTGRES_* fields below. Render's Postgres add-on sets
    # this automatically; just add it to your service's Environment variables.
    DATABASE_URL_OVERRIDE: Optional[str] = Field(default=None, alias="DATABASE_URL")

    # --- Individual Postgres fields (used for local Docker Compose) ---
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="pycrawler")

    @property
    def DATABASE_URL(self) -> str:
        """
        Returns the asyncpg connection string.
        Priority:
          1. DATABASE_URL env var (Render Postgres add-on / any cloud provider)
          2. Built from individual POSTGRES_* fields (local Docker Compose / dev)
        """
        if self.DATABASE_URL_OVERRIDE:
            raw = self.DATABASE_URL_OVERRIDE
            # Render provides postgresql:// — asyncpg needs postgresql+asyncpg://
            if raw.startswith("postgresql://") or raw.startswith("postgres://"):
                raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
                raw = raw.replace("postgres://", "postgresql+asyncpg://", 1)
            return raw
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # --- Crawler Settings ---
    CONCURRENT_REQUESTS: int = Field(default=100, description="Max concurrent crawler connections")
    MAX_DEPTH: int = Field(default=3, description="Max crawling depth from seed URLs")
    HTTP_TIMEOUT: int = Field(default=10, description="Network timeout in seconds")
    USER_AGENT: str = Field(default="PyCrawler/1.0 (+http://localhost:8000)", description="User-Agent header")
    DEFAULT_CRAWL_DELAY: int = Field(default=1, description="Politeness delay in seconds when not specified by robots.txt")
    MAX_RETRIES: int = Field(default=3, description="Maximum retries for failed URLs")

    # --- API Settings ---
    API_PORT: int = Field(default=8000)
    API_HOST: str = Field(default="0.0.0.0")

    # --- Redis Settings ---
    # Render Redis add-on injects REDIS_URL automatically.
    REDIS_URL: str = Field(default="redis://localhost:6379", description="Redis connection URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,   # allow both alias and field name
    )


settings = Settings()
