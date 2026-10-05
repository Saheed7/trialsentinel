"""Shared async HTTP client for external data sources: rate limiting, retries, timeouts."""

from __future__ import annotations

import asyncio
import time
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity.wait import wait_base

from trialsentinel.core.logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class AsyncRateLimiter:
    """Spaces requests evenly so a source's rate limit is never exceeded."""

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next_slot > now:
                await asyncio.sleep(self._next_slot - now)
                now = time.monotonic()
            self._next_slot = max(now, self._next_slot) + self._interval


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return isinstance(exc, httpx.TransportError)


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    log.warning("http_retry", attempt=state.attempt_number, error=repr(exc))


class SourceHTTPClient:
    """One instance per source. Retries only transient failures (429, 5xx, network)."""

    def __init__(
        self,
        base_url: str,
        *,
        rate_per_sec: float,
        timeout_s: float,
        max_retries: int,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            transport=transport,
            follow_redirects=True,
        )
        self._limiter = AsyncRateLimiter(rate_per_sec)
        self._max_retries = max_retries
        self._retry_wait = retry_wait or wait_exponential_jitter(initial=1, max=30)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=self._retry_wait,
            retry=retry_if_exception(_is_retryable),
            before_sleep=_log_retry,
            reraise=True,
        ):
            with attempt:
                await self._limiter.acquire()
                response = await self._client.get(path, params=params)
                response.raise_for_status()
                return response.json()
        raise RuntimeError("unreachable: retry loop exited without a result")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
