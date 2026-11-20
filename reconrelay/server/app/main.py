"""Application factory.

``create_app`` wires settings -> store -> policy -> engine -> worker so that
the same object graph is used by uvicorn in a container and by the test suite.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .engine import ScanEngine
from .modules.base import Client
from .net import build_client
from .policy import PolicyViolation, ScanPolicy
from .routes import policy_exception_handler, router
from .store import Store, build_store
from .worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("reconrelay")


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    client: Client | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.store = store or build_store(settings.use_memory_store, settings.redis_url)
        app.state.policy = ScanPolicy(
            scopes=settings.scope_list,
            allow_any_scope=settings.allow_any_scope,
            allow_active_modules=settings.allow_active_modules,
            max_depth=settings.max_depth,
        )
        app.state.client = client or build_client()
        app.state.engine = ScanEngine(
            policy=app.state.policy,
            client=app.state.client,
            max_events=settings.max_events_per_scan,
            max_depth=settings.max_depth,
            timeout=settings.scan_timeout_seconds,
        )
        app.state.worker = Worker(app.state.store, app.state.policy, app.state.engine)
        if settings.inline_worker:
            app.state.worker_task = app.state.worker.start(settings.worker_poll_interval)
        log.info(
            "startup store=%s inline_worker=%s scopes=%s",
            type(app.state.store).__name__,
            settings.inline_worker,
            settings.scope_list or "NONE",
        )
        try:
            yield
        finally:
            if settings.inline_worker:
                await app.state.worker.stop()
            await app.state.store.close()

    app = FastAPI(
        title="ReconRelay Gateway",
        version="1.0.0",
        description="Seed intake + scan orchestration for the ReconRelay browser extension.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Client-Version"],
        max_age=600,
    )

    app.add_exception_handler(PolicyViolation, policy_exception_handler)
    app.include_router(router)

    # Route the injected Settings at the instance we were built with, so tests
    # (and any caller passing custom config) get their settings, not the global.
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
