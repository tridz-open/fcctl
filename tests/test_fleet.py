import threading
import time
from types import SimpleNamespace

import pytest

from fcctl import ops
from fcctl import liveness
from fcctl.cli import g_fleet
from fcctl.errors import UsageError, WaitTimeout


class J:
    def __init__(self, items):
        self.e = {"id": "r", "state": "running", "fleet": {"op": "site.clear_cache", "items": items}}
    def get(self, rid): return self.e
    def _write(self, e): self.e = e
    def now(self): return "t"
    def transition(self, rid, s, **k): self.e["state"] = s


class Ctx:
    def __init__(self, items):
        self.journal, self.env, self.central = J(items), {}, None
    def pilot(self, s): return object()
    def op_context(self, *a, **k): return None
    def render(self, x): pass
    def info(self, m): pass


def items(*pairs):
    return [{"server": s, "site": n, "state": "pending", "task_id": None, "error": None} for s, n in pairs]


def run(monkeypatch, its, parallel=4, timeout=None, submit=None, wait=None):
    cur = {"n": 0, "max": 0}
    lk = threading.Lock()

    def run_op(op_id, site, call, ctx=None):
        with lk:
            cur["n"] += 1; cur["max"] = max(cur["max"], cur["n"])
        try:
            if submit: submit(site)
        finally:
            with lk: cur["n"] -= 1
        return {"task_id": "t-" + site}
    monkeypatch.setattr(ops, "run_op", run_op)
    monkeypatch.setattr(liveness, "wait", wait or (lambda *a, **k: {}))
    ctx = Ctx(its)
    rc = g_fleet._execute(ctx, SimpleNamespace(parallel=parallel, timeout=timeout), "r", False)
    return rc, cur, ctx


def test_two_servers_submit_concurrently(monkeypatch):
    barrier = threading.Barrier(2, timeout=5)
    rc, cur, _ = run(monkeypatch, items(("a", "s1"), ("b", "s2")), submit=lambda s: barrier.wait())
    assert rc == 0 and cur["max"] == 2


def test_same_server_serialises(monkeypatch):
    rc, cur, _ = run(monkeypatch, items(("a", "s1"), ("a", "s2"), ("a", "s3")), submit=lambda s: time.sleep(0.05))
    assert rc == 0 and cur["max"] == 1


def test_parallel_bound(monkeypatch):
    active = {"n": 0, "max": 0}
    lk = threading.Lock()

    def wait(*a, **k):
        with lk:
            active["n"] += 1; active["max"] = max(active["max"], active["n"])
        time.sleep(0.05)
        with lk: active["n"] -= 1
    its = items(*[(f"s{i}", f"x{i}") for i in range(8)])
    rc, _, _ = run(monkeypatch, its, parallel=2, wait=wait)
    assert rc == 0 and active["max"] <= 2


@pytest.mark.parametrize("n", [33, 0, -1])
def test_parallel_range_rejected(n):
    with pytest.raises(UsageError):
        g_fleet._parallel(n)
    assert g_fleet._parallel(32) == 32


def test_timeout_exit8_task_still_running(monkeypatch):
    seen = {}

    def wait(h, p, timeout=None, **k):
        seen["t"] = timeout
        raise WaitTimeout("deadline")
    rc, _, ctx = run(monkeypatch, items(("a", "s1")), timeout=7, wait=wait)
    it = ctx.journal.e["fleet"]["items"][0]
    assert seen["t"] == 7 and rc == 8 and it["state"] == "submitted" and it["task_id"] == "t-s1"
