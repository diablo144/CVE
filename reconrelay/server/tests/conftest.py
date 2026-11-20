"""Shared fixtures: a deterministic fake network and a wired-up test app.

Nothing here touches the internet. The engine, modules, store, worker and HTTP
routes are all the real ones — only the four network primitives injected into
:class:`Client` are fakes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.modules.base import Client

API_KEY = "test-key-1234"
JWT_SECRET = "unit-test-secret"

HTML_HOME = """<!doctype html>
<html><head><title>Acme Portal</title>
<script src="/static/app.js"></script></head>
<body data-reactroot="true">
  <!-- TODO: staging login is admin@acme-internal.io -->
  <a href="/dashboard">Dash</a>
  <a href="https://api.example.com/v1/users">Users API</a>
  <a href="//cdn.example.com/assets/logo.png" style="display:none">logo</a>
  <a href="https://thirdparty-analytics.net/track">analytics</a>
  <form action="/login" method="post"><input name="csrfmiddlewaretoken"></form>
  <script>const cfg = {api:"/api/v1/config", api_key:"sk_live_abc123def456ghi789"};</script>
  <p>Contact support@example.com</p>
</body></html>
"""

HTML_API = '{"users": [], "docs": "/api/v1/openapi.json"}'

PAGES: dict[str, tuple[int, dict[str, str], str]] = {
    "https://example.com": (
        200,
        {"server": "nginx/1.25.3", "content-type": "text/html; charset=utf-8"},
        HTML_HOME,
    ),
    "https://example.com/": (
        200,
        {"server": "nginx/1.25.3", "content-type": "text/html; charset=utf-8"},
        HTML_HOME,
    ),
    "http://example.com": (404, {"server": "nginx/1.25.3"}, ""),
    "https://api.example.com/v1/users": (
        200,
        {"server": "gunicorn", "content-type": "application/json"},
        HTML_API,
    ),
}

DNS_MAP: dict[str, list[str]] = {
    "example.com": ["93.184.216.34"],
    "api.example.com": ["93.184.216.35"],
    "cdn.example.com": ["93.184.216.36"],
    "dev.example.com": [],
}

CT_MAP: dict[str, list[str]] = {
    "example.com": ["example.com", "api.example.com", "dev.example.com"],
}

OPEN_PORTS = {("93.184.216.34", 443), ("93.184.216.34", 22)}


class FakeNetwork:
    """Records what the engine asked for, so tests can assert on behaviour."""

    def __init__(
        self,
        pages: dict | None = None,
        dns: dict | None = None,
        ct: dict | None = None,
        ports: set | None = None,
    ) -> None:
        self.pages = PAGES if pages is None else pages
        self.dns = DNS_MAP if dns is None else dns
        self.ct = CT_MAP if ct is None else ct
        self.ports = OPEN_PORTS if ports is None else ports
        self.fetched: list[str] = []
        self.resolved: list[str] = []
        self.probed: list[tuple[str, int]] = []
        self.ct_lookups: list[str] = []

    async def fetch(self, url: str):
        self.fetched.append(url)
        return self.pages.get(url)

    async def resolve(self, name: str) -> list[str]:
        self.resolved.append(name)
        return list(self.dns.get(name, []))

    async def port_probe(self, host: str, port: int, timeout: float = 0.5) -> bool:
        self.probed.append((host, port))
        return (host, port) in self.ports

    async def cert_lookup(self, domain: str) -> list[str]:
        self.ct_lookups.append(domain)
        return list(self.ct.get(domain, []))

    def as_client(self) -> Client:
        return Client(
            fetch=self.fetch,
            resolve=self.resolve,
            port_probe=self.port_probe,
            cert_lookup=self.cert_lookup,
        )


@pytest.fixture
def network() -> FakeNetwork:
    return FakeNetwork()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        use_memory_store=True,
        inline_worker=True,
        authorized_scopes="example.com",
        allow_any_scope=False,
        allow_active_modules=False,
        api_keys=API_KEY,
        jwt_secret=JWT_SECRET,
        max_events_per_scan=500,
        max_depth=4,
        scan_timeout_seconds=30,
        worker_poll_interval=0.01,
    )


@pytest.fixture
def app(settings, network):
    return create_app(settings=settings, client=network.as_client())


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client) -> dict[str, str]:
    response = client.post("/v1/auth/token", json={"api_key": API_KEY, "operator": "tester"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def token(client) -> str:
    response = client.post("/v1/auth/token", json={"api_key": API_KEY, "operator": "tester"})
    return response.json()["access_token"]
