import pytest

from fcctl.errors import NameConflict, PolicyDenied, QuotaExceeded, UsageError, FcError
from fcctl.journal import Journal, SUCCEEDED, NEEDS_ATTENTION
from fcctl.policy import EffectivePolicy
from fcctl.workflows import (AppSource, BackupSource, HookRef, HookResolver, ManifestMismatch, ResumeRefused,
                             ServerSpec, TemplateManifest, builtin_resolver, is_workflow_owned, provision_site)
from fcctl.workflows.types import StepContext


class StubPilot:
    def post(self, path, json=None, key=None): return {}
    def patch(self, path, body): return {}


class FakeApi:
    def __init__(self):
        self.sites, self.apps, self.cfg, self.calls = {}, {"frappe": {}, "erpnext": {}}, {}, []
        self.running, self.tier, self.healthy, self.fail_restore = [], {}, True, False
        self.keys = {}
        self.pilot = None

    def get_site(self, s): return self.sites.get(s)
    def bench_sites(self): return list(self.sites)
    def running_tasks(self): return list(self.running)
    def bench_apps(self): return dict(self.apps)
    def add_app(self, spec):
        self.calls.append(("add_app", spec)); self.apps[spec.get("name") or spec["repo"]] = {}
        return {"task_id": "t-app"}
    def create_site(self, site, apps, key):
        self.calls.append(("create_site", site, key))
        if key not in self.keys:
            self.keys[key] = "t-site"; self.sites[site] = {}
        return {"task_id": self.keys[key]}
    def wait_task(self, tid): self.calls.append(("wait", tid))
    def trust_tier(self): return self.tier
    def create_server(self, spec, key): self.calls.append(("create_server", key)); return {"resource_id": "srv1"}
    def wait_server(self, rid): self.calls.append(("wait_server", rid))
    def set_maintenance(self, site, on): self.cfg.update(maintenance_mode=1, pause_scheduler=1)
    def site_config(self, site): return dict(self.cfg)
    def patch_config(self, site, c): self.calls.append(("patch", c)); self.cfg.update(c)
    def backup_age(self, src): return None
    def restore(self, src, site, state, progress):
        self.calls.append(("restore", site))
        state["upload_id"] = "u1"; progress(1, 1, "f")
        if self.fail_restore:
            raise RuntimeError("boom")
        self.cfg.clear()  # restore resets config
        return {"task_id": "t-restore"}
    def health(self, site): return self.healthy


@pytest.fixture
def clock():
    t = [1000.0]
    def now(): t[0] += 1; return t[0]
    now.t = t
    return now


@pytest.fixture
def jr(tmp_path, clock): return Journal(tmp_path / "j", now=clock)


def run(jr, api, **kw):
    kw.setdefault("site", "a.example.com")
    return provision_site(pilot=StubPilot(), journal=jr, api=api, workflow_id=kw.pop("workflow_id", "wf1"), **kw)


SRC = BackupSource("src", "old.example.com", "20260101_000000", "24h")


def test_happy_path_order_and_result(jr):
    api = FakeApi()
    r = run(jr, api, bench_apps=[AppSource("erpnext"), AppSource(repo="https://g/x/myapp.git", branch="b")],
            site_apps=["erpnext"], from_backup=SRC, config={"k": 1},
            post_restore_hooks=[HookRef("core.clear_cache", 1)])
    out = r.wait()
    assert out["site"] == "a.example.com" and out["workflow_id"] == "wf1" and "preflight" in out["timings"]
    assert [c for c in api.calls if c[0] == "add_app"] == [("add_app", {"repo": "https://g/x/myapp.git", "branch": "b"})]
    assert api.cfg.get("maintenance_mode") == 1 and api.cfg.get("pause_scheduler") == 1  # re-applied after restore
    assert api.cfg["k"] == 1 and api.cfg["fcctl_workflow_id"] == "wf1"
    assert jr.get("wf1")["state"] == SUCCEEDED
    assert r.to_dict()["state"] == SUCCEEDED


def test_preflight_blockers(jr):
    api = FakeApi(); api.sites["other.com"] = {}
    with pytest.raises(FcError, match="already has sites"):
        run(jr, api, workflow_id="a").wait()
    run(jr, api, workflow_id="b", allow_existing_sites=True).wait()
    api2 = FakeApi(); api2.running = [{"task_id": "x"}]
    with pytest.raises(FcError, match="running"):
        run(jr, api2, workflow_id="c").wait()
    with pytest.raises(PolicyDenied):
        run(jr, FakeApi(), workflow_id="d", eff=EffectivePolicy("l", "t", "read_only")).wait()


