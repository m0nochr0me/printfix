"""
Config Maker
"""

# pyright: basic

__all__ = ("settings",)

from typing import Literal

from pydantic_settings import BaseSettings

from app import __project__, __version__


class Settings(BaseSettings):
    PROJECT_NAME: str = __project__
    PROJECT_VERSION: str = __version__
    API_VERSION: int = 1
    DEBUG: bool = False
    LOG_MESSAGE_MAX_LEN: int = 2000

    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 4201
    APP_WORKERS: int = 1
    APP_AUTH_KEY: str
    TRANSPORT: Literal["stdio", "http", "sse", "streamable-http"] = "streamable-http"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB_CACHE: int = 4
    REDIS_DB_BROKER: int = 0
    REDIS_DB_RESULTS: int = 1
    REDIS_DB_JOBS: int = 2
    REDIS_PASSWORD: str | None = None
    CACHE_TTL: int = 3600
    CACHE_TTL_LONG: int = 7 * 24 * 3600  # 7 days

    VECTOR_STORE_PATH: str = ".lancedb"
    STORAGE_DIR: str = "./data"
    MAX_UPLOAD_SIZE_MB: int = 100
    ALLOWED_EXTENSIONS: list[str] = [
        ".pdf", ".docx", ".xlsx", ".pptx",
        ".odt", ".ods", ".odp",
        ".jpg", ".jpeg", ".png", ".tiff",
    ]

    WORKER_CONCURRENCY: int = 2
    JOB_TTL_SECONDS: int = 86400  # 24 hours

    GOOGLE_API_KEY: str


    class Config:
        env_file = ".env"
        env_prefix = "PFX_"
        env_file_encoding = "utf-8"
        extra = "ignore"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
