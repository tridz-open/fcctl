"""Contract guard: every Pilot request fcctl can make must exist in the Pilot admin dashboard's own API.

The fixture `fixtures/pilot_dashboard_contract.json` is extracted from the dashboard's public JS bundle
(scripts/extract_pilot_contract.py, `just contract-refresh`); `fixtures/pilot_contract_overrides.json` adds the
body/query keys the extractor cannot see. No network is used here.
"""
import asyncio
import contextlib
import inspect
import json
from pathlib import Path

import httpx
import pytest

from fcctl import domains, taskops
from fcctl._async import apps, backups, logs, monitor, sites, tasks, transfer
from fcctl._async.pilot import PilotClient
from fcctl.testing import fake_fc  # noqa: F401

FIX = Path(__file__).parent / "fixtures"
CONTRACT = json.loads((FIX / "pilot_dashboard_contract.json").read_text())
OVERRIDES = json.loads((FIX / "pilot_contract_overrides.json").read_text())

# fcctl calls the dashboard does not make (or the fixture cannot see), each with why and when it was checked.
FCCTL_ONLY_ALLOWLIST = {
    ("DELETE", "/apps/{}"): "P8: remove_app route is not in the dashboard bundle and is unverified live; "
                            "open until probed (2026-11-18)",
    ("GET", "/sites/{}/backups/{}"): "verified live 2026-11-18; the dashboard only uses the list and download routes",
}
# Extra keys fcctl sends beyond the dashboard's, per route: {(method, path): {"body"|"query": {key: reason}}}.
EXTRA_KEYS = {
    ("POST", "/sites/{}/login"): {"body": {"hint": "optional login hint, verified live 2026-11-18"}},
    ("POST", "/sites/{}/actions/restore"): {"body": {"backup_urls": "S3 download-links restore, unverified (2026-11-18)"}},
    ("PUT", "/sites/{}/uploads/{}/files/{}"): {"query": {"offset": "chunked upload resume offset (2026-11-18)"}},
}


def _key(method, path):
    return f"{method} {path}"


INDEX = {(e["method"], e["path"]): e for e in CONTRACT}
VOCAB = ({s for _, p in INDEX for s in p.split("/")} | {s for _, p in FCCTL_ONLY_ALLOWLIST for s in p.split("/")}) - {"", "{}"}


TEMPLATES = sorted({p for _, p in INDEX} | {p for _, p in FCCTL_ONLY_ALLOWLIST}, key=lambda p: -p.count("/"))


def normalize(path: str) -> str:
    """Map a concrete path to its contract template ({} for each variable segment), preferring the template
    with the most literal segments; paths no template matches fall back to vocabulary-based replacement."""
    segs = path.split("?")[0].strip("/").split("/")
    best, score = None, -1
    for t in TEMPLATES:
        ts = t.strip("/").split("/")
        if len(ts) == len(segs) and all(x == "{}" or x == y for x, y in zip(ts, segs)):
            lit = sum(x != "{}" for x in ts)
            if lit > score:
                best, score = t, lit
    return best or "/" + "/".join(x if x in VOCAB else "{}" for x in segs)


def lookup(method: str, path: str):
    if (method, path) in INDEX:
        return INDEX[(method, path)]
    if method == "GET" or (method == "PUT" and path == "/sites/{}/uploads/{}/files/{}"):
        return INDEX.get(("GET*", path))  # SSE / download / upload URL builders
    return None


def allowed_keys(entry, kind: str, method: str, path: str):
    """Allowed body/query keys for a route, or None when anything is allowed."""
    raw = entry[f"{kind}_keys"]
    ov = OVERRIDES[f"{kind}_keys"].get(_key(entry["method"], path))
    keys = set(ov) if ov is not None else set(raw or [])
    if "<dynamic>" in keys and ov is None:
        return None
    keys.discard("<dynamic>")
    if "*" in keys:
        return None
    return keys | set(EXTRA_KEYS.get((method, path), {}).get(kind, {}))


