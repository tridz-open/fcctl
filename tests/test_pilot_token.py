import threading

import httpx
import respx

from fcctl.request import PilotClient
from tests.conftest import GW, LINK

URL = {"data": {"url": GW + "/?sid=JWT1"}}


class Clock:
    t = 0.0
    def __call__(self): return self.t


@respx.mock
def test_lazy_no_mint_until_use_and_not_persisted(central, store):
    link = respx.get(LINK).mock(return_value=httpx.Response(200, json=URL))
    ck = Clock()
    p = PilotClient(central, "srv", clock=ck)
    assert link.call_count == 0
    respx.get(GW + "/api/v1/x").mock(return_value=httpx.Response(200, json={}))
    p.get("/x"); p.get("/x")
    assert link.call_count == 1
    assert "pilots" not in store.data and store.get_pilot("srv") is None


@respx.mock
def test_proactive_remint_after_max_age(central):
    link = respx.get(LINK).mock(return_value=httpx.Response(200, json=URL))
    respx.get(GW + "/api/v1/x").mock(return_value=httpx.Response(200, json={}))
    ck = Clock()
    p = PilotClient(central, "srv", clock=ck, max_age=120)
    p.get("/x"); ck.t = 119; p.get("/x")
    assert link.call_count == 1
    ck.t = 121; p.get("/x")
    assert link.call_count == 2


@respx.mock
def test_remint_once_on_401_then_raise(central):
    import pytest
    from fcctl.request import AuthError
    link = respx.get(LINK).mock(return_value=httpx.Response(200, json=URL))
    respx.get(GW + "/api/v1/x").mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
    with pytest.raises(AuthError):
        PilotClient(central, "srv").get("/x")
    assert link.call_count == 2


@respx.mock
def test_single_flight_mint(central):
    import time
    def slow(request):
        time.sleep(0.1)
        return httpx.Response(200, json=URL)
    link = respx.get(LINK).mock(side_effect=slow)
    respx.get(GW + "/api/v1/x").mock(return_value=httpx.Response(200, json={}))
    ps = [PilotClient(central, "srv") for _ in range(5)]
    ts = [threading.Thread(target=p.get, args=("/x",)) for p in ps]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert link.call_count == 1
