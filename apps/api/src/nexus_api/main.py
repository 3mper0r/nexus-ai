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
from nexus_api.core.body_limit import BodySizeLimitMiddleware
from nexus_api.core.config import Settings
from nexus_api.core.errors import register_exception_handlers
from nexus_api.core.logging import configure_logging
from nexus_api.core.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from nexus_api.db.engine import create_engine, create_sessionmaker
from nexus_api.documents.ingestion import IngestionRunner
from nexus_api.embeddings.base import Embedder
from nexus_api.embeddings.provider import EmbedderProvider
from nexus_api.llm.claude import ClaudeAnswerGenerator
from nexus_api.llm.generator import AnswerGenerator
from nexus_api.rag.service import ChatService

# Multipart framing (boundaries, part headers) on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

logger = structlog.stdlib.get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    embedder: Embedder | None = None,
    answer_generator: AnswerGenerator | None = None,
) -> FastAPI:
    """Build a fully configured application.

    A factory (instead of a module-level ``app``) means importing this module has
    no side effects, and tests can build isolated apps with explicit settings.
    ``embedder`` injects a ready embedder (tests); by default the pinned
    sentence-transformers model is loaded from the local cache in the background.
    ``answer_generator`` injects a generator (tests); by default Claude is used when
    ``ANTHROPIC_API_KEY`` is set, and grounded answers are disabled otherwise.
    """
    if settings is None:
        settings = Settings()  # required values come from env
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        sessionmaker = create_sessionmaker(engine)
        app.state.sessionmaker = sessionmaker
        if embedder is not None:
            provider = EmbedderProvider.ready(embedder)
        else:
            provider = EmbedderProvider(lambda: _load_default_embedder(settings))
            provider.start_loading()
        runner = IngestionRunner(sessionmaker, provider, settings)
        runner.start()
        generator = answer_generator
        owned_generator: ClaudeAnswerGenerator | None = None
        if generator is None and settings.llm_configured:
            generator = owned_generator = ClaudeAnswerGenerator(settings)
        chat = ChatService(sessionmaker, provider, settings, generator)
        chat.start()
        app.state.embedder_provider = provider
        app.state.ingestion_runner = runner
        app.state.chat_service = chat
        logger.info(
            "application_startup",
            app_env=settings.app_env,
            version=__version__,
            database=settings.database_url.render_as_string(hide_password=True),
            llm_configured=settings.llm_configured,
            llm_model=settings.llm_model,
        )
        try:
            yield
        finally:
            # Order matters: stop ingestion (which still writes status updates)
            # before the engine that those writes need is disposed.
            await runner.shutdown()
            await chat.shutdown()
            if owned_generator is not None:
                await owned_generator.aclose()
            await provider.shutdown()
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
    # Innermost of ours: runs after the request ID is assigned, so 413s carry it.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
    )
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


def _load_default_embedder(settings: Settings) -> Embedder:
    """Runs in a worker thread (see EmbedderProvider).

    The import lives here, not at module level: importing torch and transformers
    takes seconds of CPU, which would block the event loop (and liveness) if it
    ran in the lifespan, and is unnecessary when an embedder is injected.
    """
    from nexus_api.embeddings.e5 import load_e5_embedder

    return load_e5_embedder(settings)


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