def violations(calls):
    out, used = [], set()
    for method, path, body, query in calls:
        e = lookup(method, path)
        if e is None:
            if (method, path) in FCCTL_ONLY_ALLOWLIST:
                used.add((method, path))
            else:
                out.append(f"{method} {path}: not in the dashboard contract or FCCTL_ONLY_ALLOWLIST")
            continue
        for kind, got in (("body", body), ("query", query)):
            ok = allowed_keys(e, kind, method, path)
            if ok is not None and not set(got) <= ok:
                out.append(f"{method} {path}: {kind} keys {sorted(set(got) - ok)} not in contract {sorted(ok)}")
    return out, used


# ---------------------------------------------------------------------------------------------- recorder
def _canned(method: str, path: str, params):
    n = normalize(path)
    if n == "/sites/{}/backups/{}":
        return {"files": [{"filename": "a-database.sql.gz", "kind": "database", "size_bytes": 5},
                          {"filename": "a-files.tar", "kind": "public-file", "size_bytes": 5},
                          {"filename": "a-private-files.tar", "kind": "private-file", "size_bytes": 5}]}
    if n == "/sites/{}/backups/{}/download-links":
        return {"links": {"database": "https://x/d", "public": "https://x/u", "private": "https://x/p"}}
    if n == "/sites/{}/uploads" and method == "POST":
        return {"upload_id": "u1"}
    if n == "/sites/{}/backup-schedule" and method == "GET":
        return {"retention": {"daily": 3}, "schedule": None}
    if n == "/logs/{}":
        return {"filename": "f", "lines": ["a", "b"]}
    if n == "/tasks/{}":
        return {"task_id": "t", "status": "success"}
    if n == "/sites/{}/login":
        return {"url": "https://x"}
    if n == "/git/repository-resolutions":
        return {"name": "myapp"}
    if n == "/marketplace/apps":
        return [{"name": "myapp", "repo": "https://github.com/o/myapp", "branch": "version-16", "is_installable": True}]
    if n == "/tasks/{}/output/content":
        return {"content": ""}
    return {"task_id": "t"}


class _Stream:
    status_code = 200

    def raise_for_status(self): ...

    async def aiter_lines(self):
        yield 'data: {"line": "x"}'

    async def aiter_bytes(self):
        yield b"abc"


class RecordingHTTP:
    def __init__(self, calls):
        self.calls = calls

    async def request(self, method, url, params=None, json=None, content=None, headers=None, timeout=None):
        path = "/" + url.split("/api/v1/", 1)[1]
        self.calls.append((method.upper(), normalize(path), sorted(json or {}), sorted(params or {})))
        if method.upper() == "GET" and "/files/" in path:
            return httpx.Response(206, content=b"x" * 5)
        if method.upper() == "PUT":
            return httpx.Response(200, json={})
        return httpx.Response(200, json=_canned(method.upper(), path, params))

    @contextlib.asynccontextmanager
    async def stream(self, method, url, params=None, **kw):
        path = "/" + url.split("/api/v1/", 1)[1]
        self.calls.append((method.upper(), normalize(path), [], sorted(params or {})))
        yield _Stream()

    async def aclose(self): ...


class RecordingPilot(PilotClient):
    """Real PilotClient with request() and the raw http client replaced by recorders; nothing leaves the process."""

    def __init__(self, calls):
        class _C:
            store = None
        super().__init__(_C(), "server-x", http=RecordingHTTP(calls))
        self.calls = calls

    async def session(self, force=False):
        return {"token": "t", "gateway": "https://gw.invalid", "issued_at": 0}

    async def request(self, method, path, params=None, json=None, idempotency_key=None, raw=False, _retried=False):
        self.calls.append((method.upper(), normalize(path), sorted(json or {}) if isinstance(json, dict) else [],
                           sorted(params or {})))
        payload = _canned(method.upper(), path, params)
        if raw:
            return httpx.Response(200, text="log text", request=httpx.Request(method, "https://gw.invalid"))
        return payload


class SyncRecordingPilot:
    """Stand-in for the sync PilotClient used by fcctl.domains / fcctl.taskops."""

    def __init__(self, calls):
        self.calls = calls

    def _rec(self, method, path, params):
        self.calls.append((method, normalize(path), [], sorted(params or {})))
        return {}

    def get(self, path, **params): return self._rec("GET", path, params)
    def delete(self, path, **params): return self._rec("DELETE", path, params)


