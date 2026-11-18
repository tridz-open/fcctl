"""Retry policy for idempotent HTTP requests: exponential backoff with full jitter.

Pure helpers shared by the async source (fcctl/_async) and the hand-written sync path
(fcctl/request.py). The actual sleeping is injected by the clients, so tests never wait.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Mapping

import httpx

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Retried on any network error / 5xx. PUT is idempotent by HTTP semantics.
RETRY_ANY_METHODS = SAFE_METHODS | {"PUT"}
# Errors where the request certainly never reached the server, so any method may be retried.
NEVER_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
NETWORK_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
                  httpx.PoolTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base: float = 0.5
    factor: float = 2.0
    cap: float = 30.0
    rng: Callable[[], float] = random.random

    def with_max_retries(self, n: int) -> "RetryPolicy":
        return replace(self, max_retries=max(0, int(n)))

    def backoff(self, attempt: int) -> float:
        """Full-jitter delay for the retry after `attempt` failed tries (1-based): U(0, min(cap, base*factor^(n-1)))."""
        ceiling = min(self.cap, self.base * (self.factor ** max(0, attempt - 1)))
        return self.rng() * ceiling

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Sleep before the next try. A server Retry-After wins, but never exceeds the cap."""
        if retry_after is not None:
            return min(self.cap, max(0.0, retry_after))
        return self.backoff(attempt)

    def can_retry(self, attempt: int) -> bool:
        return attempt <= self.max_retries


def from_env(env: Mapping[str, str] | None = None) -> RetryPolicy:
    raw = (env if env is not None else os.environ).get("FCCTL_MAX_RETRIES")
    p = RetryPolicy()
    if raw is None or str(raw).strip() == "":
        return p
    try:
        return p.with_max_retries(int(str(raw).strip()))
    except ValueError:
        return p


_DEFAULT: RetryPolicy | None = None


def default_policy() -> RetryPolicy:
    """Process default: set by the CLI (--max-retries), else FCCTL_MAX_RETRIES, else built-in."""
    return _DEFAULT if _DEFAULT is not None else from_env()


def set_default(policy: RetryPolicy | None) -> None:
    global _DEFAULT
    _DEFAULT = policy


def is_idempotent(method: str, idempotency_key: str | None = None) -> bool:
    """Safe to retry after the request may have reached the server (GET/HEAD/OPTIONS/PUT).

    An Idempotency-Key does not qualify: nothing guarantees the server deduplicates it.
    """
    return method.upper() in RETRY_ANY_METHODS


def retriable_error(method: str, exc: BaseException) -> bool:
    """Network errors: any method on never-sent errors, only idempotent methods otherwise."""
    if isinstance(exc, NEVER_SENT_ERRORS):
        return True
    return isinstance(exc, NETWORK_ERRORS) and is_idempotent(method)


def retriable_response(method: str, code: int) -> bool:
    """429 is retried for every method (the server rejected it); 5xx only for idempotent ones."""
    if code == 429:
        return True
    return retriable_status(code) and is_idempotent(method)


def retriable_status(code: int) -> bool:
    return code == 429 or (500 <= code <= 599 and code != 501)


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After as delta-seconds or HTTP-date -> seconds from now, or None if absent/invalid."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or datetime.now(timezone.utc))).total_seconds())


def annotate(err: Exception, attempts: int) -> None:
    """Add the attempt count to a typed error's why text when retries happened."""
    if attempts > 1 and hasattr(err, "why"):
        err.why = f"{err.why} (gave up after {attempts} attempts)"
