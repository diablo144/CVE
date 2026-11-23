"""Module contract for the recursive scan engine.

Mirrors BBOT's own shape — ``watched_events`` / ``produced_events`` /
``handle_event`` / ``scope_distance`` — so writing a module here and porting it
to a real BBOT module is a copy-paste job, not a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Protocol

from ..models import Event

# Injectable network primitives. Tests swap these for fakes so the engine can be
# exercised deterministically and without touching a real target.
FetchResult = tuple[int, dict[str, str], str]  # status, headers, body
Fetch = Callable[[str], Coroutine[Any, Any, FetchResult | None]]
Resolve = Callable[[str], Coroutine[Any, Any, list[str]]]
PortProbe = Callable[[str, int, float], Coroutine[Any, Any, bool]]


@dataclass
class Client:
    """Everything a module may use to reach the outside world."""

    fetch: Fetch
    resolve: Resolve
    port_probe: PortProbe
    cert_lookup: Callable[[str], Coroutine[Any, Any, list[str]]]
    user_agent: str = "Mozilla/5.0 (compatible; ReconRelay/1.0)"
    timeout: float = 10.0


@dataclass
class ModuleContext:
    client: Client
    scan_id: str
    policy: Any = None
    counters: dict[str, int] = field(default_factory=dict)


class Module(Protocol):
    name: str
    watched_events: tuple[str, ...]
    produced_events: tuple[str, ...]
    scope_distance: int
    active: bool

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        """Return zero or more new events derived from ``event``."""
        ...


class BaseModule:
    name = "base"
    watched_events: tuple[str, ...] = ()
    produced_events: tuple[str, ...] = ()
    scope_distance: int = 0
    active: bool = False

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:  # pragma: no cover
        return []

    def emit(self, parent: Event, type_: str, data: str, **pretty: Any) -> Event:
        """Build a child event, keeping depth/scope bookkeeping consistent."""
        return Event(
            type=type_,
            data=data,
            module=self.name,
            parent_id=parent.id,
            depth=parent.depth + 1,
            scope_distance=parent.scope_distance + 1,
            tags=pretty.pop("tags", []) if isinstance(pretty.get("tags"), list) else [],
            pretty=pretty,
        )
