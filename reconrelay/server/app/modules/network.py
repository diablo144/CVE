"""Network-facing modules: DNS, HTTP fingerprinting, certificate transparency.

All network access goes through :class:`Client` so the whole engine can be
driven by fakes in tests.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..models import (
    DNS_NAME,
    DNS_NAME_UNRESOLVED,
    EMAIL_ADDRESS,
    FINDING,
    HTTP_RESPONSE,
    IP_ADDRESS,
    OPEN_TCP_PORT,
    TECHNOLOGY,
    URL,
    URL_UNVERIFIED,
    Event,
)
from .base import BaseModule, ModuleContext

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TECH_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("React", re.compile(r"data-reactroot|__NEXT_DATA__|react\.production", re.I)),
    ("Vue", re.compile(r"data-v-[0-9a-f]{8}|vue\.runtime", re.I)),
    ("Angular", re.compile(r"ng-version=", re.I)),
    ("WordPress", re.compile(r"wp-content|wp-includes", re.I)),
    ("jQuery", re.compile(r"jquery[.-]?\d", re.I)),
    ("Bootstrap", re.compile(r"bootstrap(\.min)?\.css", re.I)),
    ("Nginx", re.compile(r"^nginx", re.I)),
    ("Apache", re.compile(r"^apache", re.I)),
    ("Cloudflare", re.compile(r"cloudflare", re.I)),
    ("Django", re.compile(r"csrfmiddlewaretoken", re.I)),
    ("Laravel", re.compile(r"laravel_session", re.I)),
    ("PHP", re.compile(r"\.php\b|phpsessid", re.I)),
    ("ASP.NET", re.compile(r"__VIEWSTATE|asp\.net_sessionid", re.I)),
]


class Speculate(BaseModule):
    """Turn hints into things other modules can act on.

    Seeds enter as bare hosts; this is what gives the graph its first URLs, so
    the HTTP tier of the pipeline has something to fetch.
    """

    name = "speculate"
    watched_events = (URL_UNVERIFIED, DNS_NAME, IP_ADDRESS)
    produced_events = (URL, DNS_NAME, IP_ADDRESS, URL_UNVERIFIED, EMAIL_ADDRESS)
    scope_distance = 3

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        raw = event.data.strip()
        out: list[Event] = []

        if event.type in (DNS_NAME, IP_ADDRESS):
            for scheme in ("https://", "http://"):
                out.append(self.emit(event, URL_UNVERIFIED, f"{scheme}{raw}"))
            return out

        if raw.startswith(("http://", "https://")):
            out.append(self.emit(event, URL, raw))
            host = urlsplit(raw).hostname or ""
            if host:
                out.append(self.emit(event, DNS_NAME, host.lower()))
        elif _EMAIL_RE.fullmatch(raw):
            out.append(self.emit(event, EMAIL_ADDRESS, raw))
            domain = raw.rsplit("@", 1)[-1].lower()
            if domain:
                out.append(self.emit(event, DNS_NAME, domain))
        else:
            out.append(self.emit(event, DNS_NAME, raw.lower()))
        return out


class DNSResolve(BaseModule):
    name = "dns_resolve"
    watched_events = (DNS_NAME,)
    produced_events = (IP_ADDRESS, DNS_NAME_UNRESOLVED)
    scope_distance = 3

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        addresses = await ctx.client.resolve(event.data)
        if not addresses:
            return [self.emit(event, DNS_NAME_UNRESOLVED, event.data)]
        return [
            self.emit(event, IP_ADDRESS, address, rdns=event.data)
            for address in sorted(set(addresses))
        ]


class HTTPProbe(BaseModule):
    """Fetch a URL once, fingerprint it, and emit the response for extraction."""

    name = "http_probe"
    watched_events = (URL,)
    produced_events = (HTTP_RESPONSE, TECHNOLOGY)
    scope_distance = 3

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        result = await ctx.client.fetch(event.data)
        if result is None:
            return []
        status, headers, body = result
        lowered = {k.lower(): v for k, v in headers.items()}
        out = [
            self.emit(
                event,
                HTTP_RESPONSE,
                event.data,
                status_code=status,
                content_type=lowered.get("content-type", ""),
                body=body[:200_000],
                tags=["http"],
            )
        ]

        blob = f"{body}\n{lowered.get('server','')}\n{lowered.get('x-powered-by','')}"
        for tech, pattern in _TECH_PATTERNS:
            if pattern.search(blob):
                out.append(self.emit(event, TECHNOLOGY, tech, evidence=pattern.pattern[:60]))
        for header, marker in (
            ("server", "server"),
            ("x-powered-by", "x-powered-by"),
            ("x-aspnet-version", "aspnet"),
            ("x-generator", "generator"),
        ):
            value = lowered.get(header)
            if value:
                out.append(self.emit(event, TECHNOLOGY, f"{marker}: {value}".strip(), source=header))
        return out


class Extract(BaseModule):
    """Pull hosts, endpoints and emails out of an HTTP response body."""

    name = "extract"
    watched_events = (HTTP_RESPONSE,)
    produced_events = (URL_UNVERIFIED, DNS_NAME, EMAIL_ADDRESS)
    scope_distance = 3

    _URL_RE = re.compile(
        r"""(?ix)
        (?:href|src|action|content|data-url|poster)\s*=\s*["']([^"'\s>]{4,300})["']
        |
        ["'](/(?:api|v\d|graphql|rest|admin|internal)[A-Za-z0-9_\-/{}:.]{1,200})["']
        """
    )
    _HOST_RE = re.compile(
        r"(?<![A-Za-z0-9.@-])((?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"(?:com|net|org|io|dev|app|ai|cloud|sh|xyz|info|co|gov|edu|in|me|tech|site|online|host))"
        r"(?![A-Za-z0-9-])"
    )
    _SECRET_RE = re.compile(
        r"(?i)\b(aws_access_key_id|aws_secret_access_key|api[_-]?key|secret[_-]?key|"
        r"bearer\s+[A-Za-z0-9\-._~+/]{20,})"
    )

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        body = event.pretty.get("body", "")
        base = event.data
        base_parts = urlsplit(base)
        out: list[Event] = []
        seen: set[tuple[str, str]] = set()

        def add(type_: str, data: str, **pretty) -> None:
            data = data.strip()
            if not data or (type_, data) in seen:
                return
            seen.add((type_, data))
            out.append(self.emit(event, type_, data, **pretty))

        for match in self._URL_RE.finditer(body):
            value = match.group(1) or match.group(2)
            if not value:
                continue
            if value.startswith(("//", "http://", "https://")):
                absolute = value if value.startswith("http") else f"{base_parts.scheme}:{value}"
                add(URL_UNVERIFIED, absolute)
            elif value.startswith("/") and base_parts.netloc:
                add(URL_UNVERIFIED, f"{base_parts.scheme}://{base_parts.netloc}{value}")
            elif not value.startswith(("#", "mailto:", "javascript:", "data:")):
                add(URL_UNVERIFIED, value)

        for host in self._HOST_RE.findall(body):
            host = host.lower().rstrip(".")
            if len(host) <= 253:
                add(DNS_NAME, host)

        for email in _EMAIL_RE.findall(body):
            add(EMAIL_ADDRESS, email)

        if self._SECRET_RE.search(body):
            add(FINDING, f"possible secret exposed in {base}", severity="high")
        return out[:200]


class CertTransparency(BaseModule):
    """Passive subdomain discovery via a CT log aggregator."""

    name = "cert_transparency"
    watched_events = (DNS_NAME,)
    produced_events = (DNS_NAME,)
    scope_distance = 1

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        parent = event.data.lower()
        found = await ctx.client.cert_lookup(parent)
        return [
            self.emit(event, DNS_NAME, name.lower(), source="ct")
            for name in sorted({n.lower() for n in found if n.lower() != parent})
        ]


class PortScan(BaseModule):
    """Active module. Gated: requires ``RR_ALLOW_ACTIVE_MODULES`` *and* an
    ``active`` job, both checked by the engine before this ever runs."""

    name = "port_scan"
    watched_events = (IP_ADDRESS,)
    produced_events = (OPEN_TCP_PORT,)
    scope_distance = 1
    active = True
    DEFAULT_PORTS = (21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 443, 445,
                     1433, 1521, 3306, 3389, 5432, 5900, 6379, 8080, 8443, 9200, 27017)

    async def handle_event(self, event: Event, ctx: ModuleContext) -> list[Event]:
        out: list[Event] = []
        for port in self.DEFAULT_PORTS:
            if await ctx.client.port_probe(event.data, port, 0.5):
                out.append(self.emit(event, OPEN_TCP_PORT, f"{event.data}:{port}", port=port))
        return out


def build_preset(name: str, active: bool = False) -> list[BaseModule]:
    """Compose the module set for a preset.

    ``passive`` never opens a socket to the target (CT logs and DNS are
    third-party lookups); ``recon`` adds direct HTTP; ``active`` adds scanning.
    """
    modules: list[BaseModule] = [Speculate(), Extract(), CertTransparency()]
    if name in {"recon", "active"}:
        modules += [DNSResolve(), HTTPProbe()]
    if name == "active" and active:
        modules += [PortScan()]
    return modules
