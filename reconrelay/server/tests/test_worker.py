"""Worker lifecycle: queue -> running -> completed/failed, with telemetry.

Regression coverage for a bug that left every job stuck in ``running``: the
status transitions must actually be persisted and published, not just mutated
on an in-memory object.
"""

from __future__ import annotations

import pytest

from app.engine import ScanEngine
from app.models import Job, JobStatus
from app.policy import ScanPolicy
from app.store import MemoryStore
from app.worker import Worker


async def make_worker(network, scopes=("example.com",)) -> tuple[Worker, MemoryStore]:
    store = MemoryStore()
    policy = ScanPolicy(scopes=list(scopes))
    engine = ScanEngine(policy=policy, client=network.as_client(), max_events=200, timeout=20)
    return Worker(store, policy, engine), store


async def test_dequeue_with_an_empty_queue_returns_none(network):
    worker, _ = await make_worker(network)
    assert await worker.work_once() is None


async def test_worker_publishes_started_then_finished(network):
    worker, store = await make_worker(network)
    job = Job(target="example.com", seeds=["example.com"], preset="recon")
    await store.enqueue(job)
    channel = await store.open_channel(job.id)

    finished = await worker.work_once()

    assert finished is not None
    assert finished.status == JobStatus.COMPLETED
    assert finished.started is not None
    assert finished.finished is not None

    messages = []
    while not channel.queue.empty():
        messages.append(channel.queue.get_nowait())
    kinds = [message["kind"] for message in messages]
    assert kinds[0] == "job.started", f"first message was {kinds[0]}"
    assert kinds[-1] == "job.finished"
    assert "events" in kinds

    # The state must survive a round trip through the store, not just live on
    # the object the worker happened to hold.
    persisted = await store.get_job(job.id)
    assert persisted.status == JobStatus.COMPLETED
    assert persisted.stats["total_events"] > 0
    assert await store.event_count(job.id) > 0


async def test_worker_records_failure_instead_of_leaving_a_zombie_job(network):
    worker, store = await make_worker(network)
    job = Job(target="example.com", seeds=["example.com"])
    await store.enqueue(job)

    async def explode(*args, **kwargs):
        raise RuntimeError("engine on fire")

    worker.engine.run = explode  # type: ignore[method-assign]
    finished = await worker.work_once()

    assert finished.status == JobStatus.FAILED
    assert "engine on fire" in finished.error
    assert (await store.get_job(job.id)).status == JobStatus.FAILED


async def test_worker_will_not_scan_an_unauthorised_seed(network):
    """Even a directly-queued job is re-checked against the allowlist."""
    worker, store = await make_worker(network)
    job = Job(target="evil.example", seeds=["evil.example"])
    await store.enqueue(job)

    finished = await worker.work_once()

    assert finished.status == JobStatus.COMPLETED
    assert finished.stats["total_events"] == 0
    assert finished.stats.get("rejected_out_of_scope") == 1
    assert network.fetched == []
    assert await store.event_count(job.id) == 0


async def test_store_never_hands_out_a_live_reference(network):
    """MemoryStore must copy, or it diverges from the Redis store's semantics."""
    _, store = await make_worker(network)
    job = Job(target="example.com", seeds=["example.com"])
    await store.save_job(job)

    job.status = JobStatus.CANCELLED  # mutate the caller's copy
    assert (await store.get_job(job.id)).status == JobStatus.QUEUED

    first = await store.get_job(job.id)
    first.target = "mutated"
    assert (await store.get_job(job.id)).target == "example.com"
