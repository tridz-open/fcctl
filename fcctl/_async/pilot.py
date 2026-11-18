"""Async PilotClient: Bearer JWT minted via Central get_bench_link, cached in memory, single-flight."""
from __future__ import annotations

import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .. import retry as _retry
from ..request import AuthError, DryRunRecorder, FcError, is_cloudflare_challenge, raise_for_pilot
from . import _io
from .central import CentralClient
from .transport import UA, make_http


def seg(value: Any) -> str:
    """Percent-encode one URL path segment (slashes and spaces included)."""
    return quote(str(value), safe="")


class PilotClient:
    def __init__(self, central: CentralClient, server: str, http: httpx.AsyncClient | None = None,
                 timeout: float = 120.0, max_age: float = 120.0, clock: Callable[[], float] = _io.now,
                 retry: _retry.RetryPolicy | None = None, sleep=None):
        self.retry, self.sleep = retry, sleep
        self.max_age, self.clock = max_age, clock
        self.central, self.server = central, server
        self.store = central.store
        self.http = http or make_http(timeout, UA)

    def _cache(self) -> dict:
        c: Any = self.central  # per-central cache attributes are attached dynamically
        if not hasattr(c, "_pilot_tokens"):
            c._pilot_tokens = {}
            c._pilot_server_locks = {}
        return c._pilot_tokens

    def _server_lock(self):
        self._cache()
        central: Any = self.central
        return central._pilot_server_locks.setdefault(self.server, _io.Lock())

    def _fresh(self, s: dict | None) -> bool:
        return s is not None and bool(s) and (self.clock() - s["issued_at"]) < self.max_age

    async def mint(self) -> dict:
        async with self._server_lock():
            return await self._mint_locked()

    async def _mint_locked(self) -> dict:
        data = await self.central.call("GET", "central.api.sso.get_bench_link",
                                       params={"server": self.server}, team=False)
        url = data["url"] if isinstance(data, dict) else data
        u = urlparse(url)
        token = parse_qs(u.query).get("sid", [None])[0]
        if not token:
            raise AuthError("bench link has no sid", body=data)
        s = {"token": token, "gateway": f"{u.scheme}://{u.netloc}", "issued_at": self.clock()}
        self._cache()[self.server] = s
        return s

    async def session(self, force: bool = False) -> dict:
        s = self._cache().get(self.server)
        if not force and self._fresh(s):
            return s  # type: ignore[return-value]
        async with self._server_lock():
            cur = self._cache().get(self.server)
            if cur is not s and self._fresh(cur):
                return cur  # type: ignore[return-value]
            return await self._mint_locked()

    def invalidate(self) -> None:
        self._cache().pop(self.server, None)

    async def request(self, method: str, path: str, params: dict | None = None, json: Any = None,
                      idempotency_key: str | None = None, raw: bool = False, _retried: bool = False) -> Any:
        if DryRunRecorder.record(method, path, params, json):
            return {}
        s = await self.session()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {s['token']}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        url = f"{s['gateway']}/api/v1{path}"
        idem = _retry.is_idempotent(method, idempotency_key)
        policy = self.retry or _retry.default_policy()
        attempt = 0
        while True:
            attempt += 1
            try:
                r = await self.http.request(method.upper(), url, params=params, json=json, headers=headers)
            except _retry.NETWORK_ERRORS:
                if not (idem and policy.can_retry(attempt)):
                    raise
                await (_io.sleep if self.sleep is None else self.sleep)(policy.delay(attempt))
                continue
            if (_retry.retriable_status(r.status_code) and idem
                    and policy.can_retry(attempt) and not is_cloudflare_challenge(r)):
                ra = _retry.parse_retry_after(r.headers.get("retry-after"))
                await (_io.sleep if self.sleep is None else self.sleep)(policy.delay(attempt, ra))
                continue
            break
        try:
            return r if raw else raise_for_pilot(r)
        except AuthError:
            if _retried:
                raise
            self.invalidate()
            return await self.request(method, path, params, json, idempotency_key, raw, _retried=True)
        except FcError as e:
            _retry.annotate(e, attempt)
            raise

    async def get(self, path, **params): return await self.request("GET", path, params=params or None)
    async def post(self, path, json=None, key: str | None = None):
        return await self.request("POST", path, json=json if json is not None else {},
                                  idempotency_key=key or str(uuid.uuid4()))
    async def patch(self, path, json): return await self.request("PATCH", path, json=json)
    async def put(self, path, json): return await self.request("PUT", path, json=json)
    async def delete(self, path, **params): return await self.request("DELETE", path, params=params or None)

    async def aclose(self) -> None:
        await self.http.aclose()
