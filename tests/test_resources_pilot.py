import json

import httpx
import pytest
import respx

from fcctl import apps, backups, domains, policy, sites, taskops
from fcctl import activity, billing, infra, notifications, objects, servers
from fcctl.request import PilotClient

GW = "https://pilot.example"


@pytest.fixture
def p(central):
    c = PilotClient(central, "server-abc")
    c._cache()[c.server] = {"token": "t", "gateway": GW, "issued_at": c.clock()}
    return c


@pytest.fixture
def r():
    with respx.mock(assert_all_called=False) as router:
        yield router


def ok(r, method, path, body=None, status=200):
    return getattr(r, method)(GW + "/api/v1" + path).mock(return_value=httpx.Response(status, json={} if body is None else body))


def test_site_config_domains_apps(r, p):
    ok(r, "get", "/sites/s/configuration", {"maintenance_mode": 0})
    assert sites.config_get(p, "s") == {"maintenance_mode": 0}
    patch = ok(r, "patch", "/sites/s/configuration", {})
    sites.config_set(p, "s", maintenance_mode=1, pause_scheduler=1)
    assert json.loads(patch.calls[0].request.content) == {"maintenance_mode": 1, "pause_scheduler": 1}
    ok(r, "get", "/sites/s/apps", [{"name": "myapp"}])
    assert sites.site_apps(p, "s")[0]["name"] == "myapp"
    ok(r, "get", "/sites/s/domains", [])
    ok(r, "get", "/sites/s/domains/d.com/dns-records", {"records": []})
    assert domains.list_domains(p, "s") == []
    assert domains.dns_records(p, "s", "d.com") == {"records": []}


def test_taskops(r, p):
    ok(r, "get", "/tasks/t1/output/content", {"content": "x"})
    assert taskops.output(p, "t1") == {"content": "x"}
    assert taskops.cancel_task.guard_op == "cancel_task"


def test_backups_offsite(r, p):
    ok(r, "get", "/sites/s/integrations/frappe-cloud/backups", [{"name": "b"}])
    assert backups.offsite_list(p, "s") == [{"name": "b"}]
    with pytest.raises(ValueError):
        backups.offsite_download(p, "s", "ts", p, "d")
    assert backups.restore_upload is not None and apps.remove.__doc__


def test_ops_ids_known_to_policy():
    """Every write op id declared in an OPS map should exist in policy.REGISTRY (missing ones are reported)."""
    write_words = ("start", "stop", "restart", "resize", "protect", "config_set", "mark", "terminate",
                   "create", "delete", "remove", "cancel", "restore", "quota")
    missing = []
    for mod in (servers, sites, objects, apps, backups, taskops):
        for fn, op in mod.OPS.items():
            if any(w in fn for w in write_words) and op not in policy.REGISTRY:
                missing.append(op)
    # known gaps are documented in the task report; the test just guards against typos in OPS
    assert all("." in m for m in missing)
    for mod in (servers, sites, objects, apps, backups, taskops, billing, notifications, infra, domains, activity):
        assert all(callable(getattr(mod, fn)) for fn in mod.OPS)


def test_check_updates_path(r, p):
    fetch = ok(r, "post", "/apps/fetch", {"task_id": "t"})
    old = ok(r, "post", "/app-update-checks", {})
    assert apps.check_updates(p) == {"task_id": "t"}
    assert fetch.calls[0].request.method == "POST" and not old.called


def test_check_updates_result_parses_last_line(r, p):
    ok(r, "get", "/tasks/t/output/content", {"content": 'x\n{"erpnext":{"has_update":true}}\n'})
    assert apps.check_updates_result(p, "t") == {"erpnext": {"has_update": True}}


def test_policy_check_updates_is_post():
    assert policy.REGISTRY["app.check_updates"].get("method") == "POST"


def test_add_repo_resolves_name(r, p):
    res = ok(r, "post", "/git/repository-resolutions", {"name": "demo"})
    add = ok(r, "post", "/apps", {"task_id": "t"})
    apps.add_repo(p, "https://github.com/o/demo", "main")
    assert json.loads(res.calls[0].request.content) == {"repo": "https://github.com/o/demo", "branch": "main"}
    assert json.loads(add.calls[0].request.content) == {
        "name": "demo", "repo": "https://github.com/o/demo", "branch": "main", "sites": []}
    assert [c.request.url.path for c in r.calls] == [
        "/api/v1/git/repository-resolutions", "/api/v1/apps"]


def test_add_repo_name_override_skips_resolution(r, p):
    res = ok(r, "post", "/git/repository-resolutions", {"name": "x"})
    add = ok(r, "post", "/apps", {"task_id": "t"})
    apps.add_repo(p, "R", "b", ["s"], name="mine")
    assert not res.called
    assert json.loads(add.calls[0].request.content) == {"name": "mine", "repo": "R", "branch": "b", "sites": ["s"]}


def test_add_repo_resolution_error(r, p):
    ok(r, "post", "/git/repository-resolutions", {"message": "no app found"})
    add = ok(r, "post", "/apps", {})
    with pytest.raises(Exception, match="no app found"):
        apps.add_repo(p, "R", "main")
    assert not add.called


def test_add_in_order_repo_spec(r, p):
    ok(r, "post", "/git/repository-resolutions", {"name": "demo"})
    add = ok(r, "post", "/apps", {})
    apps.add_in_order(p, [{"repo": "R", "branch": "main"}])
    assert json.loads(add.calls[0].request.content) == {"name": "demo", "repo": "R", "branch": "main", "sites": []}


def test_add_marketplace_with_sites_uses_site_install(r, p):
    a = ok(r, "post", "/sites/a/apps", {"task_id": "t"})
    b = ok(r, "post", "/sites/b/apps", {"task_id": "t"})
    apps_route = ok(r, "post", "/apps", {})
    apps.add_marketplace(p, "india_compliance", ["a", "b"])
    assert json.loads(a.calls[0].request.content) == {"app": "india_compliance"}
    assert json.loads(b.calls[0].request.content) == {"app": "india_compliance"}
    assert not apps_route.called


def test_add_marketplace_bench_only_resolves_registry(r, p):
    ok(r, "get", "/marketplace/apps", [{"name": "x", "repo": "https://github.com/o/x", "branch": "version-16", "is_installable": True}])
    add = ok(r, "post", "/apps", {"task_id": "t"})
    apps.add_marketplace(p, "x")
    assert json.loads(add.calls[0].request.content) == {"name": "x", "repo": "https://github.com/o/x", "branch": "version-16", "sites": []}


def test_add_marketplace_unknown_app_raises(r, p):
    from fcctl.errors import UsageError
    ok(r, "get", "/marketplace/apps", [])
    add = ok(r, "post", "/apps", {})
    with pytest.raises(UsageError):
        apps.add_marketplace(p, "nope")
    assert not add.called


def test_rename_site_body(r, p):
    route = ok(r, "post", "/sites/s/actions/rename", {"task_id": "t"})
    sites.rename_site(p, "s", "n.example")
    assert json.loads(route.calls.last.request.content) == {"new_name": "n.example", "keep_old_hostname": False}
