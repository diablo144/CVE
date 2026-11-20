"""Wire models: what the browser extension sends, and what a scan produces."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Event types mirror the BBOT vocabulary so output ports cleanly onto any
# downstream tooling you already have.
# ---------------------------------------------------------------------------
DNS_NAME = "DNS_NAME"
IP_ADDRESS = "IP_ADDRESS"
URL = "URL"
URL_UNVERIFIED = "URL_UNVERIFIED"
EMAIL_ADDRESS = "EMAIL_ADDRESS"
HTTP_RESPONSE = "HTTP_RESPONSE"
OPEN_TCP_PORT = "OPEN_TCP_PORT"
TECHNOLOGY = "TECHNOLOGY"
FINDING = "FINDING"
DNS_NAME_UNRESOLVED = "DNS_NAME_UNRESOLVED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    ident = uuid.uuid4().hex[:16]
    return f"{prefix}{ident}" if prefix else ident


# ---------------------------------------------------------------------------
# Inbound: the "JSON Seed Payload" produced by the extension harvester
# ---------------------------------------------------------------------------
class HarvestedSurface(BaseModel):
    """Everything the content script + network interceptor dug out of a page."""

    urls: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)
    js_files: list[str] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    forms: list[dict[str, Any]] = Field(default_factory=list)
    cloud_assets: list[str] = Field(default_factory=list)
    response_headers: dict[str, str] = Field(default_factory=dict)
    api_calls: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator(
        "urls", "emails", "endpoints", "js_files", "comments", "cloud_assets",
        mode="before",
    )
    @classmethod
    def _dedupe(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out[:500]  # hard cap: never let a page flood the queue


class ScanOptions(BaseModel):
    preset: str = "recon"
    max_depth: int | None = Field(default=None, ge=1, le=8)
    active: bool = False

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        allowed = {"passive", "recon", "active"}
        if value not in allowed:
            raise ValueError(f"preset must be one of {sorted(allowed)}")
        return value


class SeedPayload(BaseModel):
    """Submitted by the extension for the page the operator is looking at."""

    target: str = Field(min_length=3, max_length=2048)
    page_url: str | None = None
    page_title: str | None = None
    harvested: HarvestedSurface = Field(default_factory=HarvestedSurface)
    options: ScanOptions = Field(default_factory=ScanOptions)
    client: str = "extension"


class TokenRequest(BaseModel):
    api_key: str = Field(min_length=8)
    operator: str = "operator"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# Scan lifecycle
# ---------------------------------------------------------------------------
class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Event(BaseModel):
    """A node in the attack-surface graph."""

    id: str = Field(default_factory=lambda: new_id("ev_"))
    type: str
    data: str
    module: str = "seed"
    parent_id: str | None = None
    depth: int = 0
    scope_distance: int = 0
    tags: list[str] = Field(default_factory=list)
    pretty: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=utcnow)

    @property
    def key(self) -> tuple[str, str]:
        return (self.type, self.data)

    def slim(self) -> "Event":
        """Copy without the bulky fields the graph viewer never needs.

        Response bodies drive extraction inside the engine, but persisting 200 KB
        per URL to Redis/Postgres would bury the useful signal.
        """
        if "body" not in self.pretty:
            return self
        trimmed = dict(self.pretty)
        body = str(trimmed.pop("body"))
        trimmed["body_bytes"] = len(body)
        trimmed["body_excerpt"] = body[:400]
        return self.model_copy(update={"pretty": trimmed})


class Job(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job_"))
    status: JobStatus = JobStatus.QUEUED
    target: str
    preset: str = "recon"
    active: bool = False
    max_depth: int = 4
    operator: str = "operator"
    source_url: str | None = None
    created: datetime = Field(default_factory=utcnow)
    started: datetime | None = None
    finished: datetime | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    error: str | None = None
    seeds: list[str] = Field(default_factory=list)

    def mark_running(self) -> None:
        self.status = JobStatus.RUNNING
        self.started = utcnow()

    def mark_completed(self, stats: dict[str, int]) -> None:
        self.status = JobStatus.COMPLETED
        self.stats = stats
        self.finished = utcnow()

    def mark_failed(self, error: str) -> None:
        self.status = JobStatus.FAILED
        self.error = error[:2000]
        self.finished = utcnow()


class JobCreateResponse(BaseModel):
    job_id: str
    status: JobStatus
    target: str
    seeds: int


class EventPage(BaseModel):
    job_id: str
    after: int
    events: list[Event]
    total: int
    status: JobStatus
