"""The recursive event loop: this is the core of the whole tool."""

from __future__ import annotations

import pytest

from app.engine import ScanEngine, seed_list_from_payload
from app.models import Event, HarvestedSurface, Job, SeedPayload
from app.modules.base import BaseModule
from app.policy import ScanPolicy
from tests.conftest import FakeNetwork


def make_engine(
    network, scopes=("example.com",), active=False, max_events=500, **kwargs
) -> ScanEngine:
    policy = ScanPolicy(scopes=list(scopes), allow_active_modules=active)
    return ScanEngine(policy=policy, client=network.as_client(), max_events=max_events, **kwargs)


def job_for(target="example.com", preset="recon", active=False, seeds=None) -> Job:
    return Job(target=target, seeds=seeds or [target], preset=preset, active=active, max_depth=4)


async def test_recursive_loop_walks_the_graph(network):
    """Seed -> speculate -> fetch -> extract -> subdomains, all in one pass."""
    engine = make_engine(network)
    result = await engine.run(job_for())

    types = {event.type for event in result.events}
    data = {event.data for event in result.events}

    # passive discovery
    assert "api.example.com" in data, "CT subdomain discovery did not feed the graph"
    assert "dev.example.com" in data
    # DNS tier
    assert "93.184.216.34" in data
    assert "IP_ADDRESS" in types
    assert "DNS_NAME_UNRESOLVED" in types  # dev.example.com has no A record
    # HTTP tier
    assert "HTTP_RESPONSE" in types
    assert any("nginx" in item for item in data), "server header not fingerprinted"
    # extraction tier
    assert "support@example.com" in data
    assert "https://api.example.com/v1/users" in data
    assert "https://example.com/api/v1/config" in data, "inline-JS endpoint not extracted"
    # findings tier
    assert "FINDING" in types
    assert result.stats["total_events"] == len(result.events)


async def test_every_event_is_unique(network):
    engine = make_engine(network)
    result = await engine.run(job_for())
    keys = [event.key for event in result.events]
    assert len(keys) == len(set(keys)), "dedup on (type, data) failed"


async def test_out_of_scope_links_are_refused_not_followed(network):
    engine = make_engine(network)
    result = await engine.run(job_for())
    rejected = " ".join(result.rejected_out_of_scope)

    assert "thirdparty-analytics.net" in rejected
    assert "acme-internal.io" not in {event.data for event in result.events}
    for url in network.fetched:
        assert "thirdparty-analytics.net" not in url
        assert "acme-internal.io" not in url
    assert "acme-internal.io" not in network.resolved


async def test_resolved_ip_of_an_in_scope_host_stays_in_scope(network):
    """Regression: a domain-scoped allowlist must not drop its own A records.

    Without parent inheritance, ``example.com -> 93.184.216.34`` was rejected
    because the literal IP does not match the string ``example.com``, which
    silently killed DNS resolution and every module downstream of it.
    """
    engine = make_engine(network)
    result = await engine.run(job_for())

    assert "93.184.216.34" in {event.data for event in result.events}
    assert "93.184.216.34" not in result.rejected_out_of_scope
    assert "93.184.216.35" not in result.rejected_out_of_scope


async def test_emails_outside_scope_are_kept_but_not_followed(network):
    """An address is intel, not a scan target — but its domain must not be."""
    engine = make_engine(network)
    result = await engine.run(job_for())

    assert "admin@acme-internal.io" in {event.data for event in result.events}
    assert "acme-internal.io" not in {event.data for event in result.events}
    assert "acme-internal.io" not in network.resolved


async def test_passive_preset_never_touches_the_target(network):
    engine = make_engine(network)
    result = await engine.run(job_for(preset="passive"))

    assert network.fetched == [], "passive scan made HTTP requests"
    assert network.probed == [], "passive scan opened sockets"
    assert "HTTP_RESPONSE" not in {event.type for event in result.events}
    assert network.ct_lookups, "passive scan should still use certificate transparency"


async def test_port_scan_requires_server_side_authorisation(network):
    engine = make_engine(network, active=False)
    await engine.run(job_for(preset="active", active=True))
    assert network.probed == [], "active modules ran without server-side permission"

    engine = make_engine(network, active=True)
    result = await engine.run(job_for(preset="active", active=True))
    assert ("93.184.216.34", 443) in network.probed
    assert "OPEN_TCP_PORT" in {event.type for event in result.events}


async def test_depth_limit_stops_recursion():
    network = FakeNetwork(ct={})
    engine = make_engine(network, max_depth=1)
    result = await engine.run(job_for())
    assert {event.type for event in result.events} == {"DNS_NAME", "URL_UNVERIFIED", "IP_ADDRESS"}
    assert max(event.depth for event in result.events) == 1


async def test_event_ceiling_truncates_rather_than_hanging():
    network = FakeNetwork()
    engine = make_engine(network, max_events=5)
    result = await engine.run(job_for())
    assert len(result.events) <= 5
    assert result.truncated is True


async def test_unauthorised_seed_never_enters_the_queue(network):
    engine = make_engine(network)
    result = await engine.run(job_for(seeds=["example.com", "evil.example"]))
    assert "evil.example" in result.rejected_out_of_scope
    assert "evil.example" not in network.resolved


async def test_a_broken_module_cannot_kill_the_scan(network):
    class Boom(BaseModule):
        name = "boom"
        watched_events = ("DNS_NAME",)
        produced_events = ()
        scope_distance = 3

        async def handle_event(self, event, ctx):
            raise RuntimeError("module exploded")

    engine = make_engine(network)
    original = engine.modules_for
    engine.modules_for = lambda job: original(job) + [Boom()]

    result = await engine.run(job_for())
    assert result.stats.get("module_error", 0) >= 1
    assert result.stats["total_events"] > 0, "scan died instead of skipping the broken module"


def test_seeds_come_from_the_extension_payload():
    payload = SeedPayload(
        target="https://example.com/",
        harvested=HarvestedSurface(
            urls=["https://example.com/a"],
            endpoints=["/api/v1/x"],
            js_files=["https://example.com/app.js"],
            emails=["someone@elsewhere.org"],  # must not become a seed
        ),
    )
    seeds = seed_list_from_payload(payload, payload.target)
    assert seeds[0] == "https://example.com/"
    assert "https://example.com/app.js" in seeds
    assert all("elsewhere.org" not in seed for seed in seeds)


def test_slim_drops_response_bodies_before_persistence():
    event = Event(type="HTTP_RESPONSE", data="https://example.com", pretty={"body": "x" * 5000})
    slim = event.slim()
    assert "body" not in slim.pretty
    assert slim.pretty["body_bytes"] == 5000
    assert len(slim.pretty["body_excerpt"]) == 400
    assert "body" in event.pretty, "original event must keep the body for extraction"