def test_quota_and_sizing(jr):
    api = FakeApi(); api.tier = {"spending_limit": 8300, "running_monthly": 6000}
    with pytest.raises(QuotaExceeded):
        run(jr, api, workflow_id="q", server=ServerSpec("std", "img", price=3000, vcpu=2)).wait()
    api = FakeApi(); api.tier = {"current": {"max_resource_count": 3}, "progress": {"resources_used": 3}}
    with pytest.raises(QuotaExceeded, match="resource limit"):
        run(jr, api, workflow_id="q2", server=ServerSpec("std", "img", price=1, vcpu=2)).wait()
    with pytest.raises(UsageError, match="below minimum"):
        run(jr, FakeApi(), workflow_id="s", server=ServerSpec("tiny", "img", vcpu=0.5)).wait()
    api = FakeApi()
    run(jr, api, workflow_id="ok", server=ServerSpec("p", "img", price=100, vcpu=2),
        pilot_factory=lambda rid: object()).wait()
    assert ("create_server", "ok") in api.calls and ("wait_server", "srv1") in api.calls


def test_existing_site_conflict_and_adoption(jr):
    api = FakeApi(); api.sites["a.example.com"] = {}
    with pytest.raises(NameConflict):
        run(jr, api, allow_existing_sites=True).wait()


def test_manifest_gate(jr):
    api = FakeApi(); api.apps["erpnext"] = {"version": "15.1"}
    m = TemplateManifest("t", "1", {"erpnext": "99"})
    with pytest.raises(ManifestMismatch):
        run(jr, api, manifest=m).wait()
    assert TemplateManifest("t", "1", {"erpnext": {"version": "15.2", "compatible": ["15.1"]}}) \
        .check({"erpnext": "15.1"}) == []


def test_resume_after_crash_adopts_own_site_and_skips_done(jr):
    api = FakeApi(); api.fail_restore = True
    calls = {"n": 0}
    r = run(jr, api, from_backup=SRC)
    with pytest.raises(RuntimeError):
        r.wait()
    e = jr.get("wf1")
    assert e["state"] == NEEDS_ATTENTION and e["wf"]["transfer"]["upload_id"] == "u1"
    assert "create_site" in e["wf"]["steps"] and "restore" not in e["wf"]["steps"]
    api.fail_restore = False
    r2 = run(jr, api, from_backup=SRC)  # same workflow id: resume; its own site must not trip preflight/conflict
    r2.wait()
    assert sum(1 for c in api.calls if c[0] == "create_site") == 1
    assert jr.get("wf1")["state"] == SUCCEEDED


def test_resume_site_created_but_task_unrecorded(jr):
    api = FakeApi()
    r = run(jr, api)
    # simulate crash after POST /sites but before task id recorded
    r._save(lambda w: w.update(created_site={"site": "a.example.com", "idem_key": "wf1-create-site", "at": 1.0}))
    api.sites["a.example.com"] = {}; api.keys["wf1-create-site"] = "t-site"
    r._save(lambda w: w["steps"].update({"preflight": {"done": True}, "server": {"done": True},
                                         "bench_apps": {"done": True}}))
    r.wait()
    assert jr.get("wf1")["wf"]["created_site"]["task_id"] == "t-site"


def test_hook_version_mismatch_refused_then_allowed(jr):
    api = FakeApi()
    run(jr, api, post_restore_hooks=[HookRef("core.clear_cache", 1)])
    with pytest.raises(ResumeRefused):
        run(jr, api, post_restore_hooks=[HookRef("core.clear_cache", 2)])
    res = HookResolver().register("core.clear_cache", 2, lambda ctx: None)
    r = run(jr, api, post_restore_hooks=[HookRef("core.clear_cache", 2)], hook_resolver=res,
            allow_hook_upgrade=True)
    r.wait()
    d = jr.get("wf1")["wf"]["decisions"]
    assert d and d[0]["decision"] == "allow_hook_upgrade" and "core.clear_cache" in d[0]["diff"][0]


def test_hooks_run_in_order_resume_midway_and_health_failure(jr):
    api, seen = FakeApi(), []
    res = HookResolver({"x.a@1": lambda ctx, n=0: seen.append(("a", n)),
                        "x.b@1": lambda ctx: (seen.append("b"), 1 / 0)[0]})
    hooks = [HookRef("x.a", 1, {"n": 3}), HookRef("x.b", 1)]
    r = run(jr, api, post_restore_hooks=hooks, hook_resolver=res)
    with pytest.raises(ZeroDivisionError):
        r.wait()
    assert jr.get("wf1")["state"] == NEEDS_ATTENTION and jr.get("wf1")["wf"]["hooks_done"] == 1
    res2 = HookResolver({"x.a@1": lambda ctx, n=0: seen.append("a-again"), "x.b@1": lambda ctx: seen.append("b2")})
    run(jr, api, post_restore_hooks=hooks, hook_resolver=res2).wait()
    assert seen == [("a", 3), "b", "b2"]
    api = FakeApi(); api.healthy = False
    with pytest.raises(FcError, match="health"):
        run(jr, api, workflow_id="h").wait()
    assert jr.get("h")["state"] == NEEDS_ATTENTION


def test_workflow_owned_rule(jr, clock):
    api = FakeApi()
    run(jr, api).wait()
    now = jr.now()
    assert is_workflow_owned(jr, "wf1", "a.example.com", now)
    assert not is_workflow_owned(jr, "wf1", "other.com", now)
    assert not is_workflow_owned(jr, "wf1", "a.example.com", now + 3700)
    assert not is_workflow_owned(jr, "wf1", "a.example.com", now, policy="read_only")
    assert not is_workflow_owned(jr, "nope", "a.example.com", now)
    e = jr.get("wf1"); e["wf"]["handed_off"] = True; jr._write(e)
    assert not is_workflow_owned(jr, "wf1", "a.example.com", now)


