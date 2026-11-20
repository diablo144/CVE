"""Job queue + event log.

Two implementations behind one interface:

* :class:`MemoryStore` — asyncio native, no infrastructure. Used by the test
  suite and by local dev.
* :class:`RedisStore` — the one that runs on the Oracle box, so the API tier
  and N worker containers can share work.

Swapping them is a config flag (``RR_USE_MEMORY_STORE``), nothing else changes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import redis.asyncio as aioredis

from .models import Event, Job, JobStatus

JOB_HASH = "rr:jobs"
QUEUE_KEY = "rr:queue"
EVENTS_KEY = "rr:events:{job_id}"
CHANNEL_KEY = "rr:channel:{job_id}"


@dataclass
class Channel:
    """A live subscription to one job's event stream.

    Registering is synchronous-with-the-event-loop, so a consumer can open the
    channel *before* reading history and never lose a publish in between.
    """

    job_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _cleanup: Callable[[], Awaitable[None]] | None = None

    async def get(self) -> dict[str, Any]:
        return await self.queue.get()

    async def close(self) -> None:
        if self._cleanup is not None:
            await self._cleanup()
            self._cleanup = None


class Store(Protocol):
    async def enqueue(self, job: Job) -> None: ...
    async def dequeue(self, timeout: float = 0.5) -> Job | None: ...
    async def save_job(self, job: Job) -> None: ...
    async def get_job(self, job_id: str) -> Job | None: ...
    async def list_jobs(self, limit: int = 50) -> list[Job]: ...
    async def append_events(self, job_id: str, events: list[Event]) -> int: ...
    async def get_events(self, job_id: str, after: int = 0, limit: int = 500) -> list[Event]: ...
    async def event_count(self, job_id: str) -> int: ...
    async def publish(self, job_id: str, message: dict[str, Any]) -> None: ...
    async def open_channel(self, job_id: str) -> Channel: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
class MemoryStore:
    """In-process store. Same semantics as the Redis one, minus durability."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._events: dict[str, list[Event]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, job: Job) -> None:
        async with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)
            if job.id not in self._order:
                self._order.append(job.id)
        await self._queue.put(job.id)

    async def dequeue(self, timeout: float = 0.5) -> Job | None:
        try:
            job_id = await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return None
        return await self.get_job(job_id)

    async def save_job(self, job: Job) -> None:
        async with self._lock:
            # Copy in and copy out: the Redis store cannot alias objects, so
            # neither can this one, or the two would drift apart in behaviour.
            self._jobs[job.id] = job.model_copy(deep=True)
            if job.id not in self._order:
                self._order.append(job.id)

    async def get_job(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job is not None else None

    async def list_jobs(self, limit: int = 50) -> list[Job]:
        return [job.model_copy(deep=True) for job in
                (self._jobs[jid] for jid in reversed(self._order[-limit:]))]

    async def append_events(self, job_id: str, events: list[Event]) -> int:
        if not events:
            return 0
        async with self._lock:
            bucket = self._events.setdefault(job_id, [])
            bucket.extend(events)
            return len(bucket)

    async def get_events(self, job_id: str, after: int = 0, limit: int = 500) -> list[Event]:
        return self._events.get(job_id, [])[after : after + limit]

    async def event_count(self, job_id: str) -> int:
        return len(self._events.get(job_id, []))

    async def publish(self, job_id: str, message: dict[str, Any]) -> None:
        for subscriber in list(self._subscribers.get(job_id, ())):
            subscriber.put_nowait(message)

    async def open_channel(self, job_id: str) -> Channel:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(job_id, set()).add(queue)

        async def cleanup() -> None:
            self._subscribers.get(job_id, set()).discard(queue)

        return Channel(job_id=job_id, queue=queue, _cleanup=cleanup)

    async def close(self) -> None:  # pragma: no cover - nothing to release
        return None


# ---------------------------------------------------------------------------
class RedisStore:
    """Redis-backed store: list for the queue, hashes for jobs, list for events."""

    def __init__(self, url: str) -> None:
        self._redis = aioredis.from_url(url, encoding="utf-8", decode_responses=True)

    @staticmethod
    def _events_key(job_id: str) -> str:
        return EVENTS_KEY.format(job_id=job_id)

    @staticmethod
    def _channel_key(job_id: str) -> str:
        return CHANNEL_KEY.format(job_id=job_id)

    async def enqueue(self, job: Job) -> None:
        await self.save_job(job)
        await self._redis.lpush(QUEUE_KEY, job.id)

    async def dequeue(self, timeout: float = 0.5) -> Job | None:
        # BLPOP with a whole-second timeout; 0 would block forever.
        result = await self._redis.brpop([QUEUE_KEY], timeout=max(1, int(timeout)))
        if not result:
            return None
        return await self.get_job(result[1])

    async def save_job(self, job: Job) -> None:
        await self._redis.hset(JOB_HASH, job.id, job.model_dump_json())

    async def get_job(self, job_id: str) -> Job | None:
        raw = await self._redis.hget(JOB_HASH, job_id)
        return Job.model_validate_json(raw) if raw else None

    async def list_jobs(self, limit: int = 50) -> list[Job]:
        raw = await self._redis.hgetall(JOB_HASH)
        jobs = [Job.model_validate_json(value) for value in raw.values()]
        jobs.sort(key=lambda j: j.created, reverse=True)
        return jobs[:limit]

    async def append_events(self, job_id: str, events: list[Event]) -> int:
        if not events:
            return 0
        key = self._events_key(job_id)
        await self._redis.rpush(key, *[event.model_dump_json() for event in events])
        await self._redis.expire(key, 7 * 24 * 3600)
        return int(await self._redis.llen(key))

    async def get_events(self, job_id: str, after: int = 0, limit: int = 500) -> list[Event]:
        raw = await self._redis.lrange(self._events_key(job_id), after, after + limit - 1)
        return [Event.model_validate_json(item) for item in raw]

    async def event_count(self, job_id: str) -> int:
        return int(await self._redis.llen(self._events_key(job_id)))

    async def publish(self, job_id: str, message: dict[str, Any]) -> None:
        await self._redis.publish(self._channel_key(job_id), json.dumps(message, default=str))

    async def open_channel(self, job_id: str) -> Channel:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._channel_key(job_id))
        channel = Channel(job_id=job_id)

        async def pump() -> None:
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    channel.queue.put_nowait(json.loads(message["data"]))
            except asyncio.CancelledError:  # pragma: no cover - teardown path
                raise
            except Exception:  # pragma: no cover - transport reset
                return

        task = asyncio.create_task(pump(), name=f"rr-redis-pubsub-{job_id}")

        async def cleanup() -> None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await pubsub.unsubscribe(self._channel_key(job_id))
            await pubsub.aclose()

        channel._cleanup = cleanup
        return channel

    async def close(self) -> None:
        await self._redis.aclose()


def build_store(use_memory: bool, redis_url: str) -> Store:
    return MemoryStore() if use_memory else RedisStore(redis_url)


def job_finished_message(job: Job) -> dict[str, Any]:
    return {
        "kind": "job.finished",
        "job_id": job.id,
        "status": job.status.value if isinstance(job.status, JobStatus) else job.status,
        "stats": job.stats,
        "error": job.error,
    }
