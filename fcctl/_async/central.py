"""Async CentralClient (cookie sid + CSRF). Errors/unwrap come from fcctl.request."""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..request import AuthError, CsrfError, DryRunRecorder, raise_for_central
from ..session_store import SessionStore
from .transport import CENTRAL_BASE, UA, make_http


class CentralClient:
    def __init__(self, store: SessionStore, team: str | None = None, base: str = CENTRAL_BASE,
                 http: httpx.AsyncClient | None = None, timeout: float = 60.0):
        self.store, self.team, self.base = store, team or store.data.get("team"), base.rstrip("/")
        self.http = http or make_http(timeout, UA)
        self.reauth: Any = None

    def path(self, dotted: str) -> str:
        return f"/api/v2/method/{dotted}"

    def _team_q(self, method, params, team):
        q = dict(params or {})
        if team and self.team and "team" not in q and method.upper() == "GET":
            q["team"] = self.team
        return q

    def _team_body(self, method, json, team):
        body = dict(json) if isinstance(json, dict) else json
        if team and self.team and isinstance(body, dict) and method.upper() != "GET":
            body.setdefault("team", self.team)
        return body

    def build(self, method: str, dotted: str, params: dict | None = None, json: Any = None,
              team: bool = True) -> httpx.Request:
        q = self._team_q(method, params, team)
        body = self._team_body(method, json, team)
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

    async def _once(self, method, dotted, params, json, team):
        r = await self.http.send(self.build(method, dotted, params, json, team))
        self._capture_cookie(r)
        return raise_for_central(r)

    async def call(self, method: str, dotted: str, params: dict | None = None, json: Any = None,
                   team: bool = True, _retried: bool = False) -> Any:
        if DryRunRecorder.record(method, dotted, params, json):
            return {}
        try:
            return await self._once(method, dotted, params, json, team)
        except (AuthError, CsrfError) as e:
            if _retried or not self.reauth:
                raise
            self.reauth.recover(scope="central", error=e)
            return await self.call(method, dotted, params, json, team, _retried=True)

    async def logout_remote(self) -> bool:
        """Best-effort server-side logout (Frappe /api/method/logout) with the stored sid.

        Returns True if the server accepted it, False if there was no sid or the call failed.
        Never raises on network/HTTP errors and never exposes the sid.
        """
        if not self.store.sid:
            return False
        headers = {"Accept": "application/json", "Cookie": f"sid={self.store.sid}"}
        if self.store.csrf:
            headers["X-Frappe-CSRF-Token"] = self.store.csrf
        try:
            r = await self.http.send(self.http.build_request("GET", self.base + "/api/method/logout", headers=headers))
        except httpx.HTTPError:
            return False
        return r.status_code < 400

    async def get(self, dotted, **params): return await self.call("GET", dotted, params=params)
    async def post(self, dotted, **body): return await self.call("POST", dotted, json=body)

    async def aclose(self) -> None:
        await self.http.aclose()
