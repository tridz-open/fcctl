import asyncio
import json

import httpx
import pytest
import respx

from fcctl.errors import AuthError, NotFound
from fcctl._async.siteadmin import SiteAdminError, open_admin

SITE = "a.frappe.cloud"
LINK = f"https://{SITE}/?sid=SECRETLINKTOKEN"


class FakePilot:
    def __init__(self, data):
        self.data, self.calls = data, []

    async def post(self, path, json=None, key=None):
        self.calls.append(path)
        return self.data


def run(c):
    return asyncio.run(c)


def mock_login(router, html='<script>frappe.csrf_token = "CSRFSECRET";</script>'):
    router.get(f"https://{SITE}/").mock(return_value=httpx.Response(
        200, text=html, headers={"set-cookie": "sid=SIDSECRET; Path=/"}))


def test_open_and_crud_v2():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        lst = r.get(f"https://{SITE}/api/v2/resource/ToDo").mock(
            return_value=httpx.Response(200, json={"data": [{"name": "1"}]}))
        ins = r.post(f"https://{SITE}/api/v2/resource/ToDo").mock(
            return_value=httpx.Response(200, json={"data": {"name": "2"}}))
        r.get(f"https://{SITE}/api/v2/resource/ToDo/2").mock(
            return_value=httpx.Response(200, json={"data": {"name": "2"}}))
        r.put(f"https://{SITE}/api/v2/resource/ToDo/2").mock(
            return_value=httpx.Response(200, json={"data": {"name": "2", "x": 1}}))
        r.delete(f"https://{SITE}/api/v2/resource/ToDo/2").mock(
            return_value=httpx.Response(200, json={"data": "ok"}))
        r.post(f"https://{SITE}/api/method/foo.bar").mock(
            return_value=httpx.Response(200, json={"message": 5}))
        r.get(f"https://{SITE}/api/method/ping").mock(return_value=httpx.Response(200, json={"message": "pong"}))

        async def go():
            p = FakePilot({"url": LINK})
            a = await open_admin(p, "srv", SITE)
            assert p.calls == [f"/sites/{SITE}/login"]
            assert await a.get_list("ToDo", filters={"a": 1}, fields=["name"], limit=5) == [{"name": "1"}]
            assert (await a.insert("ToDo", {"d": 1}))["name"] == "2"
            assert (await a.get_doc("ToDo", "2"))["name"] == "2"
            assert (await a.update("ToDo", "2", {"x": 1}))["x"] == 1
            assert await a.delete("ToDo", "2") == "ok"
            assert await a.call("foo.bar", a=1) == 5
            assert await a.ping() is True
            await a.aclose()
        run(go())
        assert ins.calls[0].request.headers["X-Frappe-CSRF-Token"] == "CSRFSECRET"
        q = lst.calls[0].request.url.params
        assert q["limit"] == "5" and json.loads(q["filters"]) == {"a": 1}


def test_fallback_to_v1_on_404():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        r.get(f"https://{SITE}/api/v2/resource/ToDo").mock(return_value=httpx.Response(404, text="nf"))
        v1 = r.get(f"https://{SITE}/api/resource/ToDo").mock(
            return_value=httpx.Response(200, json={"data": [1]}))

        async def go():
            a = await open_admin(FakePilot(LINK), "srv", SITE)
            assert await a.get_list("ToDo") == [1]
            assert a._v2 is False
            await a.aclose()
        run(go())
        assert v1.called


def test_not_found_and_api_error_redacted():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        r.get(f"https://{SITE}/api/v2/resource/ToDo/x").mock(return_value=httpx.Response(404))
        r.get(f"https://{SITE}/api/resource/ToDo/x").mock(return_value=httpx.Response(404))
        r.post(f"https://{SITE}/api/method/boom").mock(
            return_value=httpx.Response(500, text="oops sid=SIDSECRET csrf_token: CSRFSECRET ?sid=SECRETLINKTOKEN"))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            with pytest.raises(NotFound):
                await a.get_doc("ToDo", "x")
            with pytest.raises(SiteAdminError) as ei:
                await a.call("boom")
            blob = str(ei.value) + str(ei.value.body) + str(ei.value.what)
            for s in ("SIDSECRET", "CSRFSECRET", "SECRETLINKTOKEN"):
                assert s not in blob
                assert s not in repr(a) and s not in str(a)
            await a.aclose()
        run(go())


