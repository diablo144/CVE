"""Token minting and verification.

The extension stores a long-lived API key in ``chrome.storage.local`` and
exchanges it for a short-lived JWT. Requests carry the JWT; the key never
travels on every call, so a leaked token expires rather than being permanent.
"""

from __future__ import annotations

import hmac
import time
from typing import Any

import jwt
from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings


class AuthError(Exception):
    pass


def mint_token(settings: Settings, operator: str, scopes: list[str] | None = None) -> tuple[str, int]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": operator,
        "iat": now,
        "exp": now + settings.jwt_ttl_seconds,
        "scopes": scopes or ["scan:submit", "scan:read", "scan:stream"],
        "typ": "extension",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    if isinstance(token, bytes):  # pyjwt < 2 returned bytes
        token = token.decode()
    return token, settings.jwt_ttl_seconds


def verify_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc


def check_api_key(settings: Settings, api_key: str) -> bool:
    """Constant-time compare so key probing cannot be timed."""
    return any(hmac.compare_digest(api_key, known) for known in settings.api_key_set)


def _extract(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


async def current_operator(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """FastAPI dependency: returns the operator name or raises 401."""
    token = _extract(authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = verify_token(settings, token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return str(claims.get("sub", "operator"))
