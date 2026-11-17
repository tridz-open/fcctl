"""Async SiteAdminClient: Administrator session on a site via a Pilot-minted login link.

Secrets (sid cookie, CSRF token, login-link token) live only in memory and are redacted from
errors and reprs.
"""
from __future__ import annotations

import hashlib
import json as _json
import re
import secrets
import warnings
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from ..errors import ApiError, AuthError, FcError, NotFound, redact
from .transport import UA, make_http

_CSRF_RX = re.compile(r"""csrf_token["']?\s*[:=]\s*["']([^"']+)["']""")
_URL_TOKEN_RX = re.compile(r"(?i)([?&](?:sid|key|token|login_token)=)[^&\s\"']+")


class SiteAdminError(ApiError):
    """Failure talking to a site as Administrator."""


def _scrub(text: Any) -> str:
    return _URL_TOKEN_RX.sub(r"\1***", redact(text))


def _err(msg: str, status: int | None = None, body: Any = None, cls=SiteAdminError) -> FcError:
    return cls(_scrub(msg), status=status, body=_scrub(body) if body is not None else None,
               what=_scrub(msg), why=f"HTTP {status}" if status else None)


class SiteAdminClient:
    def __init__(self, site: str, http: httpx.AsyncClient, base: str | None = None):
        self.site = site
        self.base = (base or f"https://{site}").rstrip("/")
        self.http = http
        self._csrf: str | None = None
        self._v2 = True

    def __repr__(self) -> str:
        return f"SiteAdminClient(site={self.site!r}, session=***)"

    __str__ = __repr__

    # -- session bootstrap -------------------------------------------------
    async def login(self, link: str) -> None:
        try:
            r = await self.http.get(link, follow_redirects=True)
        except httpx.HTTPError as e:
            raise _err(f"login link follow failed: {type(e).__name__}", cls=AuthError) from None
        if r.status_code >= 400 or "sid" not in self.http.cookies or \
                self.http.cookies.get("sid") in (None, "", "Guest"):
            raise _err("login link did not yield a session", r.status_code, cls=AuthError)
        self._csrf = await self._fetch_csrf(r)
        if not self._csrf:
            raise _err("no CSRF token in boot response", cls=AuthError)

    async def _fetch_csrf(self, first: httpx.Response) -> str | None:
        m = _CSRF_RX.search(first.text or "")
        if m:
            return m.group(1)
        for path in ("/app", "/api/method/frappe.sessions.get_boot"):
            r = await self.http.get(f"{self.base}{path}")
            m = _CSRF_RX.search(r.text or "")
            if m:
                return m.group(1)
            try:
                d = r.json()
                tok = ((d.get("message") or d) if isinstance(d, dict) else {}).get("csrf_token")
                if tok:
                    return tok
            except ValueError:
                pass
        return None

    # -- transport ---------------------------------------------------------
    async def _req(self, method: str, path: str, params: dict | None = None, json: Any = None) -> Any:
        headers = {"Accept": "application/json"}
        if method != "GET" and self._csrf:
            headers["X-Frappe-CSRF-Token"] = self._csrf
        try:
            r = await self.http.request(method, f"{self.base}{path}", params=params, json=json,
                                        headers=headers)
        except httpx.HTTPError as e:
            raise _err(f"{method} {path} failed: {type(e).__name__}") from None
        return r

    @staticmethod
    def _unwrap(r: httpx.Response, what: str) -> Any:
        if r.status_code in (401, 403):
            raise _err(f"{what}: session rejected", r.status_code, r.text[:300], cls=AuthError)
        if r.status_code == 404:
            raise _err(f"{what}: not found", 404, r.text[:300], cls=NotFound)
        if r.status_code >= 400:
            raise _err(f"{what}: HTTP {r.status_code}", r.status_code, r.text[:500])
        try:
            d = r.json()
        except ValueError:
            raise _err(f"{what}: non-JSON response", r.status_code) from None
        if isinstance(d, dict):
            if "data" in d:
                return d["data"]
            if "message" in d:
                return d["message"]
        return d

    async def _resource(self, method: str, doctype: str, name: str | None = None,
                        params: dict | None = None, json: Any = None) -> Any:
        tail = f"/{doctype}" + (f"/{name}" if name else "")
        what = f"{method} {doctype}" + (f"/{name}" if name else "")
        if self._v2:
            r = await self._req(method, f"/api/v2/resource{tail}", params, json)
            if r.status_code != 404:
                return self._unwrap(r, what)
        r = await self._req(method, f"/api/resource{tail}", params, json)
        if r.status_code != 404 and self._v2:
            self._v2 = False  # v2 unavailable on this site; stop probing it
        return self._unwrap(r, what)

    # -- public API --------------------------------------------------------
    async def get_list(self, doctype: str, filters: Any = None, fields: list[str] | None = None,
                       limit: int = 20) -> list:
        p: dict[str, Any] = {"limit": limit, "limit_page_length": limit}
        if filters is not None:
            p["filters"] = _json.dumps(filters)
        if fields:
            p["fields"] = _json.dumps(fields)
        return await self._resource("GET", doctype, params=p)

    async def get_doc(self, doctype: str, name: str) -> dict:
        return await self._resource("GET", doctype, name)

    async def insert(self, doctype: str, data: dict) -> dict:
        return await self._resource("POST", doctype, json=data)

    async def update(self, doctype: str, name: str, data: dict) -> dict:
        return await self._resource("PUT", doctype, name, json=data)

    async def delete(self, doctype: str, name: str) -> Any:
        return await self._resource("DELETE", doctype, name)

    async def call(self, method: str, **args: Any) -> Any:
        r = await self._req("POST", f"/api/method/{method}", json=args)
        return self._unwrap(r, f"call {method}")

    async def set_admin_password(self, password: str, user: str = "Administrator") -> None:
        """Set `user`'s password via `PUT /api/(v2/)resource/User/<user>` with `new_password`.

        Why this route: the session is Administrator (Pilot login link), and User.validate turns
        `new_password` into `update_password()` for System Managers without needing the old one.
        `frappe.core.doctype.user.user.update_password` requires the old password / reset key, so it
        is unusable here. The password is sent in the body only and never echoed back or logged.
        Note: this stamps `last_password_reset_date=today`; call `force_password_reset` AFTER it.
        """
        if not password:
            raise _err("set_admin_password: empty password")
        await self._resource("PUT", "User", user, json={"new_password": password})

    async def force_password_reset(self, user: str = "Administrator", days: int = 1) -> None:
        """Force `user` to change password at next login.

        Frappe has no per-user flag; its login flow redirects to the reset page when System Settings
        `force_user_to_reset_password` (days) > 0 and the user's `last_password_reset_date` is older
        than that. So: ensure the setting is >= `days` (left alone if already set) and backdate the
        user's `last_password_reset_date`.
        """
        cur = await self._resource("GET", "System Settings", "System Settings")
        if not int((cur or {}).get("force_user_to_reset_password") or 0):
            await self._resource("PUT", "System Settings", "System Settings",
                                 json={"force_user_to_reset_password": int(days)})
        await self._resource("PUT", "User", user, json={"last_password_reset_date": "2000-01-01"})

    async def create_password_setup_link(self, user: str = "Administrator",
                                         ensure_expiry_seconds: int = 86400) -> str:
        """Backward-compatible wrapper: returns only the URL (see `create_password_setup_link_ex`)."""
        return (await self.create_password_setup_link_ex(user, ensure_expiry_seconds))["url"]

    async def _site_time_zone(self, ss: dict) -> Any:
        """Resolve the site's time zone: System Settings value, else the site default, else UTC (warn)."""
        from zoneinfo import ZoneInfo
        for tz in (ss.get("time_zone"), None):
            if tz is None:
                try:
                    r = await self._req("GET", "/api/method/frappe.client.get_value",
                                        params={"doctype": "System Settings",
                                                "fieldname": "time_zone"})
                    d = self._unwrap(r, "get_value System Settings.time_zone")
                    tz = d.get("time_zone") if isinstance(d, dict) else None
                except FcError:
                    tz = None
            if tz:
                try:
                    return ZoneInfo(str(tz))
                except Exception:  # noqa: BLE001 - unknown tz name
                    pass
        warnings.warn(f"{self.site}: System Settings time_zone empty/unknown; using UTC for "
                      "the reset-link timestamp", RuntimeWarning, stacklevel=3)
        return timezone.utc

    async def create_password_setup_link_ex(self, user: str = "Administrator",
                                            max_expiry_seconds: int = 86400) -> dict:
        """Mint a one-time `https://<site>/update-password?key=<key>` link; return `{url, expires_in}`.

        Mirrors Frappe v16 `User._reset_password`: key = 56 hex chars (`frappe.generate_hash`), the
        User stores `reset_password_key = sha256(key)` and `last_reset_password_key_generated_on`
        (naive datetime in the site's System Settings time_zone). `_get_user_for_update_password`
        rejects it once System Settings `reset_password_link_expiry_duration` seconds pass, and
        `update_password` clears it after use (single use). The link life is capped: if the site
        value is 0 (never) or above `max_expiry_seconds`, it is set to the max. `expires_in` is the
        effective expiry in seconds. The plain key is never logged.
        """
        ss = await self._resource("GET", "System Settings", "System Settings") or {}
        cur = int(ss.get("reset_password_link_expiry_duration") or 0)
        eff = cur
        if max_expiry_seconds and (cur == 0 or cur > int(max_expiry_seconds)):
            eff = int(max_expiry_seconds)
            await self._resource("PUT", "System Settings", "System Settings",
                                 json={"reset_password_link_expiry_duration": eff})
        tz = await self._site_time_zone(ss)
        now = datetime.now(timezone.utc).astimezone(tz)
        key = secrets.token_hex(28)
        await self._resource("PUT", "User", user, json={
            "reset_password_key": hashlib.sha256(key.encode()).hexdigest(),
            "last_reset_password_key_generated_on": now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f"),
        })
        return {"url": f"https://{self.site}/update-password?key={key}", "expires_in": eff}

    async def ping(self) -> bool:
        try:
            r = await self._req("GET", "/api/method/ping")
        except FcError:
            return False
        return r.status_code == 200

    async def aclose(self) -> None:
        await self.http.aclose()


def _link_from(data: Any) -> str:
    if isinstance(data, dict):
        for k in ("url", "login_url", "link", "message"):
            if isinstance(data.get(k), str):
                return data[k]
        for v in data.values():
            if isinstance(v, dict):
                return _link_from(v)
    if isinstance(data, str) and data.startswith("http"):
        return data
    raise _err("Pilot login response had no link", cls=AuthError)


async def open_admin(client: Any, server: str, site: str, *, http: httpx.AsyncClient | None = None,
                     base: str | None = None) -> SiteAdminClient:
    """Mint a login link via Pilot and open an Administrator session.

    `client` is a PilotClient (has .post) or a facade exposing .pilot(server).
    """
    pilot = client if hasattr(client, "post") else client.pilot(server)
    data = await pilot.post(f"/sites/{site}/login")
    link = _link_from(data)
    u = urlparse(link)
    root = base or f"{u.scheme}://{u.netloc}"
    admin = SiteAdminClient(site, http or make_http(60.0, UA), base=root)
    try:
        await admin.login(link)
    except BaseException:
        await admin.aclose()
        raise
    return admin
