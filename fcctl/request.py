"""Authenticated request builders (CentralClient, PilotClient), unwrap and typed errors."""
from __future__ import annotations

import contextvars
import re
import threading
from fcctl._sync import _io
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from .session_store import SessionStore

CENTRAL_BASE = "https://dashboard.frappe.io"
from ._version import USER_AGENT as UA  # noqa: E402


from .errors import (ApiError, AuthError, CloudflareChallenge, CsrfError, FcError, NotFound,  # noqa: E402,F401
                     RateLimited, ReauthFailed, TaskConflict, TaskFailed, ValidationError)


def _ids(r: httpx.Response) -> dict:
    return {"request_id": r.headers.get("x-request-id") or r.headers.get("x-frappe-request-id"),
            "cf_ray": r.headers.get("cf-ray")}


def is_cloudflare_challenge(r: httpx.Response) -> bool:
    if r.status_code not in (403, 429, 503):
        return False
    h = {k.lower(): v for k, v in r.headers.items()}
    cf = "cf-ray" in h or "cf-mitigated" in h or h.get("server", "").lower() == "cloudflare"
    html = "text/html" in h.get("content-type", "") or r.text[:200].lstrip().lower().startswith(("<!doctype", "<html"))
    return h.get("cf-mitigated") == "challenge" or (cf and html) or (
        html and "just a moment" in r.text[:2000].lower())


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return None


def _exc_type(body: Any) -> str:
    return str(body.get("exc_type", "")) if isinstance(body, dict) else ""


def _generic(r: httpx.Response, msg: str, body: Any, code: str | None = None) -> None:
    cls = {429: RateLimited, 417: ValidationError, 422: ValidationError, 409: TaskConflict}.get(r.status_code, ApiError)
    raise cls(msg, r.status_code, body, code=code, **_ids(r))


def raise_for_central(r: httpx.Response) -> Any:
    """Map a Central response to data or a typed exception."""
    if is_cloudflare_challenge(r):
        raise CloudflareChallenge("Cloudflare challenge", r.status_code, **_ids(r))
    body = _json(r)
    if r.status_code < 400:
        if isinstance(body, dict):
            return body["data"] if "data" in body else body.get("message", body)
        return body
    et, text = _exc_type(body), r.text[:500]
    if "CSRF" in et or "CSRF" in text[:300]:
        raise CsrfError(et or "csrf", r.status_code, body, **_ids(r))
    if r.status_code == 401 or et in ("AuthenticationError", "SessionExpiredError") or \
            (r.status_code == 403 and et == "" and "session" in text.lower()):
        raise AuthError(et or "unauthenticated", r.status_code, body, **_ids(r))
    if r.status_code == 404:
        raise NotFound(text, 404, body, **_ids(r))
    _generic(r, f"{r.status_code} {et} {text}", body)


def raise_for_pilot(r: httpx.Response) -> Any:
    if is_cloudflare_challenge(r):
        raise CloudflareChallenge("Cloudflare challenge", r.status_code, **_ids(r))
    body = _json(r)
    if r.status_code < 400:
        return body if body is not None else r.text
    code = ""
    if isinstance(body, dict):
        err = body.get("error")
        code = (err.get("code") if isinstance(err, dict) else err) or body.get("code") or ""
    if r.status_code == 401:
        raise AuthError(code or "unauthorized", 401, body, **_ids(r))
    if r.status_code == 404:
        raise NotFound(code or r.text[:200], 404, body, **_ids(r))
    if r.status_code == 409 and code in ("task_conflict", "migration_conflict", ""):
        raise TaskConflict(code or "conflict", 409, body, **_ids(r))
    _generic(r, f"{r.status_code} {code} {r.text[:300]}", body, code or None)


_DRY_RUN: contextvars.ContextVar[list | None] = contextvars.ContextVar("fcctl_dry_run", default=None)


class DryRunRecorder:
    """While active, non-GET requests are recorded instead of sent (used by --dry-run).

    State lives in a ContextVar, so it is per-context rather than process-global.
    """
    MAX = 10

    @classmethod
    def start(cls) -> None:
        _DRY_RUN.set([])

    @classmethod
    def stop(cls) -> list:
        out = _DRY_RUN.get() or []
        _DRY_RUN.set(None)
        return out

    @classmethod
    def active(cls) -> bool:
        return _DRY_RUN.get() is not None

    @classmethod
    def record(cls, method: str, path: str, params: Any, body: Any) -> bool:
        """True when the request was recorded (and must not be sent)."""
        reqs = _DRY_RUN.get()
        if reqs is None or method.upper() == "GET":
            return False
        if len(reqs) >= cls.MAX:
            raise RuntimeError("dry-run: too many requests recorded")
        reqs.append({"method": method.upper(), "path": path, "params": params, "body": body})
        return True


