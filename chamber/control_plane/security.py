"""Admin-token authentication for the server-rendered control plane."""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from urllib.parse import unquote

from fastapi import Request

ADMIN_TOKEN_ENV = "AMPULE_CHAMBER_ADMIN_TOKEN"
SECURE_COOKIES_ENV = "AMPULE_CHAMBER_SECURE_COOKIES"
SESSION_COOKIE = "ampule_session"
TOKEN_PREFIX = "op_live_"
MIN_TOKEN_LENGTH = 24


@dataclass(frozen=True)
class AdminAuth:
    """Digest-only authentication state derived from the configured token."""

    token_digest: bytes | None
    session_value: str | None
    secure_cookies: bool

    @property
    def enabled(self) -> bool:
        return self.token_digest is not None

    def authenticates_token(self, candidate: str | None) -> bool:
        if not self.enabled or not candidate:
            return False
        digest = hashlib.sha256(candidate.encode("utf-8")).digest()
        return hmac.compare_digest(digest, self.token_digest or b"")

    def authenticates_request(self, request: Request) -> bool:
        if not self.enabled:
            return True
        cookie = request.cookies.get(SESSION_COOKIE)
        if cookie and self.session_value and hmac.compare_digest(cookie, self.session_value):
            return True
        authorization = request.headers.get("Authorization", "")
        scheme, _, candidate = authorization.partition(" ")
        return scheme.lower() == "bearer" and self.authenticates_token(candidate.strip())


def load_admin_auth(admin_token: str | None = None) -> AdminAuth:
    """Load and validate the Relayna-style operator token configuration."""

    raw = os.environ.get(ADMIN_TOKEN_ENV) if admin_token is None else admin_token
    token = raw.strip() if raw else ""
    secure_cookies = _environment_bool(SECURE_COOKIES_ENV, default=False)
    if not token:
        return AdminAuth(None, None, secure_cookies)
    if not token.startswith(TOKEN_PREFIX) or len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(
            f"{ADMIN_TOKEN_ENV} must start with {TOKEN_PREFIX!r} and contain at least "
            f"{MIN_TOKEN_LENGTH} characters"
        )
    token_digest = hashlib.sha256(token.encode("utf-8")).digest()
    session_value = hmac.new(
        token.encode("utf-8"),
        b"ampule-chamber-control-plane-session-v1",
        hashlib.sha256,
    ).hexdigest()
    return AdminAuth(token_digest, session_value, secure_cookies)


def safe_next_path(value: str | None) -> str:
    """Keep post-login redirects on the current control-plane origin."""

    decoded = unquote(value) if value else ""
    if (
        not value
        or not value.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(character) < 32 for character in decoded)
    ):
        return "/runs"
    return value


def _environment_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
