import asyncio
import json

import httpx
import pytest

import fcctl
from fcctl import AsyncClient, BackupSource, Client, DiskShrinkRefused
from fcctl.errors import PolicyDenied
from fcctl.guard import Guard, set_default_guard
from fcctl.journal import Journal


class Store:
    sid, csrf, data = "sid1", "csrf1", {}

    def set_central(self, **kw): pass


def make(state, plans=None, overview=None):
    plans = plans if plans is not None else [{"name": "big", "disk": 100}, {"name": "small", "disk": 20}]
    overview = overview if overview is not None else {"plan": {"disk": 50}}

    def h(req: httpx.Request):
        state.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        p = req.url.path
        if "get_bench_link" in p:
            return httpx.Response(200, json={"message": {"url": "https://gw.test/?sid=JWT"}})
        if p.endswith("server_overview"):
            return httpx.Response(200, json={"message": overview})
        if p.endswith("eligible_plans"):
            return httpx.Response(200, json={"message": plans})
        if p.endswith("resize_server"):
            return httpx.Response(200, json={"message": {"ok": 1}})
        return httpx.Response(200, json={"ok": True, "url": "x"})
    return httpx.MockTransport(h)


def sync_client(state, **kw):
    t = make(state, **kw)
    c = Client(Store(), "T", http=httpx.Client(transport=t))
    c.pilot = lambda s, _c=c: __import__("fcctl._sync.pilot", fromlist=["x"]).PilotClient(
        _c.central, s, http=httpx.Client(transport=t))
    return c


def async_client(state, **kw):
    from fcctl._async.pilot import PilotClient
    t = make(state, **kw)
    c = AsyncClient(Store(), "T", http=httpx.AsyncClient(transport=t))
    c.pilot = lambda s, _c=c: PilotClient(_c.central, s, http=httpx.AsyncClient(transport=t))
    return c


def run(c, coro):
    return asyncio.run(coro) if isinstance(c, AsyncClient) else coro


@pytest.fixture(params=["sync", "async"])
def cl(request):
    st = []
    return (sync_client if request.param == "sync" else async_client)(st), st


def call(c, fn, *a, **k):
    r = fn(*a, **k)
    return asyncio.run(r) if asyncio.iscoroutine(r) else r


def test_suspend_resume(cl):
    c, st = cl
    call(c, c.sites.suspend, "srv", "a.test")
    call(c, c.sites.resume, "srv", "a.test")
    patches = [b for m, p, b in st if m == "PATCH"]
    assert patches == [{"maintenance_mode": 1, "pause_scheduler": 1}, {"maintenance_mode": 0, "pause_scheduler": 0}]


def test_resize_ok_and_shrink_refused(cl):
    c, st = cl
    assert call(c, c.servers.resize, "srv", "big") == {"ok": 1}
    assert [b for m, p, b in st if p.endswith("resize_server")][0]["plan"] == "big"
    with pytest.raises(DiskShrinkRefused) as e:
        call(c, c.servers.resize, "srv", "small")
    assert e.value.exit_code == 12


def _resize_client(kind, seen, overview, plans, cfg=None, plans_status=200):
    """Client whose transport mimics the real Central resize pre-check endpoints."""
    def h(req: httpx.Request):
        p = req.url.path
        seen.append((req.method, p.rsplit(".", 1)[-1], dict(req.url.params),
                     json.loads(req.content) if req.content else None))
        if p.endswith("server_overview"):
            return httpx.Response(200, json={"message": overview})
        if p.endswith("get_composed_config"):
            return httpx.Response(200, json={"message": cfg if cfg is not None else {"resizable": True}})
        if p.endswith("get_eligible_plans"):
            if plans_status != 200:
                return httpx.Response(plans_status, json={"exc_type": "ValidationError",
                                                          "_server_messages": "[]", "message": "boom"})
            return httpx.Response(200, json={"message": plans})
        if p.endswith("resize_server"):
            return httpx.Response(200, json={"message": {"ok": 1}})
        return httpx.Response(404, json={})
    t = httpx.MockTransport(h) if kind == "sync" else None
    if kind == "sync":
        return Client(Store(), "T", http=httpx.Client(transport=t))
    return AsyncClient(Store(), "T", http=httpx.AsyncClient(transport=httpx.MockTransport(h)))


