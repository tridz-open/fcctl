import pytest

from fcctl import backups, billing, objects, servers, sites, tasks
from fcctl.auth import login_with_code
from fcctl.errors import RateLimited
from fcctl.request import AuthError, CloudflareChallenge, CsrfError, TaskConflict, TaskFailed
from fcctl.testing import FakeFrappeCloud, fake_fc  # noqa: F401

NOSLEEP = dict(poll=0)


def test_servers_create_idempotent_and_list(fake_fc):
    c = fake_fc.central()
    a = servers.create_server(c, "t", "p1", "img", request_key="k1")
    b = servers.create_server(c, "t", "p1", "img", request_key="k1")
    assert a["resource_id"] == b["resource_id"]
    assert [s["resource_id"] for s in servers.list_servers(c)] == [a["resource_id"]]
    servers.stop_server(c, a["resource_id"])
    assert servers.list_servers(c)[0]["status"] == "Stopped"
    servers.start_server(c, a["resource_id"])
    assert servers.wait_running(c, a["resource_id"])["status"] == "Running"


def test_create_site_via_tasks_run(fake_fc):
    p = fake_fc.pilot()
    t = tasks.run(p, lambda: sites.create_site(p, "a.fake.frappe.cloud", ["erpnext"], key="idem1"), **NOSLEEP)
    assert t["status"] == "success"
    assert sites.get_site(p, "a.fake.frappe.cloud")["status"] == "active"
    r1 = sites.create_site(p, "b.fake.frappe.cloud", [], key="same")
    r2 = sites.create_site(p, "b.fake.frappe.cloud", [], key="same")
    assert r1["task_id"] == r2["task_id"]


def test_maintenance_and_backups(fake_fc):
    p = fake_fc.pilot()
    tasks.run(p, lambda: sites.create_site(p, "s.x", []), **NOSLEEP)
    sites.set_maintenance(p, "s.x", True)
    assert sites.get_site(p, "s.x")["maintenance_mode"] == 1
    t = tasks.run(p, lambda: backups.create(p, "s.x"), **NOSLEEP)
    lst = backups.list_backups(p, "s.x")["backups"]
    assert lst and lst[0]["timestamp"] == t["timestamp"]
    assert "links" in backups.download_links(p, "s.x", t["timestamp"])


def test_task_conflict(fake_fc):
    p = fake_fc.pilot()
    sites.create_site(p, "c.x", [])  # pending, unpolled
    with pytest.raises(TaskConflict):
        sites.migrate(p, "c.x")


def test_injected_conflict(fake_fc):
    p = fake_fc.pilot()
    tasks.run(p, lambda: sites.create_site(p, "d.x", []), **NOSLEEP)
    fake_fc.inject_conflict("/actions/clear-cache", times=1)
    r = tasks.retry_conflict(lambda: sites.clear_cache(p, "d.x"), )
    assert r["task_id"]


def test_fail_task_and_events(fake_fc):
    p = fake_fc.pilot()
    r = sites.create_site(p, "f.x", [])
    fake_fc.fail_task(r["task_id"])
    with pytest.raises(TaskFailed):
        tasks.wait_task(p, r["task_id"], **NOSLEEP)
    evs = list(tasks.stream_events(p, f"/tasks/{r['task_id']}/events"))
    assert '"failed"' in evs[0]


def test_billing_and_buckets(fake_fc):
    c = fake_fc.central()
    assert billing.trust_tier(c)["tier"] == "verified"
    b = objects.create_bucket(c, "bk")
    assert b["secret_key"]
    assert objects.get_bucket_usage(c, "bk")["used_bytes"] == 0
    assert "bk" in fake_fc.buckets


def test_faults_exhaust(fake_fc):
    c = fake_fc.central()
    fake_fc.inject_auth_expired(times=1)
    with pytest.raises(AuthError):
        billing.trust_tier(c)
    fake_fc.inject_csrf()
    with pytest.raises(CsrfError):
        billing.trust_tier(c)
    fake_fc.inject_cloudflare()
    with pytest.raises(CloudflareChallenge):
        billing.trust_tier(c)
    assert billing.trust_tier(c)


def test_login_flow_and_expired_session(fake_fc):
    c = fake_fc.central(store=fake_fc.store(authed=False))
    with pytest.raises(AuthError):
        billing.trust_tier(c)
    login_with_code(c, "u@x.com", lambda: fake_fc.login_code)
    assert c.store.sid == fake_fc.sid and billing.trust_tier(c)
    fake_fc.expire_session()
    with pytest.raises(AuthError):
        billing.trust_tier(c)


def test_pilot_token_expiry_and_calls_log(fake_fc):
    p = fake_fc.pilot()
    sites.list_sites(p)
    sites.list_sites(p)
    mints = lambda: [x for x in fake_fc.calls if x.path.endswith("get_bench_link")]  # noqa: E731
    assert len(mints()) == 1
    fake_fc.clock.advance(500)
    sites.list_sites(p)
    assert len(mints()) == 2


def test_rate_limit(fake_fc):
    assert isinstance(fake_fc, FakeFrappeCloud)
    p = fake_fc.pilot()
    fake_fc.inject_rate_limit("/sites")
    with pytest.raises(RateLimited) as e:
        sites.list_sites(p)
    assert e.value.status == 429


def test_logs_content_with_lines_uses_json_tail(fake_fc):
    from fcctl import logs
    p = fake_fc.pilot()
    fake_fc.logs["f"] = "a\nb\nc\nd\n"
    assert logs.content(p, "f", 3) == "b\nc\nd"
    paths = [c.path for c in fake_fc.calls]
    assert "/api/v1/logs/f" in paths and "/api/v1/logs/f/content" not in paths


def test_logs_content_without_lines_downloads(fake_fc):
    from fcctl import logs
    p = fake_fc.pilot()
    fake_fc.logs["f"] = "a\nb\n"
    assert logs.content(p, "f") == "a\nb\n"
    assert "/api/v1/logs/f/content" in [c.path for c in fake_fc.calls]
