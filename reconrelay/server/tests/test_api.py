"""End-to-end HTTP surface, exactly as the browser extension calls it."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import status

from app.config import get_settings
from tests.conftest import API_KEY, JWT_SECRET


def wait_for_job(client, job_id, headers, timeout: float = 10.0) -> dict:
    """Poll until the inline worker reports a terminal state."""
    deadline = time.monotonic() + timeout
    body = {"job": {"status": "unknown"}}
    while time.monotonic() < deadline:
        body = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
        if body["job"]["status"] not in ("queued", "running"):
            return body
        time.sleep(0.02)
    raise AssertionError(
        f"job {job_id} never finished after {timeout}s; last state: {body['job']['status']}"
        f" error={body['job'].get('error')}"
    )


def submit(client, headers, **overrides) -> dict:
    payload = {
        "target": "https://example.com/dashboard",
        "page_url": "https://example.com/dashboard",
        "page_title": "Acme Portal",
        "harvested": {
            "urls": ["https://example.com/login", "https://thirdparty-analytics.net/x"],
            "endpoints": ["/api/v1/config"],
            "js_files": ["https://example.com/static/app.js"],
            "emails": ["support@example.com"],
            "cloud_assets": ["https://acme-assets.s3.amazonaws.com"],
        },
        "options": {"preset": "recon"},
        "client": "extension",
    }
    payload.update(overrides)
    return client.post("/v1/jobs", json=payload, headers=headers)


# ------------------------------------------------------------------- meta
def test_health_reports_configuration(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["store"] == "MemoryStore"
    assert body["scopes_configured"] == 1
    assert body["allow_any_scope"] is False
    assert body["active_modules_allowed"] is False


def test_root_points_at_docs(client):
    assert client.get("/").json()["docs"] == "/docs"


def _walk_routes(routes):
    """FastAPI nests included routers behind different attributes per version."""
    for route in routes:
        yield route
        for holder in (route, getattr(route, "original_router", None)):
            nested = getattr(holder, "routes", None) if holder is not None else None
            if nested:
                yield from _walk_routes(nested)


def test_openapi_schema_is_served(app, client):
    """HTTP routes are documented; WebSocket routes are not part of OpenAPI."""
    schema = client.get("/openapi.json").json()
    assert "/v1/jobs" in schema["paths"]
    assert "/v1/auth/token" in schema["paths"]
    assert "/v1/jobs/{job_id}/events" in schema["paths"]

    from starlette.routing import WebSocketRoute

    ws_paths = {
        route.path for route in _walk_routes(app.routes) if isinstance(route, WebSocketRoute)
    }
    assert "/v1/ws/jobs/{job_id}" in ws_paths


# ------------------------------------------------------------------- auth
def test_token_mints_a_usable_jwt(client):
    response = client.post("/v1/auth/token", json={"api_key": API_KEY, "operator": "alice"})
    assert response.status_code == 200
    claims = jwt.decode(
        response.json()["access_token"], JWT_SECRET, algorithms=["HS256"]
    )
    assert claims["sub"] == "alice"
    assert "scan:submit" in claims["scopes"]


def test_token_rejects_an_unknown_key(client):
    response = client.post("/v1/auth/token", json={"api_key": "wrong-key-9999"})
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_scan_requires_a_bearer_token(client):
    assert submit(client, {}).status_code == status.HTTP_401_UNAUTHORIZED
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert submit(client, bad).status_code == status.HTTP_401_UNAUTHORIZED


# ----------------------------------------------------------------- policy
def test_out_of_scope_target_is_refused(client, auth_headers):
    response = submit(client, auth_headers, target="https://acme-competitor.test/")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "authorized scope allowlist" in response.json()["detail"]


def test_active_scan_refused_when_disabled_server_side(client, auth_headers):
    response = submit(client, auth_headers, options={"preset": "active", "active": True})
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "active modules are disabled" in response.json()["detail"]


def test_unknown_preset_is_a_validation_error(client, auth_headers):
    response = submit(client, auth_headers, options={"preset": "yolo"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ----------------------------------------------------------------- happy path
def test_full_scan_round_trip(client, auth_headers):
    created = submit(client, auth_headers)
    assert created.status_code == status.HTTP_201_CREATED
    job_id = created.json()["job_id"]

    body = wait_for_job(client, job_id, auth_headers)
    assert body["job"]["status"] == "completed"
    assert body["job"]["target"] == "example.com"
    assert body["event_count"] > 10

    stats = body["job"]["stats"]
    assert stats["total_events"] == body["event_count"]
    assert stats.get("rejected_out_of_scope", 0) > 0, "third-party seeds were not filtered"

    events = client.get(f"/v1/jobs/{job_id}/events", headers=auth_headers).json()
    types = {event["type"] for event in events["events"]}
    data = {event["data"] for event in events["events"]}
    assert {"DNS_NAME", "HTTP_RESPONSE", "TECHNOLOGY"} <= types
    assert "support@example.com" in data


def test_harvested_third_party_seeds_never_become_targets(client, auth_headers):
    created = submit(client, auth_headers)
    job_id = created.json()["job_id"]
    wait_for_job(client, job_id, auth_headers)

    events = client.get(
        f"/v1/jobs/{job_id}/events", headers=auth_headers, params={"limit": 2000}
    ).json()
    for event in events["events"]:
        assert "thirdparty-analytics.net" not in str(event["data"])
        assert "s3.amazonaws.com" not in str(event["data"])


def test_event_pagination_advances(client, auth_headers):
    job_id = submit(client, auth_headers).json()["job_id"]
    wait_for_job(client, job_id, auth_headers)

    first = client.get(
        f"/v1/jobs/{job_id}/events", headers=auth_headers, params={"limit": 5}
    ).json()
    assert len(first["events"]) == 5
    assert first["after"] == 5

    second = client.get(
        f"/v1/jobs/{job_id}/events",
        headers=auth_headers,
        params={"limit": 5, "after": first["after"]},
    ).json()
    assert {event["id"] for event in first["events"]}.isdisjoint(
        {event["id"] for event in second["events"]}
    )


def test_persisted_events_carry_no_response_bodies(client, auth_headers):
    job_id = submit(client, auth_headers).json()["job_id"]
    wait_for_job(client, job_id, auth_headers)

    events = client.get(
        f"/v1/jobs/{job_id}/events", headers=auth_headers, params={"limit": 2000}
    ).json()["events"]
    responses = [event for event in events if event["type"] == "HTTP_RESPONSE"]
    assert responses, "expected at least one HTTP_RESPONSE event"
    for event in responses:
        assert "body" not in event["pretty"], "full body was persisted"
        assert "body_excerpt" in event["pretty"]
    assert any(event["pretty"]["body_bytes"] > 100 for event in responses), (
        "no response body was measured"
    )


def test_unknown_job_is_404(client, auth_headers):
    assert client.get("/v1/jobs/job_nope", headers=auth_headers).status_code == 404
    assert (
        client.get("/v1/jobs/job_nope/events", headers=auth_headers).status_code == 404
    )


def test_job_listing_and_cancel(client, auth_headers):
    job_id = submit(client, auth_headers).json()["job_id"]
    listing = client.get("/v1/jobs", headers=auth_headers).json()["jobs"]
    assert any(job["id"] == job_id for job in listing)

    cancelled = client.post(f"/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] in ("cancelled", "completed")


# ---------------------------------------------------------------- websocket
def test_websocket_delivers_the_full_stream(client, auth_headers, token):
    job_id = submit(client, auth_headers).json()["job_id"]
    seen_events: list[dict] = []

    with client.websocket_connect(f"/v1/ws/jobs/{job_id}?token={token}") as ws:
        first = ws.receive_json()
        assert first["kind"] == "job.state"
        assert first["job_id"] == job_id

        finished = None
        for _ in range(200):
            message = ws.receive_json()
            if message["kind"] == "events":
                seen_events.extend(message["events"])
            elif message["kind"] == "job.finished":
                finished = message
                break

    assert finished is not None, "stream never reported job.finished"
    assert finished["status"] == "completed"
    assert len(seen_events) > 5
    ids = [event["id"] for event in seen_events]
    assert len(ids) == len(set(ids)), "backfill and live tail overlapped without dedupe"


def test_websocket_rejects_an_invalid_token(client, auth_headers):
    job_id = submit(client, auth_headers).json()["job_id"]
    with pytest.raises(Exception):
        with client.websocket_connect(f"/v1/ws/jobs/{job_id}?token=garbage") as ws:
            ws.receive_json()


def test_websocket_rejects_an_unknown_job(client, token):
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/ws/jobs/job_nope?token=" + token) as ws:
            ws.receive_json()


# ------------------------------------------------------------------- config
def test_settings_csv_parsing_is_lenient():
    from app.config import Settings

    settings = Settings(
        authorized_scopes=" example.com, ,acme.test \n third.test",
        api_keys="a-key-1, b-key-2",
    )
    assert settings.scope_list == ["example.com", "acme.test", "third.test"]
    assert settings.api_key_set == {"a-key-1", "b-key-2"}


def test_settings_are_overridden_per_app(app, settings):
    """create_app(settings=...) must win over the process-global default."""
    from fastapi.testclient import TestClient

    with TestClient(app) as probe:
        assert probe.get("/health").json()["scopes_configured"] == len(settings.scope_list)
    assert app.dependency_overrides[get_settings]() is settings