class CentralClient:
    """Central v2: {base}/api/v2/method/<dotted>; cookie sid + X-Frappe-CSRF-Token.

    `reauth` (fcctl.reauth.Reauther) is invoked once on Auth/Csrf errors. Cloudflare challenges are raised, never solved.
    """

    def __init__(self, store: SessionStore, team: str | None = None, base: str = CENTRAL_BASE,
                 http: httpx.Client | None = None, timeout: float = 60.0):
        self.store, self.team, self.base = store, team or store.data.get("team"), base.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout, headers={"User-Agent": UA})
        self.reauth: Any = None

    # -- build
    def path(self, dotted: str) -> str:
        return f"/api/v2/method/{dotted}"

    def build(self, method: str, dotted: str, params: dict | None = None, json: Any = None,
              team: bool = True) -> httpx.Request:
        q = dict(params or {})
        if team and self.team and "team" not in q and method.upper() == "GET":
            q["team"] = self.team
        body = dict(json) if isinstance(json, dict) else json
        if team and self.team and isinstance(body, dict) and method.upper() != "GET":
            body.setdefault("team", self.team)
        headers = {"Accept": "application/json"}
        if self.store.csrf:
            headers["X-Frappe-CSRF-Token"] = self.store.csrf
        cookies = {"sid": self.store.sid} if self.store.sid else None
        return self.http.build_request(method.upper(), self.base + self.path(dotted), params=q or None,
                                       json=body if method.upper() != "GET" else None,
                                       headers=headers, cookies=cookies)

    def _capture_cookie(self, r: httpx.Response) -> None:
        for c in r.headers.get_list("set-cookie"):
            m = re.match(r"sid=([^;]+)", c)
            if m and m.group(1) not in ("Guest", "") and m.group(1) != self.store.sid:
                self.store.set_central(sid=m.group(1))

    def _once(self, method, dotted, params, json, team):
        r = self.http.send(self.build(method, dotted, params, json, team))
        self._capture_cookie(r)
        return raise_for_central(r)

    def call(self, method: str, dotted: str, params: dict | None = None, json: Any = None,
             team: bool = True, _retried: bool = False) -> Any:
        try:
            return self._once(method, dotted, params, json, team)
        except (AuthError, CsrfError) as e:
            if _retried or not self.reauth:
                raise
            self.reauth.recover(scope="central", error=e)
            return self.call(method, dotted, params, json, team, _retried=True)

    def get(self, dotted, **params): return self.call("GET", dotted, params=params)
    def post(self, dotted, **body): return self.call("POST", dotted, json=body)


class PilotClient:
    """Pilot admin API on a server gateway: {gateway}/api/v1/*, Bearer JWT from get_bench_link."""

    def __init__(self, central: CentralClient, server: str, http: httpx.Client | None = None,
                 timeout: float = 120.0, max_age: float = 120.0, clock: Callable[[], float] = _io.now):
        self.max_age, self.clock = max_age, clock
        self.central, self.server = central, server
        self.store = central.store
        self.http = http or httpx.Client(timeout=timeout, headers={"User-Agent": UA})

    # Tokens live in memory only (shared per CentralClient), never in the SessionStore.
    def _cache(self) -> dict:
        c = self.central
        if not hasattr(c, "_pilot_tokens"):
            c._pilot_tokens = {}
            c._pilot_lock = threading.Lock()
            c._pilot_server_locks = {}
        return c._pilot_tokens

    def _server_lock(self) -> threading.Lock:
        self._cache()
        with self.central._pilot_lock:
            return self.central._pilot_server_locks.setdefault(self.server, threading.Lock())

    def _fresh(self, s: dict | None) -> bool:
        return bool(s) and (self.clock() - s["issued_at"]) < self.max_age

    def mint(self) -> dict:
        """central.api.sso.get_bench_link?server=<id> -> data.url = <gateway>/?sid=<JWT>. Single-flight."""
        with self._server_lock():
            return self._mint_locked()

    def _mint_locked(self) -> dict:
        data = self.central.call("GET", "central.api.sso.get_bench_link", params={"server": self.server},
                                 team=False)
        url = data["url"] if isinstance(data, dict) else data
        u = urlparse(url)
        token = parse_qs(u.query).get("sid", [None])[0]
        if not token:
            raise AuthError("bench link has no sid", body=data)
        s = {"token": token, "gateway": f"{u.scheme}://{u.netloc}", "issued_at": self.clock()}
        self._cache()[self.server] = s
        return s

    def session(self, force: bool = False) -> dict:
        """Lazy mint; re-mint when older than max_age. Concurrent callers share one mint."""
        s = self._cache().get(self.server)
        if not force and self._fresh(s):
            return s  # type: ignore[return-value]
        stale = s
        with self._server_lock():
            cur = self._cache().get(self.server)
            if cur is not stale and self._fresh(cur):  # another thread minted meanwhile
                return cur  # type: ignore[return-value]
            return self._mint_locked()

    def invalidate(self) -> None:
        self._cache().pop(self.server, None)

    def request(self, method: str, path: str, params: dict | None = None, json: Any = None,
                idempotency_key: str | None = None, raw: bool = False, _retried: bool = False) -> Any:
        s = self.session()
        headers = {"Accept": "application/json", "Authorization": f"Bearer {s['token']}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        r = self.http.request(method.upper(), f"{s['gateway']}/api/v1{path}", params=params, json=json,
                              headers=headers)
        try:
            return r if raw else raise_for_pilot(r)
        except AuthError:
            if _retried:
                raise
            self.invalidate()
            return self.request(method, path, params, json, idempotency_key, raw, _retried=True)

    def get(self, path, **params): return self.request("GET", path, params=params or None)
    def post(self, path, json=None, key: str | None = None):
        return self.request("POST", path, json=json if json is not None else {}, idempotency_key=key or str(uuid.uuid4()))
    def patch(self, path, json): return self.request("PATCH", path, json=json)
    def put(self, path, json): return self.request("PUT", path, json=json)
    def delete(self, path, **params): return self.request("DELETE", path, params=params or None)