class StubCentral:
    async def call(self, *a, **k): return {}


# ---------------------------------------------------------------------------------------------- table
S, A, B, TS, F = "s.example.com", "myapp", "20260101_000000", "t1", "f.log"
SPEC = [{"repo": "https://github.com/o/myapp", "branch": "main"}, {"name": "myapp"}]
# key -> list of (runner(p, dst, tmp), expects_requests); runner may return an awaitable or async iterator.
CASES = {
    "sites.list_sites": [lambda p, d, t: sites.list_sites(p)],
    "sites.get_site": [lambda p, d, t: sites.get_site(p, S)],
    "sites.wildcard_domains": [lambda p, d, t: sites.wildcard_domains(p)],
    "sites.create_site": [lambda p, d, t: sites.create_site(p, S, ["erpnext"])],
    "sites.clone_site": [lambda p, d, t: sites.clone_site(p, S, "n.example.com")],
    "sites.install_app": [lambda p, d, t: sites.install_app(p, S, app=A)],
    "sites.uninstall_app": [lambda p, d, t: sites.uninstall_app(p, S, A, force=True, remove_from_bench=True)],
    "sites.set_maintenance": [lambda p, d, t: sites.set_maintenance(p, S, True)],
    "sites.clear_cache": [lambda p, d, t: sites.clear_cache(p, S)],
    "sites.migrate": [lambda p, d, t: sites.migrate(p, S)],
    "sites.build_assets": [lambda p, d, t: sites.build_assets(p, S)],
    "sites.login_link": [lambda p, d, t: sites.login_link(p, S, hint="h")],
    "sites.drop_site": [lambda p, d, t: sites.drop_site(p, S, no_backup=True)],
    "sites.rename_site": [lambda p, d, t: sites.rename_site(p, S, "n.example.com", True)],
    "sites.config_get": [lambda p, d, t: sites.config_get(p, S)],
    "sites.config_set": [lambda p, d, t: sites.config_set(p, S, maintenance_mode=1)],
    "sites.site_apps": [lambda p, d, t: sites.site_apps(p, S)],
    "apps.install_order": [lambda p, d, t: _pure(apps.install_order(["hrms"]))],
    "apps.list_apps": [lambda p, d, t: apps.list_apps(p)],
    "apps.add_marketplace": [lambda p, d, t: apps.add_marketplace(p, A, [S]),
                             lambda p, d, t: apps.add_marketplace(p, A)],
    "apps.add_repo": [lambda p, d, t: apps.add_repo(p, "https://github.com/o/myapp", "main"),
                      lambda p, d, t: apps.add_repo(p, "https://github.com/o/myapp", "main", sites=[S], name=A)],
    "apps.add_in_order": [lambda p, d, t: apps.add_in_order(p, SPEC)],
    "apps.check_updates": [lambda p, d, t: apps.check_updates(p)],
    "apps.check_updates_result": [lambda p, d, t: apps.check_updates_result(p, TS)],
    "apps.update_apps": [lambda p, d, t: apps.update_apps(p, [A], True)],
    "apps.switch_branch": [lambda p, d, t: apps.switch_branch(p, A, "main")],
    "apps.remove_app": [lambda p, d, t: apps.remove_app(p, A)],
    "apps.remove": [lambda p, d, t: apps.remove(p, A)],
    "backups.create": [lambda p, d, t: backups.create(p, S)],
    "backups.list_backups": [lambda p, d, t: backups.list_backups(p, S, limit=5)],
    "backups.get": [lambda p, d, t: backups.get(p, S, B)],
    "backups.download_links": [lambda p, d, t: backups.download_links(p, S, B)],
    "backups.restore": [lambda p, d, t: backups.restore(p, S, source_site="o.example.com", backup_timestamp=B,
                                                        upload_id="u1", skip_failing_patches=True)],
    "backups.get_schedule": [lambda p, d, t: backups.get_schedule(p, S)],
    "backups.set_schedule": [lambda p, d, t: backups.set_schedule(p, S),
                             lambda p, d, t: backups.set_schedule(p, S, "0 3 * * *", {"daily": 1})],
    "backups.offsite_list": [lambda p, d, t: backups.offsite_list(p, S)],
    "backups.download_file": [lambda p, d, t: backups.download_file(p, S, B, F, str(t / "out.bin"))],
    "backups.upload_from_site_backup": [lambda p, d, t: backups.upload_from_site_backup(p, S, B, d, "d.example.com")],
    "backups.restore_from_site_backup": [lambda p, d, t: backups.restore_from_site_backup(
        p, S, B, d, "d.example.com", owned_by_workflow="wf")],
    "backups.offsite_download": [lambda p, d, t: backups.offsite_download(p, S, B, d, "d.example.com",
                                                                          allow_overwrite=True)],
    "backups.restore_upload": [lambda p, d, t: backups.restore_upload(p, S, B, d, "d.example.com",
                                                                      owned_by_workflow="wf")],
    "logs.list_logs": [lambda p, d, t: logs.list_logs(p)],
    "logs.content": [lambda p, d, t: logs.content(p, F, 3), lambda p, d, t: logs.content(p, F)],
    "logs.tail": [lambda p, d, t: logs.tail(p, F)],
    "logs.task_events": [lambda p, d, t: logs.task_events(p, TS)],
    "monitor.central_metrics": [lambda p, d, t: (monitor.central_metrics(StubCentral(), "r1"), False)],
    "monitor.metrics": [lambda p, d, t: monitor.metrics(p)],
    "monitor.system": [lambda p, d, t: monitor.system(p)],
    "monitor.health": [lambda p, d, t: monitor.health(p)],
    "tasks.retry_conflict": [lambda p, d, t: _pure(tasks.retry_conflict(_noop))],
    "tasks.wait_task": [lambda p, d, t: tasks.wait_task(p, TS, poll=0)],
    "tasks.stream_events": [lambda p, d, t: tasks.stream_events(p, f"/tasks/{TS}/events")],
    "transfer.classify": [lambda p, d, t: _pure(transfer.classify("a-database.sql.gz"))],
    "transfer.restore_from_site_backup": [lambda p, d, t: transfer.restore_from_site_backup(p, S, B, d, "d.example.com")],
    "transfer.restore_via_download_links": [lambda p, d, t: transfer.restore_via_download_links(p, S, B, d, "d.example.com")],
    "domains.list_domains": [lambda p, d, t: domains.list_domains(p, S)],
    "domains.dns_records": [lambda p, d, t: domains.dns_records(p, S, "www.example.com")],
    "taskops.output": [lambda p, d, t: taskops.output(p, TS)],
    "taskops.cancel_task": [lambda p, d, t: taskops.cancel_task._unsafe(p, TS)],
}
SYNC_MODULES = {"domains", "taskops"}
MODULES = {"sites": sites, "apps": apps, "backups": backups, "logs": logs, "monitor": monitor, "tasks": tasks,
           "transfer": transfer, "domains": domains, "taskops": taskops}