CATALOG = {"plans": {"General": [
    {"plan": "s-2", "includes": [{"resource_type": "Disk", "quantity": 25, "unit": "GB"}]},
    {"plan": "s-8", "includes": [{"resource_type": "Disk", "quantity": 160, "unit": "GB"}]}]}}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_resize_precheck_uses_billing_catalog_like_dashboard(kind):
    seen = []
    c = _resize_client(kind, seen, {"server": {"region": "blr", "disk_gigabytes": 80}}, CATALOG,
                       cfg={"resizable": True, "subscription": "SUB-1", "disk_gb": 80})
    assert call(c, c.servers.resize, "srv", "s-8") == {"ok": 1}
    q = next(params for m, fn, params, b in seen if fn == "get_eligible_plans")
    assert q == {"cluster": "blr", "exclude_subscription": "SUB-1", "for_resize": "1", "team": "T"}
    assert not any(fn == "eligible_plans" for m, fn, params, b in seen)  # never the image (create) catalog
    with pytest.raises(DiskShrinkRefused):
        call(c, c.servers.resize, "srv", "s-2")
    assert sum(fn == "resize_server" for m, fn, params, b in seen) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_resize_current_disk_from_composed_config(kind):
    seen = []
    c = _resize_client(kind, seen, {"server": {"region": "blr"}}, CATALOG,
                       cfg={"subscription": "SUB-1", "disk_gb": 50})
    with pytest.raises(DiskShrinkRefused):
        call(c, c.servers.resize, "srv", "s-2")
    assert call(c, c.servers.resize, "srv", "s-8") == {"ok": 1}


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_resize_explicit_disk_keeps_disk_and_blocks_shrink(kind):
    seen = []
    c = _resize_client(kind, seen, {"region": "blr", "disk_gigabytes": 80}, CATALOG)
    assert call(c, c.servers.resize, "srv", "s-2", disk_gigabytes=80) == {"ok": 1}
    assert not any(fn == "get_eligible_plans" for m, fn, params, b in seen)
    with pytest.raises(DiskShrinkRefused):
        call(c, c.servers.resize, "srv", "s-8", disk_gigabytes=40)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_resize_precheck_fails_open_with_warning(kind):
    seen = []
    c = _resize_client(kind, seen, {"region": "blr", "disk_gigabytes": 80}, CATALOG, plans_status=417)
    with pytest.warns(RuntimeWarning, match="pre-check skipped"):
        assert call(c, c.servers.resize, "srv", "s-2") == {"ok": 1}
    assert seen[-1][1] == "resize_server"


def test_download_links(cl):
    c, st = cl
    call(c, c.backups.download_links, "srv", "a.test", "20260101_000000")
    assert st[-1][1] == "/api/v1/sites/a.test/backups/20260101_000000/download-links"


def test_restore_guarded_and_owned(cl, tmp_path, monkeypatch):
    c, st = cl
    set_default_guard(Guard(home=tmp_path / "g", key="k", secret="s", user="alice"))
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "h"))
    try:
        rec = call(c, c.backups.restore, "srv", "a.test", from_upload={"upload_id": "u1", "parts": ["database"]})
        assert rec.op == "restore_backup" and rec.args["upload_id"] == "u1"
        assert not [1 for m, p, b in st if p.endswith("actions/restore")]
        with pytest.raises(PolicyDenied):
            call(c, c.backups.restore, "srv", "a.test", from_upload="u1", owned_by_workflow="wf-x",
                 journal=Journal(tmp_path / "j"))
        jr = Journal(tmp_path / "j2")
        jr.create("provision", {})
        monkeypatch.setattr("fcctl._async.workflows.provision.is_workflow_owned", lambda *a, **k: True)
        monkeypatch.setattr("fcctl._sync.workflows.provision.is_workflow_owned", lambda *a, **k: True)
        call(c, c.backups.restore, "srv", "a.test", from_upload="u1", owned_by_workflow="wf-x", journal=jr)
        assert [b for m, p, b in st if p.endswith("actions/restore")][0]["upload_id"] == "u1"
    finally:
        set_default_guard(None)


def test_provision_maps_backup_source(cl, tmp_path):
    c, st = cl
    run_ = call(c, c.workflows.provision_site, server="srv", site="n.test", journal=Journal(tmp_path / "j"),
                from_backup=BackupSource("prod", "src-srv", "old.test", "20260101_000000"))
    fb = run_.from_backup
    assert (fb.profile, fb.site, fb.backup) == ("prod", "old.test", "20260101_000000")
    assert run_.api.source_pilot.server == "src-srv" and fcctl.BackupSource is BackupSource


def test_sites_create_list_login(cl):
    c, st = cl
    call(c, c.sites.create, "srv", "n.test", ["erpnext"], "key1")
    call(c, c.sites.list, "srv")
    call(c, c.sites.login_link, "srv", "n.test")
    assert [(m, p) for m, p, b in st if "/api/v1/" in p] == [("POST", "/api/v1/sites"), ("GET", "/api/v1/sites"),
                                          ("POST", "/api/v1/sites/n.test/login")]
    assert [b for m, p, b in st if p == "/api/v1/sites" and m == "POST"][0] == {"name": "n.test", "apps": ["erpnext"]}


def test_sites_drop_guarded_and_owned(cl, tmp_path, monkeypatch):
    c, st = cl
    set_default_guard(Guard(home=tmp_path / "g", key="k", secret="s", user="alice"))
    try:
        rec = call(c, c.sites.drop, "srv", "a.test")
        assert rec.op == "drop_site" and not [1 for m, p, b in st if m == "DELETE"]
        with pytest.raises(PolicyDenied):
            call(c, c.sites.drop, "srv", "a.test", owned_by_workflow="wf-x", journal=Journal(tmp_path / "j"))
        monkeypatch.setattr("fcctl._async.facade._check_owned", lambda *a, **k: None)
        monkeypatch.setattr("fcctl._sync.facade._check_owned", lambda *a, **k: None)
        call(c, c.sites.drop, "srv", "a.test", owned_by_workflow="wf-x")
        assert [p for m, p, b in st if m == "DELETE"] == ["/api/v1/sites/a.test"]
    finally:
        set_default_guard(None)


def test_sites_admin_delegates(cl, monkeypatch):
    c, st = cl
    seen = []

    async def fake(client, server, site):
        seen.append((server, site))
        return "ADMIN"

    def fake_sync(client, server, site):
        seen.append((server, site))
        return "ADMIN"
    monkeypatch.setattr("fcctl._async.siteadmin.open_admin", fake)
    monkeypatch.setattr("fcctl._sync.siteadmin.open_admin", fake_sync)
    assert call(c, c.sites.admin, "srv", "a.test") == "ADMIN" and seen == [("srv", "a.test")]
