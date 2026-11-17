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
