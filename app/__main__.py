"""
App Entry Point
"""

import uvicorn

from app.core.config import settings
from app.core.log import uvicorn_log_config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        workers=settings.APP_WORKERS,
        log_config=uvicorn_log_config,
        reload=False,
        forwarded_allow_ips="*",
    )
