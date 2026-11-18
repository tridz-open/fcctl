import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx

from fcctl import retry
from fcctl._async.central import CentralClient as ACentral
from fcctl._async.pilot import PilotClient as APilot
from fcctl.request import CentralClient, PilotClient, RateLimited
from fcctl.retry import RetryPolicy

BASE = "https://dashboard.frappe.io"
GW = "https://gw.example.com"
LINK = BASE + "/api/v2/method/central.api.sso.get_bench_link"
PING = BASE + "/api/v2/method/x.y"
LINKRESP = httpx.Response(200, json={"message": {"url": GW + "/?sid=JWT"}})


class Sleeps(list):
    def __call__(self, s):
        self.append(s)


def policy(**kw):
    kw.setdefault("rng", lambda: 1.0)
    return RetryPolicy(**kw)


def make(store, sl, **kw):
    c = CentralClient(store, team="T", retry=policy(**kw), sleep=sl)
    p = PilotClient(c, "srv", retry=policy(**kw), sleep=sl)
    return c, p


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as r:
        r.get(LINK).mock(return_value=LINKRESP)
        yield r


def test_429_then_200_honours_retry_after(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl)
    route = mock.get(PING).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "7"}),
                                             httpx.Response(200, json={"message": "ok"})])
    assert c.get("x.y") == "ok"
    assert sl == [7.0] and route.call_count == 2


def test_retry_after_capped_and_http_date(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl, cap=10.0)
    mock.get(PING).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "999"}),
                                     httpx.Response(200, json={"message": 1})])
    c.get("x.y")
    assert sl == [10.0]
    when = datetime.now(timezone.utc) + timedelta(seconds=5)
    got = retry.parse_retry_after(format_datetime(when, usegmt=True))
    assert 3 <= got <= 5
    assert retry.parse_retry_after("garbage") is None and retry.parse_retry_after(None) is None


def test_503_twice_then_200(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.get(GW + "/api/v1/x").mock(side_effect=[httpx.Response(503), httpx.Response(503),
                                                          httpx.Response(200, json={"a": 1})])
    assert p.get("/x") == {"a": 1}
    assert sl == [0.5, 1.0] and route.call_count == 3  # rng=1.0 -> full ceiling


def test_connect_error_then_200(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl)
    mock.get(PING).mock(side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"message": 2})])
    assert c.get("x.y") == 2 and len(sl) == 1


def test_exhaustion_raises_rate_limited_with_attempts(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl)
    route = mock.get(PING).mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited) as ei:
        c.get("x.y")
    assert route.call_count == 4 and len(sl) == 3
    assert "4 attempts" in ei.value.why


def test_network_error_exhaustion_reraises(store, mock):
    c, _ = make(store, Sleeps(), max_retries=1)
    mock.get(PING).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(httpx.ReadTimeout):
        c.get("x.y")


def test_post_without_key_not_retried(store, mock):
    sl = Sleeps()
    c, p = make(store, sl)
    route = mock.post(PING).mock(return_value=httpx.Response(503))
    with pytest.raises(Exception):
        c.post("x.y", a=1)
    assert route.call_count == 1 and sl == []
    r2 = mock.post(GW + "/api/v1/y").mock(return_value=httpx.Response(503))
    with pytest.raises(Exception):
        p.request("POST", "/y", json={})
    assert r2.call_count == 1 and sl == []


def test_post_with_key_5xx_not_retried(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.post(GW + "/api/v1/y").mock(side_effect=[httpx.Response(502), httpx.Response(200, json={"ok": 1})])
    with pytest.raises(Exception):
        p.post("/y", {}, key="k1")
    assert route.call_count == 1 and sl == []
    assert route.calls[0].request.headers["Idempotency-Key"] == "k1"


def test_post_read_timeout_not_retried(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.post(GW + "/api/v1/y").mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(httpx.ReadTimeout):
        p.post("/y", {})
    assert route.call_count == 1 and sl == []


def test_post_connect_error_retried(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.post(GW + "/api/v1/y").mock(side_effect=[httpx.ConnectError("c"), httpx.Response(200, json={"ok": 1})])
    assert p.post("/y", {}) == {"ok": 1}
    assert route.call_count == 2 and len(sl) == 1


def test_post_429_retried(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.post(GW + "/api/v1/y").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    assert p.post("/y", {}) == {"ok": 1}
    assert route.call_count == 2


def test_get_5xx_retried(store, mock):
    sl = Sleeps()
    _, p = make(store, sl)
    route = mock.get(GW + "/api/v1/y").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})])
    assert p.get("/y") == {"ok": 1}
    assert route.call_count == 2