async def _noop():
    return "ok"


def _pure(v):
    """Marks a case that is expected to make no Pilot request."""
    return v, False


def public_functions():
    out = set()
    for name, mod in MODULES.items():
        for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not fn_name.startswith("_") and fn.__module__ == mod.__name__:
                out.add(f"{name}.{fn_name}")
    return out


async def _drive(key, runner, calls, tmp):
    mod = key.split(".")[0]
    if mod in SYNC_MODULES:
        runner(SyncRecordingPilot(calls), None, tmp)
        return True
    p, d = RecordingPilot(calls), RecordingPilot(calls)
    r, expects = runner(p, d, tmp), True
    if isinstance(r, tuple):
        r, expects = r
    if inspect.isawaitable(r):
        r = await r
    if hasattr(r, "__aiter__"):
        async for _ in r:
            pass
    return expects


FLAT = [pytest.param(k, i, id=f"{k}#{i}") for k, rs in CASES.items() for i in range(len(rs))]
ALL_CALLS: list = []


@pytest.mark.parametrize("key,idx", FLAT)
def test_call_matches_dashboard_contract(key, idx, tmp_path):
    calls: list = []
    expects = asyncio.run(_drive(key, CASES[key][idx], calls, tmp_path))
    if expects:
        assert calls, f"{key} made no Pilot request"
    bad, _ = violations(calls)
    assert not bad, "\n".join(bad)
    ALL_CALLS.extend(calls)


