"""Authorisation policy: the guard that keeps this a scanner and not a weapon.

The extension is untrusted input. It runs in a browser that visits arbitrary
sites, so anything it POSTs could be attacker-shaped. This module is the only
thing deciding whether a target may be scanned, and it decides on the server.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class PolicyViolation(Exception):
    """Raised when a requested target is outside the operator's allowlist."""

    def __init__(self, target: str, reason: str) -> None:
        super().__init__(f"{reason}: {target!r}")
        self.target = target
        self.reason = reason


def normalise_target(raw: str) -> str:
    """Reduce a URL / host / CIDR-ish string to the thing we authorise on."""
    text = raw.strip().lower()
    if not text:
        raise PolicyViolation(raw, "empty target")
    if "://" not in text:
        candidate = text
    else:
        candidate = urlparse(text).hostname or ""
    if not candidate:
        raise PolicyViolation(raw, "could not extract a hostname")
    return candidate.split(":")[0]


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def is_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.match(value))


def host_matches_scope(host: str, scope: str) -> bool:
    """``host`` is covered by ``scope`` (a domain, wildcard domain, or CIDR)."""
    host = host.lower().strip().rstrip(".")
    scope = scope.lower().strip().rstrip(".")

    if scope.startswith("*."):
        scope = scope[2:]

    # CIDR / single-IP scope
    if "/" in scope or is_ip(scope):
        try:
            network = ipaddress.ip_network(scope, strict=False)
        except ValueError:
            return False
        try:
            return ipaddress.ip_address(host) in network
        except ValueError:
            return False

    # Domain scope: exact match or any subdomain of it.
    return host == scope or host.endswith(f".{scope}")


class ScanPolicy:
    """Answers 'may this be scanned?' for every seed and every derived event."""

    def __init__(
        self,
        scopes: list[str] | None = None,
        allow_any_scope: bool = False,
        allow_active_modules: bool = False,
        max_depth: int = 4,
    ) -> None:
        self.scopes = [s for s in (scopes or []) if s]
        self.allow_any_scope = allow_any_scope
        self.allow_active_modules = allow_active_modules
        self.max_depth = max_depth

    # ------------------------------------------------------------- authorise
    def is_authorized(self, raw_target: str) -> bool:
        try:
            host = normalise_target(raw_target)
        except PolicyViolation:
            return False
        return self.in_scope(host)

    def in_scope(self, host: str) -> bool:
        host = (host or "").lower().rstrip(".")
        if not host:
            return False
        if self.allow_any_scope:
            return True
        return any(host_matches_scope(host, scope) for scope in self.scopes)

    def assert_authorized(self, raw_target: str) -> str:
        """Return the normalised host, or raise ``PolicyViolation``."""
        host = normalise_target(raw_target)
        if not self.in_scope(host):
            reason = (
                "no scopes configured (set RR_AUTHORIZED_SCOPES)"
                if not self.scopes and not self.allow_any_scope
                else "target is not in the authorized scope allowlist"
            )
            raise PolicyViolation(raw_target, reason)
        if not (is_domain(host) or is_ip(host)):
            raise PolicyViolation(raw_target, "target is neither a valid domain nor an IP")
        return host

    def filter_seeds(self, seeds: list[str]) -> tuple[list[str], list[str]]:
        """Split derived seeds into (allowed, rejected) without raising."""
        allowed: list[str] = []
        rejected: list[str] = []
        for seed in seeds:
            try:
                host = normalise_target(seed)
            except PolicyViolation:
                rejected.append(seed)
                continue
            (allowed if self.in_scope(host) else rejected).append(seed)
        return allowed, rejected

    # -------------------------------------------------------------- modules
    def assert_active_allowed(self) -> None:
        if not self.allow_active_modules:
            raise PolicyViolation(
                "active",
                "active modules are disabled server-side (RR_ALLOW_ACTIVE_MODULES)",
            )
