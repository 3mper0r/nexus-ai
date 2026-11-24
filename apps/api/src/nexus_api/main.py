"""Application factory and process entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nexus_api import __version__
from nexus_api.api.v1.router import router as api_v1_router
from nexus_api.core.config import Settings
from nexus_api.core.errors import register_exception_handlers
from nexus_api.core.logging import configure_logging
from nexus_api.core.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from nexus_api.db.engine import create_engine, create_sessionmaker

logger = structlog.stdlib.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully configured application.

    A factory (instead of a module-level ``app``) means importing this module has
    no side effects, and tests can build isolated apps with explicit settings.
    """
    if settings is None:
        settings = Settings()  # required values come from env
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        logger.info(
            "application_startup",
            app_env=settings.app_env,
            version=__version__,
            database=settings.database_url.render_as_string(hide_password=True),
        )
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("application_shutdown")

    app = FastAPI(
        title="NEXUS AI API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # Middleware added last runs first. CORS is outermost so that even the
    # 500 responses produced by RequestIdMiddleware carry CORS headers.
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_exception_handlers(app)
    app.include_router(api_v1_router)
    return app


def run() -> None:
    """Run with uvicorn using settings from the environment (``nexus-api`` script)."""
    settings = Settings()
    uvicorn.run(
        "nexus_api.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
        access_log=False,
    )
