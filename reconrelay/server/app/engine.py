"""The recursive event loop.

This is the box labelled "BBOT Recursive Event Loop Core" in the architecture
diagram: seeds enter a queue, modules consume them and push new events back on,
until nothing new is produced or a budget is hit.

Guards, in the order they bite:

1. de-duplication on ``(type, data)`` — stops exponential re-emission
2. scope allowlist — every derived event is re-checked, so a page linking to an
   unrelated third party cannot drag the scan out of scope
3. ``max_depth`` — how many hops from a seed
4. ``max_events`` — hard ceiling on total output
5. wall-clock timeout
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from .models import DNS_NAME, DNS_NAME_UNRESOLVED, EMAIL_ADDRESS, IP_ADDRESS, Event, Job, JobStatus
from .modules.base import Client, ModuleContext
from .modules.network import build_preset
from .policy import ScanPolicy

log = logging.getLogger("reconrelay.engine")

EventHandler = Callable[[list[Event]], Any]


@dataclass
class ScanResult:
    job_id: str
    events: list[Event] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    rejected_out_of_scope: list[str] = field(default_factory=list)
    truncated: bool = False
    timed_out: bool = False


class ScanEngine:
    def __init__(
        self,
        policy: ScanPolicy,
        client: Client,
        max_events: int = 5000,
        max_depth: int = 4,
        timeout: float = 900.0,
        queue_size: int = 10_000,
    ) -> None:
        self.policy = policy
        self.client = client
        self.max_events = max_events
        self.max_depth = max_depth
        self.timeout = timeout
        self.queue_size = queue_size

    def modules_for(self, job: Job):
        return build_preset(job.preset, active=job.active)

    async def run(
        self,
        job: Job,
        on_events: EventHandler | None = None,
        flush_every: int = 25,
    ) -> ScanResult:
        modules = self.modules_for(job)
        active_ok = job.active and self.policy.allow_active_modules
        if job.active and not active_ok:
            log.warning("job %s requested active modules; server policy forbids it", job.id)
        usable = [m for m in modules if active_ok or not getattr(m, "active", False)]

        ctx = ModuleContext(client=self.client, scan_id=job.id, policy=self.policy)
        seen: set[tuple[str, str]] = set()
        result = ScanResult(job_id=job.id)
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.queue_size)
        pending: list[Event] = []
        deadline = time.monotonic() + self.timeout
        counters: dict[str, int] = {}

        # ---- seed the queue from the job's authorised targets -------------
        accepted: dict[str, Event] = {}
        for seed in job.seeds:
            try:
                host = self.policy.assert_authorized(seed)
            except Exception:
                result.rejected_out_of_scope.append(seed)
                continue
            event = Event(
                type=IP_ADDRESS if _looks_like_ip(host) else DNS_NAME,
                data=host,
                module="seed",
                depth=0,
                scope_distance=0,
                tags=["seed"],
            )
            if event.key in seen:
                continue
            seen.add(event.key)
            accepted[event.id] = event
            pending.append(event)  # seeds are graph nodes too, not just inputs
            await queue.put(event)

        async def flush(force: bool = False) -> None:
            if not pending or (not force and len(pending) < flush_every):
                return
            batch, pending[:] = list(pending), []
            result.events.extend(batch)
            for event in batch:
                counters[event.type] = counters.get(event.type, 0) + 1
            if on_events is not None:
                maybe = on_events(batch)
                if asyncio.iscoroutine(maybe):
                    await maybe

        # ---- the loop -----------------------------------------------------
        while not queue.empty():
            if time.monotonic() > deadline:
                result.timed_out = True
                break
            if len(result.events) + len(pending) >= self.max_events:
                result.truncated = True
                break

            event = queue.get_nowait()

            for module in usable:
                if event.type not in module.watched_events:
                    continue
                if event.scope_distance > module.scope_distance:
                    continue
                try:
                    children = await module.handle_event(event, ctx)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # a broken module must not kill a scan
                    log.exception("module %s failed on %s", module.name, event.data)
                    counters["module_error"] = counters.get("module_error", 0) + 1
                    continue

                for child in children:
                    if child.key in seen:
                        continue
                    if child.depth > self.max_depth:
                        continue
                    if len(accepted) >= self.max_events:
                        # Hard ceiling: enforced per child, not per iteration, so
                        # a single chatty module cannot overshoot the budget.
                        result.truncated = True
                        continue
                    if not self._child_in_scope(child, accepted):
                        result.rejected_out_of_scope.append(child.data)
                        counters["out_of_scope"] = counters.get("out_of_scope", 0) + 1
                        continue
                    seen.add(child.key)
                    accepted[child.id] = child
                    pending.append(child)
                    if queue.qsize() < self.queue_size:
                        queue.put_nowait(child)
                    else:
                        result.truncated = True

                await flush()

        await flush(force=True)
        result.stats = dict(sorted(counters.items(), key=lambda kv: -kv[1]))
        result.stats["total_events"] = len(result.events)
        if result.truncated:
            result.stats["truncated"] = 1
        if result.timed_out:
            result.stats["timed_out"] = 1
        return result

    def _child_in_scope(self, event: Event, accepted: dict[str, Event]) -> bool:
        """Decide whether a derived event may join the graph.

        Hosts and URLs are matched against the allowlist directly. Everything
        else *inherits* its parent's decision, but only if that parent was
        itself accepted — so a resolved IP of an authorized hostname stays in
        scope, while an IP with no vetted parent cannot sneak in.
        """
        if event.type in (DNS_NAME, DNS_NAME_UNRESOLVED):
            return self.policy.in_scope(event.data)
        if event.type in ("URL", "URL_UNVERIFIED"):
            host = urlsplit(event.data).hostname or event.data
            return self.policy.in_scope(host)
        # IP_ADDRESS, EMAIL_ADDRESS, HTTP_RESPONSE, TECHNOLOGY, FINDING,
        # OPEN_TCP_PORT: passive or derived data, never a new scan target on
        # their own. Require a vetted parent.
        return event.parent_id is not None and event.parent_id in accepted


def _looks_like_ip(value: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def seed_list_from_payload(payload: Any, target: str) -> list[str]:
    """Fold the extension's harvested surface into engine seeds."""
    seeds: list[str] = [target]
    harvested = getattr(payload, "harvested", None)
    if harvested is None:
        return seeds
    for field_name in ("urls", "endpoints", "js_files", "cloud_assets"):
        seeds.extend(getattr(harvested, field_name, []) or [])
    return [s for s in dict.fromkeys(seeds) if s]


def apply_status(result: ScanResult, job: Job) -> Job:
    job.mark_completed(result.stats)
    if result.rejected_out_of_scope:
        job.stats["rejected_out_of_scope"] = len(result.rejected_out_of_scope)
    job.status = JobStatus.COMPLETED
    return job
