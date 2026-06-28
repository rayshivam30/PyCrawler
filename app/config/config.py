import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Database Settings
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="pycrawler")

    @property
    def DATABASE_URL(self) -> str:
        # Returns asyncpg connection string
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Crawler Settings
    CONCURRENT_REQUESTS: int = Field(default=100, description="Max concurrent crawler connections")
    MAX_DEPTH: int = Field(default=3, description="Max crawling depth from seed URLs")
    HTTP_TIMEOUT: int = Field(default=10, description="Network timeout in seconds")
    USER_AGENT: str = Field(default="PyCrawler/1.0 (+http://localhost:8000)", description="User-Agent header")
    DEFAULT_CRAWL_DELAY: int = Field(default=1, description="Politeness delay in seconds when not specified by robots.txt")
    MAX_RETRIES: int = Field(default=3, description="Maximum retries for failed URLs")
    
    # API Settings
    API_PORT: int = Field(default=8000)
    API_HOST: str = Field(default="0.0.0.0")

    # Redis Settings
    REDIS_URL: str = Field(default="redis://localhost:6379", description="Redis connection URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
