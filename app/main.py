"""
Entry Point
"""

import asyncio
import secrets
from contextlib import asynccontextmanager
from time import perf_counter

from asgi_correlation_id import CorrelationIdMiddleware
from fastapi import FastAPI, Request, Response
from fastmcp import FastMCP
from fastmcp.server.auth.providers.debug import DebugTokenVerifier
from ulid import ULID

from app.api.printfix import router as printfix_router
from app.context.printfix import server as printfix_server
from app.core.config import settings
from app.core.log import logger
from app.schema.status import HealthCheckResponse, IndexResponse
from app.worker.broker import broker as taskiq_broker
from app.worker.job_state import JobStateManager

exec_id = ULID()
start_time = perf_counter()

verifier = DebugTokenVerifier(
    validate=lambda token: secrets.compare_digest(token, settings.APP_AUTH_KEY),
    client_id="mcp-client",
    scopes=["read", "write"],
)

mcp_server = FastMCP(
    "Printfix",
    version=settings.PROJECT_VERSION,
    auth=verifier,
)


@mcp_server.resource("resource://health_check")
async def get_health() -> str:
    """Provides platform information"""
    health_check_response = HealthCheckResponse(
        status="OK",
        version=settings.PROJECT_VERSION,
        uptime=perf_counter() - start_time,
        exec_id=exec_id,
    )
    return health_check_response.model_dump_json()


# Mount Full MCP Contexts
mcp_server.mount(printfix_server, namespace="printfix")
mcp_app = mcp_server.http_app(path="/mcp")


# Combine lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan for FastMCP application"""
    async with mcp_app.lifespan(app):
        logger.info(f"Starting up {settings.PROJECT_NAME} v{settings.PROJECT_VERSION}")
        logger.info(f"Debug mode: {settings.DEBUG}")
        logger.info(f"Listening on: {settings.APP_HOST}:{settings.APP_PORT} - Workers: {settings.APP_WORKERS}")
        logger.info(f"Exec ID: {exec_id}")

        if not taskiq_broker.is_worker_process:
            await taskiq_broker.startup()
            logger.info("Taskiq broker started (client mode)")

        # Initialize rate limiter
        from redis.asyncio import Redis

        from app.core.rate_limit import RateLimiter

        rl_redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB_RATE_LIMIT,
            password=settings.REDIS_PASSWORD,
        )
        app.state.rate_limiter = RateLimiter(rl_redis)
        logger.info("Rate limiter initialized")

        try:
            yield
        finally:
            logger.info(f"Shutting down {settings.PROJECT_NAME}...")
            if hasattr(app.state, "rate_limiter"):
                await app.state.rate_limiter.close()
            if not taskiq_broker.is_worker_process:
                await taskiq_broker.shutdown()
            await JobStateManager.close()
            await asyncio.sleep(2)  # Failsafe delay


app = FastAPI(
    lifespan=lifespan,
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    docs_url="/docs",
    redoc_url=None,
    # openapi_url="/api/v1/openapi.json",
)

app.add_middleware(
    CorrelationIdMiddleware,
    generator=lambda: str(ULID()),
    validator=None,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log method, path, status, and duration for every HTTP request."""
    t0 = perf_counter()
    response = await call_next(request)
    duration_ms = (perf_counter() - t0) * 1000
    # Skip noisy paths
    if request.url.path not in ("/favicon.ico", "/health"):
        logger.info(
            f"{request.method} {request.url.path} "
            f"status={response.status_code} "
            f"duration={duration_ms:.1f}ms"
        )
    return response


app.include_router(printfix_router)
app.mount("/app", mcp_app)


@app.get(
    "/favicon.ico",
    include_in_schema=False,
)
async def favicon():
    return Response(
        content=b"\x00\x00\x01\x00\x01\x00\x10\x10\x02\x00\x01\x00\x01\x00\xb0\x00\x00"
        b"\x00\x16\x00\x00\x00\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x00\x00\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff"
        b"\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff"
        b"\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00\xff\xff\x00\x00"
        b"\xff\xff\x00\x00\xff\xff\x00\x00",
        media_type="image/x-icon",
    )


@app.get(
    "/health",
    include_in_schema=False,
)
async def health(request: Request, response: Response) -> HealthCheckResponse:
    """Health check endpoint"""

    return HealthCheckResponse(
        status="ok",
        version=settings.PROJECT_VERSION,
        uptime=perf_counter() - start_time,
        exec_id=exec_id,
    )


@app.get(
    "/",
    include_in_schema=False,
)
async def index() -> IndexResponse:
    return IndexResponse()
