"""API-key authentication.

Only SHA-256 hashes of keys are configured (TS_API_KEYS_SHA256, comma-separated), so a leaked
config file does not leak usable keys. With no keys configured the API fails closed (503)."""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic_settings import BaseSettings, SettingsConfigDict

API_KEY_HEADER = "X-API-Key"
KEY_PREFIX = "ts_"

_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


class ApiSettings(BaseSettings):
    """API settings, read from environment variables only (prefix TS_API_)."""

    model_config = SettingsConfigDict(env_prefix="TS_API_", extra="ignore")

    keys_sha256: str = ""
    judge_model: str = "qwen2.5:7b"
    max_queued_audits: int = 20
    max_stored_audits: int = 500

    def key_hashes(self) -> frozenset[str]:
        return frozenset(h.strip().lower() for h in self.keys_sha256.split(",") if h.strip())


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def _is_valid(candidate: str, hashes: frozenset[str]) -> bool:
    digest = hash_key(candidate)
    # compare against every hash, so timing does not reveal which one (if any) matched
    return any([hmac.compare_digest(digest, h) for h in hashes])


def require_api_key(request: Request, key: Annotated[str | None, Depends(_api_key_header)]) -> str:
    """FastAPI dependency: returns a short, non-secret key fingerprint for logging."""
    hashes = request.app.state.api_settings.key_hashes()
    if not hashes:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication is not configured")
    if not key or not _is_valid(key, hashes):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid API key",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )
    return hash_key(key)[:8]


ApiKey = Annotated[str, Depends(require_api_key)]
