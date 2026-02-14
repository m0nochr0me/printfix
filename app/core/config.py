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
    APP_PORT: int = 8083
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

    STORAGE_DIR: str = "./data"
    MAX_UPLOAD_SIZE_MB: int = 100
    ALLOWED_EXTENSIONS: list[str] = [
        ".pdf",
        ".docx",
        ".xlsx",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
        ".jpg",
        ".jpeg",
        ".png",
        ".tiff",
    ]

    WORKER_CONCURRENCY: int = 2
    JOB_TTL_SECONDS: int = 86400  # 24 hours

    GOOGLE_API_KEY: str

    # Anthropic (optional - only used if USE_ANTHROPIC_AI is True)
    USE_ANTHROPIC_AI: bool = False
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_DIAGNOSIS_MODEL: str = "claude-sonnet-4-5-20250929"

    # Diagnosis thresholds
    DIAGNOSIS_MIN_MARGIN_INCHES: float = 0.5
    DIAGNOSIS_MIN_FONT_PT: float = 8.0
    DIAGNOSIS_MIN_IMAGE_DPI: int = 150
    DIAGNOSIS_MAX_INDENT_INCHES: float = 1.0

    # Timeouts & retries
    AI_API_TIMEOUT_SECONDS: int = 60
    AI_API_MAX_RETRIES: int = 3
    LIBREOFFICE_TIMEOUT_SECONDS: int = 120
    FIX_EXECUTION_TIMEOUT_SECONDS: int = 30

    # Rate limiting
    RATE_LIMIT_REQUESTS: int = 60
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    REDIS_DB_RATE_LIMIT: int = 5

    # Integrity & repair
    ENABLE_UPLOAD_VALIDATION: bool = True
    ENABLE_REPAIR_ON_INGEST: bool = True
    ENABLE_POST_FIX_VALIDATION: bool = True
    LIBREOFFICE_REPAIR_TIMEOUT_SECONDS: int = 180

    class Config:
        env_file = ".env"
        env_prefix = "PFX_"
        env_file_encoding = "utf-8"
        extra = "ignore"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
