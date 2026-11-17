import asyncio

import httpx

import fcctl
from fcctl._async import tasks as atasks
from fcctl._async._io import Deadline
from fcctl._async.central import CentralClient as ACentral
from fcctl._async.pilot import PilotClient as APilot
from fcctl._sync.central import CentralClient as SCentral
from fcctl._sync.pilot import PilotClient as SPilot
from fcctl._sync import tasks as stasks
from fcctl.request import TaskConflict


class Store:
    sid, csrf, data = "sid1", "csrf1", {}

    def set_central(self, **kw): pass


def make_handler(state):
    def h(req: httpx.Request):
        if "get_bench_link" in req.url.path:
            return httpx.Response(200, json={"message": {"url": "https://gw.test/?sid=JWT"}})
        if req.url.path.endswith("/tasks/t1"):
            state["n"] += 1
            return httpx.Response(200, json={"status": "success" if state["n"] >= 3 else "running"})
        return httpx.Response(200, json={"ok": True})
    return h


def test_async_central_and_pilot_wait(monkeypatch):
    async def fake_sleep(s): state["sleeps"].append(s)
    state = {"n": 0, "sleeps": []}
    monkeypatch.setattr("fcctl._async._io.sleep", fake_sleep)

    async def go():
        t = httpx.MockTransport(make_handler(state))
        c = ACentral(Store(), "team", http=httpx.AsyncClient(transport=t))
        p = APilot(c, "srv", http=httpx.AsyncClient(transport=t))
        assert (await p.get("/x")) == {"ok": True}
        r = await atasks.wait_task(p, "t1", poll=7)
        assert r["status"] == "success"

    asyncio.run(go())
    assert state["sleeps"] == [7, 7]


def test_async_retry_conflict(monkeypatch):
    waits = []

    async def fake_sleep(s): waits.append(s)
    monkeypatch.setattr("fcctl._async._io.sleep", fake_sleep)
    calls = []

    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TaskConflict("c", 409, {})
        return "ok"

    assert asyncio.run(atasks.retry_conflict(fn)) == "ok"
    assert waits == [5.0, 10.0]


def test_sync_twin_and_facades():
    t = httpx.MockTransport(make_handler({"n": 5}))
    c = SCentral(Store(), "team", http=httpx.Client(transport=t))
    p = SPilot(c, "srv", http=httpx.Client(transport=t))
    assert p.get("/x") == {"ok": True}
    assert stasks.wait_task(p, "t1")["status"] == "success"
    assert fcctl.Client and fcctl.AsyncClient
    assert fcctl.Client(Store(), "t").pilot("s").server == "s"


def test_deadline():
    ticks = iter([0, 1, 10])
    d = Deadline(5, clock=lambda: next(ticks))
    assert not d.expired() and d.expired()
