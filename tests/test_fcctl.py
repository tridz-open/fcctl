import httpx
import pytest
import respx

from fcctl import sites, tasks
from fcctl.reauth import Reauther
from fcctl.request import (AuthError, CloudflareChallenge, CsrfError, ReauthFailed, TaskConflict,
                           TaskFailed, is_cloudflare_challenge)
from fcctl.session_store import SessionStore
from tests.conftest import BASE, GW, LINK, PROFILE

JWT_URL = {"data": {"url": GW + "/?sid=JWT1"}}


def test_store_roundtrip_encrypted(store, tmp_path):
    assert b"sid0" not in store.path.read_bytes()
    s2 = SessionStore(store.path, key=store._key)
    assert s2.sid == 'sid0'
    assert store.sid == "sid0" and store.csrf == "csrf0"


@respx.mock
def test_central_request_builder_and_unwrap(central):
    r = respx.get(BASE + "/api/v2/method/central.api.servers.registry").mock(
        return_value=httpx.Response(200, json={"data": {"servers": []}}))
    assert central.call("GET", "central.api.servers.registry") == {"servers": []}
    req = r.calls[0].request
    assert req.url.params["team"] == "TEAM-1"
    assert req.headers["x-frappe-csrf-token"] == "csrf0" and "sid=sid0" in req.headers["cookie"]


@respx.mock
def test_post_injects_team_and_captures_rotated_sid(central, store):
    r = respx.post(BASE + "/api/v2/method/central.api.snapshots.take_snapshot").mock(
        return_value=httpx.Response(200, json={"data": 1}, headers={"set-cookie": "sid=sid1; Max-Age=612000; Path=/"}))
    central.call("POST", "central.api.snapshots.take_snapshot", json={"resource_id": "s"})
    import json
    assert json.loads(r.calls[0].request.content)["team"] == "TEAM-1"
    assert store.sid == "sid1"


@respx.mock
def test_typed_errors(central):
    u = BASE + "/api/v2/method/x.y"
    respx.get(u).mock(return_value=httpx.Response(401, json={"exc_type": "AuthenticationError"}))
    with pytest.raises(AuthError):
        central.call("GET", "x.y")
    respx.get(u).mock(return_value=httpx.Response(403, json={"exc_type": "CSRFTokenError"}))
    with pytest.raises(CsrfError):
        central.call("GET", "x.y")


@respx.mock
def test_cloudflare_detection(central):
    cf = httpx.Response(403, text="<!DOCTYPE html><title>Just a moment...</title>", headers={"cf-ray": "1", "content-type": "text/html"})
    assert is_cloudflare_challenge(cf)
    assert not is_cloudflare_challenge(httpx.Response(403, json={"exc_type": "PermissionError"}))
    respx.get(BASE + "/api/v2/method/a.b").mock(return_value=cf)
    with pytest.raises(CloudflareChallenge):
        central.call("GET", "a.b")


@respx.mock
def test_reauth_order_csrf_alert(central, store):
    # profile always fails -> csrf step tried, then alert
    respx.get(PROFILE).mock(return_value=httpx.Response(401, json={"exc_type": "AuthenticationError"}))
    respx.get(BASE + "/dashboard").mock(return_value=httpx.Response(200, text="no token"))
    class Al:
        n = 0
        def send(self, *a, **k): Al.n += 1
    ra = Reauther(store, central, alerter=Al())
    with pytest.raises(ReauthFailed):
        ra.recover("central")
    assert ra.trail == ["csrf", "alert"] and Al.n == 1


@respx.mock
def test_reauth_stops_at_first_success_and_retries_call(central, store):
    respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"data": {}}, headers={"x-frappe-csrf-token": "fresh"}))
    seq = respx.get(BASE + "/api/v2/method/a.b").mock(side_effect=[
        httpx.Response(403, json={"exc_type": "CSRFTokenError"}), httpx.Response(200, json={"data": 7})])
    ra = Reauther(store, central)
    assert central.call("GET", "a.b") == 7
    assert ra.trail == ["csrf"] and seq.call_count == 2


@respx.mock
def test_pilot_mint_bearer_and_remint_on_401(pilot, store):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    route = respx.get(GW + "/api/v1/sites").mock(side_effect=[
        httpx.Response(401, json={"error": "unauthorized"}), httpx.Response(200, json=[{"name": "s"}])])
    assert sites.list_sites(pilot) == [{"name": "s"}]
    assert route.calls[0].request.headers["authorization"] == "Bearer JWT1"
    assert respx.calls.call_count == 4  # mint, 401, mint, 200
    assert store.get_pilot("server-abc") is None  # pilot tokens are never persisted
    assert pilot.session()["gateway"] == GW


@respx.mock
def test_post_sends_idempotency_key(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    r = respx.post(GW + "/api/v1/sites").mock(return_value=httpx.Response(202, json={"task_id": "t1", "status": "queued"}))
    assert sites.create_site(pilot, "a.example.com", ["erpnext"])["task_id"] == "t1"
    assert r.calls[0].request.headers["idempotency-key"]


@respx.mock
def test_409_retry_backoff(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    r = respx.post(GW + "/api/v1/sites/s/actions/migrate").mock(side_effect=[
        httpx.Response(409, json={"error": {"code": "task_conflict"}}),
        httpx.Response(409, json={"error": {"code": "task_conflict"}}),
        httpx.Response(202, json={"task_id": "t9"})])
    waits = []
    out = tasks.retry_conflict(lambda: sites.migrate(pilot, "s"), sleep=waits.append)
    assert out["task_id"] == "t9" and waits == [5.0, 10.0]
    r.side_effect = [httpx.Response(409, json={"error": {"code": "task_conflict"}})] * 3
    with pytest.raises(TaskConflict):
        tasks.retry_conflict(lambda: sites.migrate(pilot, "s"), attempts=3, sleep=lambda s: None)


@respx.mock
def test_wait_task_polls_until_success_and_failure(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    respx.get(GW + "/api/v1/tasks/t1").mock(side_effect=[
        httpx.Response(200, json={"status": "queued"}), httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "success"})])
    sleeps = []
    assert tasks.wait_task(pilot, "t1", poll=15, sleep=sleeps.append)["status"] == "success"
    assert sleeps == [15, 15]
    respx.get(GW + "/api/v1/tasks/t2").mock(return_value=httpx.Response(200, json={"status": "failed", "failure": {"code": "x"}}))
    with pytest.raises(TaskFailed):
        tasks.wait_task(pilot, "t2", sleep=lambda s: None)


@respx.mock
def test_wait_task_timeout(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    respx.get(GW + "/api/v1/tasks/t3").mock(return_value=httpx.Response(200, json={"status": "running"}))
    t = iter(range(0, 1000, 100))
    with pytest.raises(TimeoutError):
        tasks.wait_task(pilot, "t3", timeout=250, sleep=lambda s: None, clock=lambda: next(t))


@respx.mock
def test_maintenance_patch_body(pilot):
    respx.get(LINK).mock(return_value=httpx.Response(200, json=JWT_URL))
    r = respx.patch(GW + "/api/v1/sites/s/configuration").mock(return_value=httpx.Response(200, json={}))
    sites.set_maintenance(pilot, "s", True)
    import json
    assert json.loads(r.calls[0].request.content) == {"maintenance_mode": 1, "pause_scheduler": 1}


def test_imap_extract():
    from fcctl.auth import ImapCodePoller
    assert ImapCodePoller.extract("Your code is 482913. Valid 10 min") == "482913"
