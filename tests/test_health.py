import httpx
import pytest
import respx

from fcctl import health
from fcctl.health import check_admin_session, check_site_health
from tests.conftest import GW, LINK

SITE = "a.example.com"
URL = {"data": {"url": GW + "/?sid=JWT1"}}


def setup(maint, status="active", ping=200, root=200):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=URL))
    respx.get(GW + f"/api/v1/sites/{SITE}").mock(return_value=httpx.Response(
        200, json={"name": SITE, "status": status, "maintenance_mode": maint}))
    respx.get(f"https://{SITE}/api/method/ping").mock(return_value=httpx.Response(ping, json={"message": "pong"}))
    respx.get(f"https://{SITE}/").mock(return_value=httpx.Response(root))


@respx.mock
@pytest.mark.parametrize("root", [200, 302])
def test_healthy(pilot, root):
    setup(False, root=root)
    r = check_site_health(pilot, SITE)
    assert r.state == health.HEALTHY and r.ok and not r.reasons


@respx.mock
def test_healthy_in_maintenance(pilot):
    setup(True, root=503)
    r = check_site_health(pilot, SITE)
    assert r.state == health.HEALTHY_MAINT and r.ok


@respx.mock
def test_maintenance_isolation_missing_fails(pilot):
    setup(True, root=200)
    r = check_site_health(pilot, SITE)
    assert not r.ok and any("isolation" in x for x in r.reasons)


@respx.mock
def test_broken_and_provisioning_and_ping_fail(pilot):
    setup(False, status="broken", ping=502)
    r = check_site_health(pilot, SITE)
    assert not r.ok and len(r.reasons) == 2
    setup(False, status="installing")
    assert any("provisioning" in x for x in check_site_health(pilot, SITE).reasons)


@respx.mock
def test_missing_site_and_task(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=URL))
    respx.get(GW + f"/api/v1/sites/{SITE}").mock(return_value=httpx.Response(404, json={"error": "not_found"}))
    assert not check_site_health(pilot, SITE).ok
    setup(False)
    respx.get(GW + "/api/v1/tasks/t1").mock(return_value=httpx.Response(200, json={"status": "failed"}))
    r = check_site_health(pilot, SITE, task_id="t1")
    assert r.task_status == "failed" and not r.ok


def test_admin_stub(pilot):
    with pytest.raises(NotImplementedError):
        check_admin_session(object(), SITE)
