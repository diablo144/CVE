"""Worker: pulls jobs off the queue, runs the engine, streams events back.

Runs as its own container in production (``python -m app.runner``) and as an
in-process task when ``RR_INLINE_WORKNER=true`` (dev + tests).
"""

from __future__ import annotations

import asyncio
import logging

from .engine import ScanEngine
from .models import Job, JobStatus
from .policy import ScanPolicy
from .store import Store, job_finished_message

log = logging.getLogger("reconrelay.worker")


class Worker:
    def __init__(self, store: Store, policy: ScanPolicy, engine: ScanEngine) -> None:
        self.store = store
        self.policy = policy
        self.engine = engine
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------- one job
    async def _persist(self, job: Job, kind: str) -> None:
        """Write the job state and tell any listening client about it."""
        await self.store.save_job(job)
        await self.store.publish(
            job.id, {"kind": kind, "job_id": job.id, "status": job.status}
        )

    async def run_job(self, job: Job) -> Job:
        job.mark_running()
        await self._persist(job, "job.started")

        async def on_events(batch) -> None:
            persisted = [event.slim() for event in batch]
            await self.store.append_events(job.id, persisted)
            await self.store.publish(
                job.id,
                {
                    "kind": "events",
                    "job_id": job.id,
                    "events": [e.model_dump(mode="json") for e in persisted],
                },
            )

        try:
            result = await self.engine.run(job, on_events=on_events)
            job.mark_completed(result.stats)
            if result.rejected_out_of_scope:
                job.stats["rejected_out_of_scope"] = len(result.rejected_out_of_scope)
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            await self.store.save_job(job)
            raise
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.mark_failed(str(exc))

        await self.store.save_job(job)
        await self.store.publish(job.id, job_finished_message(job))
        return job

    # ---------------------------------------------------------- dequeue loop
    async def work_once(self) -> Job | None:
        job = await self.store.dequeue(timeout=0.5)
        if job is None:
            return None
        return await self.run_job(job)

    async def loop(self, poll_interval: float = 0.25) -> None:  # pragma: no cover
        log.info("worker started")
        while True:
            try:
                if await self.work_once() is None:
                    await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker iteration failed")
                await asyncio.sleep(1.0)

    def start(self, poll_interval: float = 0.25) -> asyncio.Task:  # pragma: no cover
        self._task = asyncio.create_task(self.loop(poll_interval), name="rr-worker")
        return self._task

    async def stop(self) -> None:  # pragma: no cover
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
