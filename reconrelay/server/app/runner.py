"""Standalone worker entrypoint for the execution-engine container.

    RR_REDIS_URL=redis://redis:6379/0 RR_USE_MEMORY_STORE=false python -m app.runner

Scale this container horizontally; the Redis list is the only coordination
point. Keep ``RR_AUTHORIZED_SCOPES`` identical to the API tier so a worker can
never be talked into scanning something the gateway would have refused.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .config import get_settings
from .engine import ScanEngine
from .modules.base import Client
from .net import build_client
from .policy import ScanPolicy
from .store import build_store
from .worker import Worker

log = logging.getLogger("reconrelay.runner")


async def main(client: Client | None = None) -> None:
    settings = get_settings()
    if settings.use_memory_store:  # pragma: no cover - misconfiguration guard
        log.warning("worker started with the memory store; jobs will not be shared with the API tier")
    store = build_store(settings.use_memory_store, settings.redis_url)
    policy = ScanPolicy(
        scopes=settings.scope_list,
        allow_any_scope=settings.allow_any_scope,
        allow_active_modules=settings.allow_active_modules,
        max_depth=settings.max_depth,
    )
    engine = ScanEngine(
        policy=policy,
        client=client or build_client(),
        max_events=settings.max_events_per_scan,
        max_depth=settings.max_depth,
        timeout=settings.scan_timeout_seconds,
    )
    worker = Worker(store, policy, engine)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    task = asyncio.create_task(worker.loop(settings.worker_poll_interval))
    await stop.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await store.close()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
