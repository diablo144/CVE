"""HTTP + WebSocket surface consumed by the browser extension."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from .auth import check_api_key, current_operator, mint_token, verify_token, AuthError
from .config import Settings, get_settings
from .engine import seed_list_from_payload
from .models import (
    Event,
    EventPage,
    Job,
    JobCreateResponse,
    JobStatus,
    SeedPayload,
    TokenRequest,
    TokenResponse,
)
from .policy import PolicyViolation
from .store import Store, job_finished_message
from .worker import Worker

log = logging.getLogger("reconrelay.api")

router = APIRouter()

# Seconds without traffic before the stream sends a heartbeat. Kept well under
# the default nginx ``proxy_read_timeout`` (60s) in deploy/nginx.conf.
WS_IDLE_SECONDS = 20.0


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_worker(request: Request) -> Worker:
    return request.app.state.worker


# --------------------------------------------------------------------- health
@router.get("/health", tags=["meta"])
async def health(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    store: Store = request.app.state.store
    return {
        "status": "ok",
        "app": settings.app_name,
        "store": type(store).__name__,
        "scopes_configured": len(settings.scope_list),
        "allow_any_scope": settings.allow_any_scope,
        "active_modules_allowed": settings.allow_active_modules,
    }


# ----------------------------------------------------------------------- auth
@router.post("/v1/auth/token", response_model=TokenResponse, tags=["auth"])
async def issue_token(body: TokenRequest, settings: Settings = Depends(get_settings)) -> TokenResponse:
    if not check_api_key(settings, body.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    token, ttl = mint_token(settings, body.operator)
    return TokenResponse(access_token=token, expires_in=ttl)


# ----------------------------------------------------------------------- jobs
@router.post(
    "/v1/jobs",
    response_model=JobCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["scan"],
)
async def create_job(
    payload: SeedPayload,
    request: Request,
    operator: str = Depends(current_operator),
    settings: Settings = Depends(get_settings),
) -> JobCreateResponse:
    store: Store = get_store(request)
    policy = request.app.state.policy

    try:
        target = policy.assert_authorized(payload.target)
    except PolicyViolation as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if payload.options.active:
        try:
            policy.assert_active_allowed()
        except PolicyViolation as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    raw_seeds = seed_list_from_payload(payload, payload.target)
    allowed, rejected = policy.filter_seeds(raw_seeds)
    # The primary target always stays; everything else is allowlist-filtered.
    if target not in allowed:
        allowed.insert(0, target)

    job = Job(
        target=target,
        preset=payload.options.preset,
        active=payload.options.active and settings.allow_active_modules,
        max_depth=min(payload.options.max_depth or settings.max_depth, settings.max_depth),
        operator=operator,
        source_url=payload.page_url,
        seeds=allowed[:200],
    )
    await store.enqueue(job)
    log.info(
        "job %s queued by %s target=%s preset=%s seeds=%d rejected=%d",
        job.id, operator, target, job.preset, len(allowed), len(rejected),
    )
    return JobCreateResponse(job_id=job.id, status=job.status, target=job.target, seeds=len(allowed))


@router.get("/v1/jobs", tags=["scan"])
async def list_jobs(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    operator: str = Depends(current_operator),
) -> dict[str, Any]:
    store: Store = get_store(request)
    jobs = await store.list_jobs(limit=limit)
    return {
        "jobs": [
            {
                "id": job.id,
                "target": job.target,
                "status": job.status,
                "preset": job.preset,
                "operator": job.operator,
                "created": job.created,
                "stats": job.stats,
            }
            for job in jobs
        ]
    }


@router.get("/v1/jobs/{job_id}", tags=["scan"])
async def get_job(
    job_id: str,
    request: Request,
    operator: str = Depends(current_operator),
) -> dict[str, Any]:
    store: Store = get_store(request)
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown job")
    return {
        "job": job.model_dump(mode="json"),
        "event_count": await store.event_count(job_id),
    }


@router.get("/v1/jobs/{job_id}/events", response_model=EventPage, tags=["scan"])
async def get_events(
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    operator: str = Depends(current_operator),
) -> EventPage:
    store: Store = get_store(request)
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown job")
    events: list[Event] = await store.get_events(job_id, after=after, limit=limit)
    return EventPage(
        job_id=job_id,
        after=after + len(events),
        events=events,
        total=await store.event_count(job_id),
        status=job.status,
    )


@router.post("/v1/jobs/{job_id}/cancel", tags=["scan"])
async def cancel_job(
    job_id: str,
    request: Request,
    operator: str = Depends(current_operator),
) -> dict[str, Any]:
    store: Store = get_store(request)
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown job")
    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        job.status = JobStatus.CANCELLED
        await store.save_job(job)
    return {"job_id": job_id, "status": job.status}


# ------------------------------------------------------------------ telemetry
@router.websocket("/v1/ws/jobs/{job_id}")
async def job_stream(websocket: WebSocket, job_id: str, token: str = Query(default="")) -> None:
    """Live event stream to the extension popup / devtools panel.

    Browsers cannot set headers on a WebSocket handshake, so the JWT rides in
    the query string. It is short-lived by design (see ``RR_JWT_TTL_SECONDS``).

    Order matters: the channel is opened *before* the backfill is read, so a
    client that connects mid-scan sees history once and then the live tail
    without a gap. Duplicates (if a publish lands during backfill) are dropped
    client-side by event id.
    """
    settings: Settings = websocket.app.state.settings
    try:
        claims = verify_token(settings, token)
    except AuthError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return

    store: Store = websocket.app.state.store
    job = await store.get_job(job_id)
    if job is None:
        await websocket.close(code=4404, reason="unknown job")
        return

    channel = await store.open_channel(job_id)
    await websocket.accept()
    try:
        await websocket.send_json(
            {
                "kind": "job.state",
                "job_id": job_id,
                "status": job.status,
                "operator": claims.get("sub"),
            }
        )
        history = await store.get_events(job_id, after=0, limit=2000)
        if history:
            await websocket.send_json(
                {
                    "kind": "events",
                    "job_id": job_id,
                    "backfill": True,
                    "events": [event.slim().model_dump(mode="json") for event in history],
                }
            )
        # If the scan already finished before this client arrived, nobody will
        # ever publish job.finished — synthesise it so the client can close.
        refreshed = await store.get_job(job_id)
        if refreshed is not None and refreshed.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            await websocket.send_json(job_finished_message(refreshed))
            return
        while True:
            try:
                message = await asyncio.wait_for(channel.get(), timeout=WS_IDLE_SECONDS)
            except (asyncio.TimeoutError, TimeoutError):
                # Idle: re-check the job and keep the socket alive so a reverse
                # proxy's read timeout cannot silently drop the stream.
                current = await store.get_job(job_id)
                if current is not None and current.status not in (
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                ):
                    await websocket.send_json(job_finished_message(current))
                    return
                await websocket.send_json({"kind": "heartbeat", "job_id": job_id})
                continue
            await websocket.send_json(message)
            if message.get("kind") == "job.finished":
                break
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - transport level
        log.debug("ws closed for %s: %s", job_id, exc)
    finally:
        await channel.close()
        try:
            await websocket.close()
        except Exception:
            pass


# --------------------------------------------------------------- error shape
async def policy_exception_handler(request: Request, exc: PolicyViolation) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc), "target": exc.target})
