"""Real network client: httpx + dnspython.

Kept in one place so it is trivial to replace with a mock in tests, and so all
outbound traffic shares one timeout / user-agent / TLS policy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from urllib.parse import quote

import dns.resolver
import httpx

from .modules.base import Client, FetchResult

log = logging.getLogger("reconrelay.net")


async def http_fetch(url: str, client: httpx.AsyncClient) -> FetchResult | None:
    try:
        response = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        log.debug("fetch failed %s: %s", url, exc)
        return None
    body = response.text or ""
    return response.status_code, dict(response.headers), body


async def dns_resolve(name: str) -> list[str]:
    def _resolve() -> list[str]:
        out: list[str] = []
        for rdtype in ("A", "AAAA"):
            try:
                out.extend(str(rr) for rr in dns.resolver.resolve(name, rdtype, lifetime=5))
            except Exception:
                continue
        return out

    try:
        return await asyncio.to_thread(_resolve)
    except Exception as exc:  # pragma: no cover - resolver edge cases
        log.debug("resolve failed %s: %s", name, exc)
        return []


async def tcp_probe(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError, ValueError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def crtsh_lookup(domain: str, client: httpx.AsyncClient) -> list[str]:
    """Certificate-transparency subdomain discovery via crt.sh (passive)."""
    url = f"https://crt.sh/?q={quote(f'%.{domain}')}&output=json"
    try:
        response = await client.get(url)
        response.raise_for_status()
        rows = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.debug("crt.sh lookup failed for %s: %s", domain, exc)
        return []
    names: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        for chunk in str(row.get("name_value", "")).splitlines():
            chunk = chunk.strip().lower().lstrip("*.")
            if chunk and chunk.endswith(domain) and "*" not in chunk:
                names.add(chunk)
    return sorted(names)


def build_client(timeout: float = 10.0, user_agent: str = "ReconRelay/1.0") -> Client:
    """Wire the real network primitives into a :class:`Client`."""
    http_client = httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": user_agent},
        verify=ssl.create_default_context(),
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=8),
    )

    async def fetch(url: str) -> FetchResult | None:
        return await http_fetch(url, http_client)

    async def cert_lookup(domain: str) -> list[str]:
        return await crtsh_lookup(domain, http_client)

    return Client(fetch=fetch, resolve=dns_resolve, port_probe=tcp_probe, cert_lookup=cert_lookup)
