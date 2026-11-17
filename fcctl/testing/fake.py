"""In-memory simulation of Frappe Cloud Central (/api/v2/method/*) and Pilot (/api/v1/*).

Deliberately simple: canned state transitions + fault hooks. Not a mini Frappe Cloud.
"""
from __future__ import annotations

import base64
import json as _json
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from fcctl._sync._io import sleep as _io_sleep

CENTRAL_HOST = "dashboard.frappe.io"
GATEWAY = "https://admin-fake.gw.example.com"
CF_HTML = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>Checking your browser</body></html>"


class FakeClock:
    """Callable monotonic-ish clock; advance() to expire tokens deterministically."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, secs: float) -> float:
        self.now += secs
        return self.now


@dataclass
class Call:
    method: str
    host: str
    path: str
    params: dict
    body: Any
    headers: dict
    status: int = 0

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class _Fault:
    match: Any
    status: int
    body: Any
    times: int | None
    delay: float
    headers: dict


def _resp(status: int, body: Any = None, headers: dict | None = None, text: str | None = None) -> httpx.Response:
    if text is not None:
        return httpx.Response(status, text=text, headers=headers)
    return httpx.Response(status, json=body, headers=headers)


def _data(d: Any, status: int = 200) -> httpx.Response:
    return _resp(status, {"data": d})


def _central_err(status: int, exc_type: str, msg: str = "") -> httpx.Response:
    return _resp(status, {"exc_type": exc_type, "message": msg or exc_type})


def _pilot_err(status: int, code: str, msg: str = "") -> httpx.Response:
    return _resp(status, {"error": {"code": code, "message": msg or code}})


class FakeFrappeCloud:
    def __init__(self, tmp_dir: Path | str | None = None, task_polls: int = 2, email: str = "user@example.com",
                 sleep: Callable[[float], None] | None = None):
        self._tmp = Path(tmp_dir) if tmp_dir else Path(tempfile.mkdtemp(prefix="fcctl-fake-"))
        self.clock = FakeClock()
        self.calls: list[Call] = []
        self.faults: list[_Fault] = []
        self.email, self.team = email, "TEAM-1"
        self.sid, self.csrf = "sid-fake", "csrf-fake"
        self.valid_sids = {self.sid}
        self.login_code = "123456"
        self.sent_codes: list[str] = []
        self.task_polls = task_polls  # polls before a task turns success (pending, running, success)
        self.sleep = sleep or _io_sleep
        self.gateway = GATEWAY
        self.tokens: set[str] = set()
        self.servers: dict[str, dict] = {}
        self._request_keys: dict[str, str] = {}
        self.snapshots: dict[str, dict] = {}
        self.buckets: dict[str, dict] = {}
        self.notifications = [
            {"name": "N-1", "title": "Welcome", "read": 0},
            {"name": "N-2", "title": "Invoice ready", "read": 0}]
        self.invoices = [{"name": "INV-1", "status": "Paid", "total": 10.0}]
        self.trust_tier = {"tier": "verified", "level": 2}
        self.balance = 25.0
        self.sites: dict[str, dict] = {}
        self.apps: list[dict] = [{"name": "frappe"}, {"name": "erpnext"}]
        self.tasks: dict[str, dict] = {}
        self._idem: dict[str, dict] = {}
        self.backups: dict[str, list[dict]] = {}
        self.uploads: dict[str, dict] = {}
        self.logs = {"web.log": "line1\nline2\nline3\n"}
        self._http: httpx.Client | None = None

    # ---------------------------------------------------------------- wiring
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(transport=self.transport, headers={"User-Agent": "fcctl-fake"})
        return self._http

    def close(self) -> None:
        if self._http:
            self._http.close()

    def store(self, authed: bool = True):
        from cryptography.fernet import Fernet
        from ..session_store import SessionStore
        s = SessionStore(self._tmp / f"s-{uuid.uuid4().hex[:6]}.enc", Fernet.generate_key())
        if authed:
            s.set_central(sid=self.sid, csrf=self.csrf)
        return s

    def central(self, store=None, team: str | None = None):
        from ..request import CentralClient
        return CentralClient(store or self.store(), team=team or self.team, http=self.http())

    def pilot(self, server: str = "server-fake", central=None):
        from ..request import PilotClient
        return PilotClient(central or self.central(), server, http=self.http(), clock=self.clock)

    # ---------------------------------------------------------------- faults
    def inject(self, match, status: int = 500, body: Any = None, times: int | None = 1, delay: float = 0.0,
               headers: dict | None = None) -> None:
        """Fault for requests matching `match` (substring of 'METHOD path', compiled regex, or callable(Call)).
        times=None means forever. body str -> raw text; dict/list -> JSON; None -> {}."""
        self.faults.append(_Fault(match, status, body, times, delay, headers or {}))

    def inject_auth_expired(self, match="/api/v2/method/", times=1):
        self.inject(match, 401, {"exc_type": "AuthenticationError", "message": "session expired"}, times)

    def inject_csrf(self, match="/api/v2/method/", times=1):
        self.inject(match, 400, {"exc_type": "CSRFTokenError", "message": "Invalid CSRF Token"}, times)

    def inject_conflict(self, match="/api/v1/", times=1):
        self.inject(match, 409, {"error": {"code": "task_conflict", "message": "task running"}}, times)

    def inject_rate_limit(self, match="/api/", times=1):
        self.inject(match, 429, {"error": {"code": "rate_limited"}}, times, headers={"Retry-After": "1"})

    def inject_cloudflare(self, match="/api/v2/method/", times=1):
        self.inject(match, 403, CF_HTML, times, headers={"cf-ray": "abc123-BOM", "server": "cloudflare",
                                                         "content-type": "text/html"})

    def fail_task(self, task_id: str, reason: str = "injected failure") -> None:
        self.tasks[task_id].update(forced="failed", failure=reason)

    def expire_session(self) -> None:
        self.valid_sids.clear()

    def _matches(self, f: _Fault, c: Call) -> bool:
        m = f.match
        if callable(m):
            return bool(m(c))
        if hasattr(m, "search"):
            return bool(m.search(c.key))
        return str(m) in c.key

    # ---------------------------------------------------------------- handler
    def _handle(self, req: httpx.Request) -> httpx.Response:
        body: Any = None
        if req.content:
            try:
                body = _json.loads(req.content)
            except ValueError:
                body = req.content
        c = Call(req.method, req.url.host, req.url.path, dict(req.url.params), body,
                 {k.lower(): v for k, v in req.headers.items()})
        self.calls.append(c)
        for f in list(self.faults):
            if self._matches(f, c):
                if f.times is not None:
                    f.times -= 1
                    if f.times <= 0:
                        self.faults.remove(f)
                if f.delay:
                    self.sleep(f.delay)
                c.status = f.status
                if isinstance(f.body, str):
                    return httpx.Response(f.status, text=f.body, headers=f.headers)
                return httpx.Response(f.status, json=f.body if f.body is not None else {}, headers=f.headers)
        r = self._central(req, c) if c.host == CENTRAL_HOST else self._pilot(req, c)
        c.status = r.status_code
        return r

    # ---------------------------------------------------------------- central
    def _central(self, req: httpx.Request, c: Call) -> httpx.Response:
        m = re.match(r"/api/v2/method/(.+)$", c.path)
        if not m:
            return httpx.Response(404, text="not found")
        fn = m.group(1)
        body = c.body if isinstance(c.body, dict) else {}
        q = c.params
        if fn == "central.api.auth.send_code":
            self.sent_codes.append(body.get("email", ""))
            return _data({"sent": True})
        if fn == "central.api.auth.verify_code":
            if str(body.get("code")) != self.login_code:
                return _central_err(401, "AuthenticationError", "invalid code")
            self.valid_sids.add(self.sid)
            return httpx.Response(200, json={"data": {"csrf_token": self.csrf, "email": body.get("email")}},
                                  headers={"set-cookie": f"sid={self.sid}; Path=/; HttpOnly"})
        cookie = c.headers.get("cookie", "")
        sm = re.search(r"sid=([^;]+)", cookie)
        if not sm or sm.group(1) not in self.valid_sids:
            return _central_err(401, "AuthenticationError", "not logged in")
        if req.method != "GET" and c.headers.get("x-frappe-csrf-token") != self.csrf:
            return _central_err(400, "CSRFTokenError", "Invalid CSRF Token")
        if fn == "central.api.identity.my_profile":
            return _data({"email": self.email, "team": self.team, "teams": [self.team]})
        if fn == "central.api.sso.get_bench_link":
            tok = self._jwt(q.get("server", "server-fake"))
            self.tokens.add(tok)
            return _data({"url": f"{self.gateway}/?sid={tok}"})
        if fn == "central.api.servers.registry":
            return _data({"servers": list(self.servers.values())})
        if fn == "central.api.servers.server_overview":
            s = self.servers.get(q.get("resource_id"))
            return _data(s) if s else _central_err(404, "DoesNotExistError", "no such server")
        if fn == "central.api.servers.action_status":
            return _data({"name": q.get("name"), "status": "Success"})
        if fn == "central.api.servers.create_server":
            key = body.get("request_key")
            if key and key in self._request_keys:
                return _data(self.servers[self._request_keys[key]])
            rid = f"srv-{len(self.servers) + 1:03d}"
            s = {"resource_id": rid, "name": rid, "title": body.get("title"), "plan": body.get("plan"),
                 "region": body.get("region"), "status": "Pending", "_polls": 0}
            self.servers[rid] = s
            if key:
                self._request_keys[key] = rid
            return _data(s)
        act = {"start_server": "Running", "restart_server": "Running", "stop_server": "Stopped"}
        short = fn.rsplit(".", 1)[-1]
        if fn.startswith("central.api.servers.") and short in act:
            s = self.servers.get(body.get("resource_id"))
            if not s:
                return _central_err(404, "DoesNotExistError", "no such server")
            s["status"] = act[short]
            return _data({"resource_id": s["resource_id"], "status": s["status"]})
        if short == "terminate_server":
            self.servers.pop(body.get("resource_id"), None)
            return _data({"terminated": True})
        if fn.startswith("central.api.snapshots."):
            return self._snapshots(short, body, q)
        if fn.startswith("central.services.api.storage."):
            return self._storage(short, body, q)
        if fn.startswith("central.billing."):
            return self._billing(short, body)
        if fn.startswith("central.api.notifications."):
            return self._notifications(short, body)
        return _central_err(404, "DoesNotExistError", f"unknown method {fn}")

    def _snapshots(self, fn: str, body: dict, q: dict) -> httpx.Response:
        if fn == "take_snapshot":
            n = f"snap-{len(self.snapshots) + 1:03d}"
            s = {"name": n, "resource_id": body.get("resource_id"), "title": body.get("title"),
                 "status": "Completed", "keep": 0}
            self.snapshots[n] = s
            return _data(s)
        if fn == "list_snapshots":
            rid = q.get("resource_id")
            return _data([s for s in self.snapshots.values() if not rid or s["resource_id"] == rid])
        if fn == "keep_snapshot":
            self.snapshots[body["name"]]["keep"] = 1
            return _data({"name": body["name"], "keep": 1})
        if fn == "delete_snapshots":
            for n in body.get("names", []):
                self.snapshots.pop(n, None)
            return _data({"deleted": body.get("names", [])})
        return _central_err(404, "DoesNotExistError", fn)

    def _storage(self, fn: str, body: dict, q: dict) -> httpx.Response:
        if fn == "get_object_storage":
            return _data({"buckets": list(self.buckets.values())})
        if fn == "create_bucket":
            n = body["bucket_name"]
            if n in self.buckets:
                return _central_err(417, "ValidationError", "bucket exists")
            b = {"name": n, "region": body.get("region", "bom"), "endpoint": "https://s3.fake.example",
                 "access_key": "AKFAKE" + uuid.uuid4().hex[:8], "secret_key": uuid.uuid4().hex}
            self.buckets[n] = {k: v for k, v in b.items() if k != "secret_key"}
            return _data(b)
        name = body.get("name") or q.get("name")
        if name not in self.buckets:
            return _central_err(404, "DoesNotExistError", "no such bucket")
        if fn == "delete_bucket":
            self.buckets.pop(name)
            return _data({"deleted": name})
        if fn == "get_bucket_usage":
            return _data({"name": name, "used_bytes": 0, "object_count": 0})
        if fn == "list_objects":
            return _data({"objects": [], "next_offset": None})
        if fn in ("get_upload_url", "get_object_url"):
            return _data({"url": f"https://s3.fake.example/{name}/{body.get('key') or q.get('key')}?sig=x"})
        return _data({"name": name})

    def _billing(self, fn: str, body: dict) -> httpx.Response:
        if fn == "get_trust_tier":
            return _data(self.trust_tier)
        if fn == "get_eligible_plans":
            return _data([{"name": "pilot-small", "price": 5}])
        if "balance" in fn:
            return _data({"balance": self.balance, "currency": "USD"})
        if "overview" in fn:
            return _data({"balance": self.balance, "trust_tier": self.trust_tier, "invoices_due": 0})
        if "invoice" in fn:
            return _data(self.invoices)
        return _data({})

    def _notifications(self, fn: str, body: dict) -> httpx.Response:
        if fn in ("mark_read", "mark_as_read", "read"):
            for n in self.notifications:
                if not body.get("name") or n["name"] == body["name"]:
                    n["read"] = 1
            return _data({"ok": True})
        return _data(self.notifications)

    def _jwt(self, server: str) -> str:
        def b(d): return base64.urlsafe_b64encode(_json.dumps(d).encode()).rstrip(b"=").decode()
        exp = int(self.clock() + 3600)
        return f"{b({'alg': 'HS256', 'typ': 'JWT'})}.{b({'server': server, 'exp': exp, 'n': uuid.uuid4().hex[:6]})}.fake"

    # ---------------------------------------------------------------- pilot
    def new_task(self, kind: str, **extra) -> dict:
        tid = f"task-{len(self.tasks) + 1:04d}"
        t = {"task_id": tid, "type": kind, "status": "pending", "_polls": 0, **extra}
        self.tasks[tid] = t
        return t

    def _public_task(self, t: dict) -> dict:
        return {k: v for k, v in t.items() if not k.startswith("_") and k != "forced"}

    def _advance(self, t: dict) -> None:
        if t["status"] in ("success", "failed", "killed"):
            return
        if t.get("forced"):
            t["status"] = t["forced"]
            return
        t["_polls"] += 1
        t["status"] = "success" if t["_polls"] >= self.task_polls else "running"
        if t["status"] == "success":
            self._complete(t)

    def _complete(self, t: dict) -> None:
        if t["type"] == "site_create":
            self.sites[t["site"]]["status"] = "active"
        elif t["type"] == "backup":
            self.backups[t["site"]].append({"timestamp": t["timestamp"], "status": "success",
                                            "files": [{"id": "database", "name": "db.sql.gz"}]})

    def _accept(self, c: Call, t: dict) -> httpx.Response:
        return _resp(202, {"task_id": t["task_id"], "status": t["status"]})

    def _pilot(self, req: httpx.Request, c: Call) -> httpx.Response:
        auth = c.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return _pilot_err(401, "unauthorized")
        m = re.match(r"/api/v1(/.*)$", c.path)
        if not m:
            return httpx.Response(404, text="not found")
        path, meth, body = m.group(1), c.method, (c.body if isinstance(c.body, dict) else {})
        if meth == "POST" and ("/actions/" in path or path in ("/sites", "/apps") or path.endswith("/backups")):
            key = c.headers.get("idempotency-key")
            if key and key in self._idem:
                return self._accept(c, self._idem[key])
            running = [t for t in self.tasks.values() if t["status"] in ("pending", "running")
                       and t.get("site") and t["site"] == self._site_of(path, body)]
            if running and path != "/sites":
                return _pilot_err(409, "task_conflict", "another task is running for this site")
        if path == "/sites" and meth == "GET":
            return _resp(200, {"sites": list(self.sites.values())})
        if path == "/sites" and meth == "POST":
            name = body.get("name", "")
            if name in self.sites:
                return _pilot_err(409, "site_exists")
            self.sites[name] = {"name": name, "status": "pending", "apps": body.get("apps", []),
                                "maintenance_mode": 0, "pause_scheduler": 0}
            self.backups.setdefault(name, [])
            return self._start(c, self.new_task("site_create", site=name))
        if path == "/sites/wildcard-domains":
            return _resp(200, {"domains": ["fake.frappe.cloud"]})
        sm = re.match(r"/sites/([^/]+)(/.*)?$", path)
        if sm:
            return self._site(c, sm.group(1), sm.group(2) or "", body)
        if path == "/apps" and meth == "GET":
            return _resp(200, {"apps": self.apps})
        if path == "/apps" and meth == "POST":
            self.apps.append({"name": body.get("name") or body.get("repo")})
            return self._start(c, self.new_task("app_add"))
        if path == "/apps/fetch" and meth == "POST":
            return self._start(c, self.new_task("fetch_all_app_updates", _output=_json.dumps(
                {a["name"]: {"has_update": False} for a in self.apps}) + "\n"))
        om = re.match(r"/tasks/([^/]+)/output/content$", path)
        if om and meth == "GET":
            t = self.tasks.get(om.group(1))
            return _resp(200, {"content": t.get("_output", "")}) if t else _pilot_err(404, "not_found")
        if path == "/tasks" and meth == "GET":
            return _resp(200, {"tasks": [self._public_task(t) for t in self.tasks.values()]})
        tm = re.match(r"/tasks/([^/]+)(/events)?$", path)
        if tm:
            t = self.tasks.get(tm.group(1))
            if not t:
                return _pilot_err(404, "not_found")
            if tm.group(2):
                self._advance(t)
                lines = [f"data: {_json.dumps({'status': t['status'], 'task_id': t['task_id']})}\n\n"]
                return httpx.Response(200, text="".join(lines), headers={"content-type": "text/event-stream"})
            self._advance(t)
            return _resp(200, self._public_task(t))
        if path == "/logs":
            return _resp(200, {"logs": [{"name": n, "has_errors": False} for n in self.logs]})
        lm = re.match(r"/logs/([^/]+)/(content|events)$", path)
        if lm and lm.group(1) in self.logs:
            if lm.group(2) == "content":
                return httpx.Response(200, text=self.logs[lm.group(1)])
            ev = "".join(f"data: {_json.dumps({'line': ln})}\n\n" for ln in self.logs[lm.group(1)].splitlines())
            return httpx.Response(200, text=ev, headers={"content-type": "text/event-stream"})
        if path == "/health":
            return _resp(200, {"status": "ok"})
        if path in ("/metrics", "/monitor/status", "/system"):
            return _resp(200, {"cpu": 1.0, "memory": 20.0, "disk": 30.0})
        if path == "/uploads" and meth == "POST":
            uid = f"up-{len(self.uploads) + 1:03d}"
            self.uploads[uid] = {"upload_id": uid, "chunks": {}}
            return _resp(201, {"upload_id": uid})
        um = re.match(r"/uploads/([^/]+)(/.*)?$", path)
        if um and um.group(1) in self.uploads:
            if meth == "PUT":
                self.uploads[um.group(1)]["chunks"][c.params.get("offset", "0")] = c.body
            return _resp(200, {"upload_id": um.group(1), "ok": True})
        return _pilot_err(404, "not_found", f"unknown {meth} {path}")

    def _site_of(self, path: str, body: dict) -> str | None:
        m = re.match(r"/sites/([^/]+)", path)
        return m.group(1) if m else None

    def _start(self, c: Call, t: dict) -> httpx.Response:
        key = c.headers.get("idempotency-key")
        if key:
            self._idem[key] = t
        return self._accept(c, t)

    def _site(self, c: Call, name: str, rest: str, body: dict) -> httpx.Response:
        s = self.sites.get(name)
        if s is None:
            return _pilot_err(404, "site_not_found")
        meth = c.method
        if rest == "" and meth == "GET":
            return _resp(200, s)
        if rest == "" and meth == "DELETE":
            t = self.new_task("site_drop", site=name)
            self.sites.pop(name)
            return self._start(c, t)
        if rest == "/configuration" and meth == "PATCH":
            for k in ("maintenance_mode", "pause_scheduler"):
                if k in body:
                    s[k] = int(body[k])
            return _resp(200, {k: s[k] for k in ("maintenance_mode", "pause_scheduler")})
        if rest == "/backups" and meth == "POST":
            ts = f"20260101_{len(self.backups[name]) + 1:06d}"
            return self._start(c, self.new_task("backup", site=name, timestamp=ts))
        if rest == "/backups" and meth == "GET":
            return _resp(200, {"backups": self.backups[name]})
        bm = re.match(r"/backups/([^/]+)(/download-links)?$", rest)
        if bm:
            b = next((x for x in self.backups[name] if x["timestamp"] == bm.group(1)), None)
            if not b:
                return _pilot_err(404, "backup_not_found")
            if bm.group(2):
                return _resp(200, {"links": {f["id"]: f"https://s3.fake.example/{name}/{f['name']}?sig=x"
                                             for f in b["files"]}})
            return _resp(200, b)
        if rest == "/apps" and meth == "POST":
            s["apps"].append(body.get("app") or body.get("repo"))
            return self._start(c, self.new_task("app_install", site=name))
        if rest == "/login" and meth == "POST":
            return _resp(201, {"url": f"https://{name}/app?sid=fake-login"})
        if rest.startswith("/actions/") and meth == "POST":
            return self._start(c, self.new_task(rest.split("/")[-1].replace("-", "_"), site=name))
        return _pilot_err(404, "not_found", f"unknown {meth} /sites/{name}{rest}")


class FakeSiteAdmin:
    """In-memory stand-in for SiteAdminClient's password helpers. Never stores the password in `calls`."""

    def __init__(self, site: str = "site.fake"):
        self.site = site
        self.calls: list[tuple] = []
        self._passwords: dict[str, str] = {}
        self.force_user_to_reset_password = 0
        self.last_password_reset_date: dict[str, str] = {}
        self.reset_password_keys: dict[str, str] = {}
        self.closed = False

    def set_admin_password(self, password: str, user: str = "Administrator") -> None:
        if not password:
            raise ValueError("set_admin_password: empty password")
        self.calls.append(("set_admin_password", user))
        self._passwords[user] = password
        self.last_password_reset_date[user] = "today"

    def force_password_reset(self, user: str = "Administrator", days: int = 1) -> None:
        self.calls.append(("force_password_reset", user))
        if not self.force_user_to_reset_password:
            self.force_user_to_reset_password = int(days)
        self.last_password_reset_date[user] = "2000-01-01"

    def create_password_setup_link(self, user: str = "Administrator", ensure_expiry_seconds: int = 86400) -> str:
        import hashlib
        import secrets
        key = secrets.token_hex(28)
        self.calls.append(("create_password_setup_link", user))
        self.reset_password_keys[user] = hashlib.sha256(key.encode()).hexdigest()
        return f"https://{self.site}/update-password?key={key}"

    def check_password(self, user: str, password: str) -> bool:
        return self._passwords.get(user) == password

    def close(self) -> None:
        self.closed = True

    def __repr__(self) -> str:
        return f"FakeSiteAdmin(site={self.site!r})"