def test_login_failure_and_missing_csrf():
    with respx.mock(assert_all_called=False) as r:
        r.get(f"https://{SITE}/").mock(return_value=httpx.Response(401, text="no"))
        with pytest.raises(AuthError) as ei:
            run(open_admin(FakePilot({"url": LINK}), "srv", SITE))
        assert "SECRETLINKTOKEN" not in str(ei.value)
    with respx.mock(assert_all_called=False) as r:
        mock_login(r, html="<html></html>")
        r.get(f"https://{SITE}/app").mock(return_value=httpx.Response(200, text="x"))
        r.get(f"https://{SITE}/api/method/frappe.sessions.get_boot").mock(
            return_value=httpx.Response(200, json={"message": {"csrf_token": "FROMBOOT"}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            assert a._csrf == "FROMBOOT"
            await a.aclose()
        run(go())


def test_pilot_response_without_link_and_ping_false():
    with pytest.raises(AuthError):
        run(open_admin(FakePilot({}), "srv", SITE))
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        r.get(f"https://{SITE}/api/method/ping").mock(return_value=httpx.Response(503))

        async def go():
            a = await open_admin(FakePilot(LINK), "srv", SITE)
            assert await a.ping() is False
            await a.aclose()
        run(go())


def test_set_admin_password_and_force_reset():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        put_user = r.put(f"https://{SITE}/api/v2/resource/User/Administrator").mock(
            return_value=httpx.Response(200, json={"data": {"name": "Administrator"}}))
        r.get(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {"force_user_to_reset_password": 0}}))
        put_ss = r.put(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            await a.set_admin_password("N3w-Secret!")
            body = json.loads(put_user.calls[-1].request.content)
            assert body == {"new_password": "N3w-Secret!"}
            assert put_user.calls[-1].request.headers["X-Frappe-CSRF-Token"] == "CSRFSECRET"
            await a.force_password_reset()
            assert json.loads(put_ss.calls[-1].request.content) == {"force_user_to_reset_password": 1}
            assert json.loads(put_user.calls[-1].request.content) == {"last_password_reset_date": "2000-01-01"}
            with pytest.raises(SiteAdminError):
                await a.set_admin_password("")
            await a.aclose()
        run(go())


def test_set_admin_password_error_does_not_leak_password():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        r.put(f"https://{SITE}/api/v2/resource/User/Administrator").mock(
            return_value=httpx.Response(417, json={"exc": "weak"}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            with pytest.raises(SiteAdminError) as e:
                await a.set_admin_password("N3w-Secret!")
            assert "N3w-Secret!" not in str(e.value) and "N3w-Secret!" not in repr(e.value)
            await a.aclose()
        run(go())


def test_fake_site_admin_admin_password():
    from fcctl.testing import FakeSiteAdmin
    f = FakeSiteAdmin("x.fake")
    f.set_admin_password("pw1")
    f.force_password_reset()
    assert f.check_password("Administrator", "pw1")
    assert f.force_user_to_reset_password == 1
    assert f.last_password_reset_date["Administrator"] == "2000-01-01"
    assert "pw1" not in repr(f.calls)


def test_create_password_setup_link_stores_hash_and_timestamp():
    import hashlib
    from urllib.parse import parse_qs, urlparse
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        put_user = r.put(f"https://{SITE}/api/v2/resource/User/Administrator").mock(
            return_value=httpx.Response(200, json={"data": {"name": "Administrator"}}))
        r.get(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {"reset_password_link_expiry_duration": 0,
                                                            "time_zone": "Asia/Kolkata"}}))
        put_ss = r.put(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            url = await a.create_password_setup_link()
            u = urlparse(url)
            assert u.scheme == "https" and u.netloc == SITE and u.path == "/update-password"
            key = parse_qs(u.query)["key"][0]
            assert len(key) == 56
            body = json.loads(put_user.calls[-1].request.content)
            assert body["reset_password_key"] == hashlib.sha256(key.encode()).hexdigest()
            assert key not in put_user.calls[-1].request.content.decode()
            assert len(body["last_reset_password_key_generated_on"]) == 26
            assert json.loads(put_ss.calls[-1].request.content) == {"reset_password_link_expiry_duration": 86400}
            assert key not in repr(a)
            url2 = await a.create_password_setup_link()
            assert url2 != url
            await a.aclose()
        run(go())


def test_create_password_setup_link_keeps_existing_expiry():
    with respx.mock(assert_all_called=False) as r:
        mock_login(r)
        r.put(f"https://{SITE}/api/v2/resource/User/Administrator").mock(
            return_value=httpx.Response(200, json={"data": {}}))
        r.get(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {"reset_password_link_expiry_duration": 3600, "time_zone": "UTC"}}))
        put_ss = r.put(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
            return_value=httpx.Response(200, json={"data": {}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            await a.create_password_setup_link()
            assert not put_ss.called
            await a.aclose()
        run(go())


def test_fake_site_admin_setup_link():
    from fcctl.testing import FakeSiteAdmin
    f = FakeSiteAdmin("x.fake")
    url = f.create_password_setup_link()
    assert url.startswith("https://x.fake/update-password?key=")
    assert url.split("key=")[1] not in repr(f.calls)
    assert f.reset_password_keys["Administrator"]


def _link_env(r, ss, put_user_ok=True):
    mock_login(r)
    put_user = r.put(f"https://{SITE}/api/v2/resource/User/Administrator").mock(
        return_value=httpx.Response(200, json={"data": {}}))
    r.get(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
        return_value=httpx.Response(200, json={"data": ss}))
    put_ss = r.put(f"https://{SITE}/api/v2/resource/System Settings/System Settings").mock(
        return_value=httpx.Response(200, json={"data": {}}))
    return put_user, put_ss


def test_setup_link_clamps_long_expiry_and_returns_expires_in():
    with respx.mock(assert_all_called=False) as r:
        put_user, put_ss = _link_env(r, {"reset_password_link_expiry_duration": 999999,
                                         "time_zone": "UTC"})

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            res = await a.create_password_setup_link_ex()
            assert res["expires_in"] == 86400 and res["url"].startswith(f"https://{SITE}/update-password?key=")
            assert json.loads(put_ss.calls[-1].request.content) == {"reset_password_link_expiry_duration": 86400}
            res = await a.create_password_setup_link_ex(max_expiry_seconds=600)
            assert res["expires_in"] == 600
            assert isinstance(await a.create_password_setup_link(), str)
            await a.aclose()
        run(go())


def test_setup_link_short_expiry_untouched():
    with respx.mock(assert_all_called=False) as r:
        _, put_ss = _link_env(r, {"reset_password_link_expiry_duration": 3600, "time_zone": "UTC"})

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            assert (await a.create_password_setup_link_ex())["expires_in"] == 3600
            assert not put_ss.called
            await a.aclose()
        run(go())


def _ts(put_user):
    from datetime import datetime
    body = json.loads(put_user.calls[-1].request.content)
    return datetime.strptime(body["last_reset_password_key_generated_on"], "%Y-%m-%d %H:%M:%S.%f")


def test_setup_link_tz_fallback_to_site_default():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    with respx.mock(assert_all_called=False) as r:
        put_user, _ = _link_env(r, {"reset_password_link_expiry_duration": 3600, "time_zone": ""})
        gv = r.get(f"https://{SITE}/api/method/frappe.client.get_value").mock(
            return_value=httpx.Response(200, json={"message": {"time_zone": "Asia/Kolkata"}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            await a.create_password_setup_link_ex()
            assert gv.called
            exp = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
            assert abs((_ts(put_user) - exp).total_seconds()) < 30
            await a.aclose()
        run(go())


def test_setup_link_tz_unknown_falls_back_to_utc_with_warning():
    from datetime import datetime, timezone
    with respx.mock(assert_all_called=False) as r:
        put_user, _ = _link_env(r, {"reset_password_link_expiry_duration": 3600, "time_zone": "Mars/Base"})
        r.get(f"https://{SITE}/api/method/frappe.client.get_value").mock(
            return_value=httpx.Response(200, json={"message": {"time_zone": None}}))

        async def go():
            a = await open_admin(FakePilot({"url": LINK}), "srv", SITE)
            with pytest.warns(RuntimeWarning, match="UTC"):
                await a.create_password_setup_link_ex()
            exp = datetime.now(timezone.utc).replace(tzinfo=None)
            assert abs((_ts(put_user) - exp).total_seconds()) < 30
            await a.aclose()
        run(go())