def test_restore_refused_when_not_owned(jr):
    r = run(jr, FakeApi(), from_backup=SRC, eff=EffectivePolicy("l", "t", "guarded"))
    r._save(lambda w: w["steps"].update({k: {"done": True} for k in ("preflight", "server", "bench_apps", "create_site", "isolate")}))
    with pytest.raises(PolicyDenied):
        r.wait()


def test_to_from_dict_roundtrip(jr):
    api = FakeApi()
    r = run(jr, api, bench_apps=[AppSource("erpnext")], site_apps=["erpnext"], from_backup=SRC,
            post_restore_hooks=[HookRef("core.clear_cache", 1)])
    d = r.to_dict()
    import json; json.dumps(d)
    r2 = type(r).from_dict(d, journal=jr, pilot=StubPilot(), api=api)
    r2.wait()
    assert r2.to_dict()["state"] == SUCCEEDED


def test_builtin_hooks():
    class P:
        def __init__(self): self.c = []
        def patch(self, path, body): self.c.append(("patch", path, body))
        def post(self, path, json=None, key=None): self.c.append(("post", path)); return {"task_id": "t"}
    p, waited = P(), []
    ctx = StepContext("s.com", p, wait_task=waited.append)
    r = builtin_resolver()
    r.resolve("core.set_config@1")(ctx, config={"a": 1})
    r.resolve(HookRef("core.set_host_name"))(ctx, host_name="h.com")
    r.resolve("core.scheduler_pause@1")(ctx)
    r.resolve("core.maintenance_off@1")(ctx)
    r.resolve("core.migrate@1")(ctx); r.resolve("core.clear_cache@1")(ctx)
    assert p.c[0] == ("patch", "/sites/s.com/configuration", {"a": 1})
    assert p.c[1][2] == {"host_name": "h.com"} and waited == ["t", "t"]
    with pytest.raises(UsageError):
        r.resolve("core.set_fresh_encryption_key@1")(ctx)
    r.resolve("core.set_fresh_encryption_key@1")(ctx, confirm_no_encrypted_values=True)
    assert "encryption_key" in p.c[-1][2]
    with pytest.raises(UsageError):
        r.resolve("nope@1")


def test_site_admin_lazy_and_opt_in(jr):
    api, opened, seen = FakeApi(), [], []

    class Adm:
        def close(self): opened.append("closed")

    def factory():
        opened.append("open")
        return Adm()
    res = HookResolver({"x.a@1": lambda ctx: seen.append(ctx.site_admin)})
    hooks = [HookRef("x.a", 1)]
    run(jr, api, post_restore_hooks=hooks, hook_resolver=res, admin_factory=factory).wait()
    assert opened == [] and seen == [None]  # not declared -> never opened
    run(jr, api, workflow_id="w2", site="b.example.com", allow_existing_sites=True, post_restore_hooks=hooks, hook_resolver=res, admin_factory=factory,
        needs_site_admin=True).wait()
    assert opened == ["open", "closed"] and isinstance(seen[1], Adm)
    m = TemplateManifest("t", "1", extras={"needs_site_admin": True})
    run(jr, api, workflow_id="w3", site="c.example.com", allow_existing_sites=True, post_restore_hooks=hooks, hook_resolver=res, admin_factory=factory,
        manifest=m).wait()
    assert opened.count("open") == 2


def test_running_tasks_includes_queued():
    import asyncio
    from fcctl._async.workflows.provision import LiveApi

    class P:
        def __init__(self): self.calls = []
        async def get(self, path, **params):
            self.calls.append((path, params))
            return {"tasks": [{"id": "a", "status": "queued"}, {"id": "b", "status": "running"},
                              {"id": "c", "status": "success"}, {"id": "d", "status": "pending"}]}

    p = P()
    r = asyncio.run(LiveApi(p).running_tasks())
    assert [t["id"] for t in r] == ["a", "b"]
    assert p.calls == [("/tasks", {})]


def test_plain_dict_hooks_are_accepted(jr):
    """Stored templates pass hooks as {name, version, args} dicts; they used to crash to_dict() at start."""
    api = FakeApi()
    calls = []
    res = HookResolver().register("core.clear_cache", 1, lambda ctx: calls.append(1))
    r = run(jr, api, post_restore_hooks=[{"name": "core.clear_cache", "version": 1, "args": {}}], hook_resolver=res)
    r.wait()
    assert calls == [1]


def test_live_api_site_config_reads_configuration_endpoint():
    """Live Pilot: GET /sites/{s} has no config; maintenance checks must read /configuration."""
    from fcctl.workflows.provision import LiveApi
    class P:
        def get(self, path, **kw):
            P.path = path
            return {"maintenance_mode": 1, "pause_scheduler": 1}
    cfg = LiveApi(P(), None).site_config("a.example.com")
    assert P.path == "/sites/a.example.com/configuration" and cfg["maintenance_mode"] == 1