def test_async_post_read_timeout_not_retried_connect_retried(store):
    async def go():
        async def asleep(_): pass

        c = ACentral(store, team="T", retry=policy(), sleep=asleep)
        p = APilot(c, "srv", retry=policy(), sleep=asleep)
        with respx.mock(assert_all_called=False) as m:
            m.get(LINK).mock(return_value=LINKRESP)
            r = m.post(GW + "/api/v1/y").mock(side_effect=httpx.ReadTimeout("t"))
            with pytest.raises(httpx.ReadTimeout):
                await p.post("/y", {})
            assert r.call_count == 1
            r2 = m.post(GW + "/api/v1/z").mock(side_effect=[httpx.ConnectError("c"), httpx.Response(200, json={"ok": 1})])
            assert await p.post("/z", {}) == {"ok": 1} and r2.call_count == 2
    asyncio.run(go())


def test_client_errors_not_retried(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl)
    route = mock.get(PING).mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(Exception):
        c.get("x.y")
    assert route.call_count == 1 and sl == []


def test_jitter_bounded():
    p = RetryPolicy(rng=lambda: 0.5)
    assert p.backoff(1) == 0.25 and p.backoff(2) == 0.5
    for i in range(1, 12):
        for r in (0.0, 0.999):
            assert 0 <= RetryPolicy(rng=lambda r=r: r).backoff(i) <= 30.0
    assert RetryPolicy(rng=lambda: 1.0).backoff(20) == 30.0


def test_max_retries_zero_disables(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl, max_retries=0)
    route = mock.get(PING).mock(return_value=httpx.Response(503))
    with pytest.raises(Exception):
        c.get("x.y")
    assert route.call_count == 1 and sl == []


def test_env_and_cli_default(monkeypatch):
    monkeypatch.setenv("FCCTL_MAX_RETRIES", "0")
    assert retry.default_policy().max_retries == 0
    monkeypatch.setenv("FCCTL_MAX_RETRIES", "junk")
    assert retry.default_policy().max_retries == 3
    retry.set_default(RetryPolicy(max_retries=5))
    try:
        assert retry.default_policy().max_retries == 5
    finally:
        retry.set_default(None)


def test_reauth_still_works_with_retry(store, mock):
    sl = Sleeps()
    c, _ = make(store, sl)
    calls = []

    class R:
        def recover(self, **kw): calls.append(1)
    c.reauth = R()
    mock.get(PING).mock(side_effect=[httpx.Response(503), httpx.Response(401, json={}),
                                     httpx.Response(200, json={"message": "z"})])
    assert c.get("x.y") == "z" and calls == [1]


def test_async_path(store):
    sl = []

    async def asleep(s): sl.append(s)

    async def go():
        with respx.mock(assert_all_called=False) as r:
            r.get(LINK).mock(return_value=LINKRESP)
            r.get(PING).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "2"}),
                                          httpx.Response(200, json={"message": "a"})])
            r.get(GW + "/api/v1/x").mock(side_effect=[httpx.ConnectError("x"), httpx.Response(200, json={"v": 1})])
            r.post(GW + "/api/v1/n").mock(return_value=httpx.Response(503))
            c = ACentral(store, "T", retry=policy(), sleep=asleep)
            p = APilot(c, "srv", retry=policy(), sleep=asleep)
            assert await c.get("x.y") == "a"
            assert await p.get("/x") == {"v": 1}
            with pytest.raises(Exception):
                await p.request("POST", "/n", json={})
            await c.aclose()
    asyncio.run(go())
    assert sl == [2.0, 0.5]