def test_every_public_function_is_in_the_table():
    missing = public_functions() - set(CASES)
    stale = set(CASES) - public_functions()
    assert not missing, f"add these to CASES so the contract covers them: {sorted(missing)}"
    assert not stale, f"CASES names functions that no longer exist: {sorted(stale)}"


def test_allowlist_entries_have_reason_date_and_are_still_needed():
    for k, why in FCCTL_ONLY_ALLOWLIST.items():
        assert "2026-" in why and len(why) > 20, f"{k}: needs a reason and a verification date"
    calls = []
    for key, runners in CASES.items():
        for r in runners:
            asyncio.run(_drive(key, r, calls, Path("/tmp")))
    bad, used = violations(calls)
    assert not bad, bad
    assert used == set(FCCTL_ONLY_ALLOWLIST), f"unused allowlist entries: {set(FCCTL_ONLY_ALLOWLIST) - used}"
    for k in FCCTL_ONLY_ALLOWLIST:
        assert k not in INDEX, f"{k} is now in the dashboard contract: drop it from the allowlist"


def test_overrides_reference_real_routes():
    for kind in ("body_keys", "query_keys"):
        for k in OVERRIDES[kind]:
            m, _, p = k.partition(" ")
            assert (m, p) in INDEX, f"override {k} names no dashboard route"


# ---------------------------------------------------------------------------------------------- fake conformance
def test_fake_routes_are_in_contract_and_backup_detail_uses_size_bytes(fake_fc):
    import fcctl.apps as sapps
    import fcctl.backups as sbackups
    import fcctl.logs as slogs
    import fcctl.monitor as smonitor
    import fcctl.sites as ssites
    import fcctl.taskops as staskops
    import fcctl.tasks as stasks
    from fcctl.request import PilotClient as SyncPilot
    calls: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        resp = fake_fc._handle(req)
        if req.url.path.startswith("/api/v1/") and resp.status_code != 404:
            body = json.loads(req.content) if req.content else {}
            calls.append((req.method, normalize(req.url.path[len("/api/v1"):]),
                          sorted(body) if isinstance(body, dict) else [], sorted(dict(req.url.params))))
        return resp

    p = SyncPilot(fake_fc.central(), "server-fake", http=httpx.Client(transport=httpx.MockTransport(handler)),
                  clock=fake_fc.clock)
    run = dict(poll=0)
    name = "a.fake.frappe.cloud"
    stasks.run(p, lambda: ssites.create_site(p, name, []), **run)
    ssites.list_sites(p), ssites.get_site(p, name), ssites.wildcard_domains(p)
    ssites.set_maintenance(p, name, True)
    ssites.login_link(p, name, hint="h")
    stasks.run(p, lambda: ssites.clear_cache(p, name), **run)
    t = stasks.run(p, lambda: sbackups.create(p, name), **run)
    sbackups.list_backups(p, name, limit=5), sbackups.download_links(p, name, t["timestamp"])
    detail = sbackups.get(p, name, t["timestamp"])
    sapps.list_apps(p), sapps.add_marketplace(p, "erpnext"), sapps.add_repo(p, "https://github.com/o/x", "main")
    r = sapps.check_updates(p)
    stasks.wait_task(p, r["task_id"], poll=0)
    sapps.check_updates_result(p, r["task_id"])
    slogs.list_logs(p), slogs.content(p, "web.log", 2), slogs.content(p, "web.log")
    smonitor.health(p), smonitor.metrics(p), smonitor.system(p)
    staskops.output(p, r["task_id"])
    stasks.run(p, lambda: getattr(ssites.drop_site, "_unsafe", ssites.drop_site)(p, name), **run)

    assert calls
    bad, _ = violations(calls)
    assert not bad, "fake answers routes outside the contract:\n" + "\n".join(bad)
    want = OVERRIDES["responses"]["GET /sites/{}/backups/{}"]["files[]"]
    assert detail["files"], "fake backup detail has no files"
    for f in detail["files"]:
        assert set(want) <= set(f), f"fake backup file {f} lacks {want}"
        assert "size" not in f
