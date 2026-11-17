import json

import pytest

from fcctl import journal as J
from fcctl.guard import Guard, REGISTRY, _Op
from fcctl.reconcile import clear_orphan_markers, reconcile


@pytest.fixture
def jr(tmp_path):
    clock = {"t": 1000.0}
    j = J.Journal(tmp_path / "j", now=lambda: clock["t"])
    j.clock = clock
    return j


def test_happy_path_transitions(jr):
    e = jr.create("create_site", {"a": 1})
    for s in ("dispatching", "submitted", "running", "succeeded"):
        e = jr.transition(e["id"], s)
    assert [h[0] for h in e["history"]] == ["pending", "dispatching", "submitted", "running", "succeeded"]
    assert json.loads((jr.dir / f"{e['id']}.json").read_text())["state"] == "succeeded"


@pytest.mark.parametrize("bad", [("pending", "running"), ("pending", "succeeded"), ("succeeded", "running"),
                                 ("failed", "pending"), ("running", "dispatching")])
def test_invalid_transitions(jr, bad):
    e = jr.create("x")
    path = {"pending": [], "running": ["dispatching", "submitted", "running"],
            "succeeded": ["dispatching", "submitted", "succeeded"], "failed": ["failed"]}[bad[0]]
    for s in path:
        jr.transition(e["id"], s)
    with pytest.raises(J.InvalidTransition):
        jr.transition(e["id"], bad[1])


def test_other_transitions(jr):
    e = jr.create("x"); i = e["id"]
    jr.transition(i, "dispatching"); jr.transition(i, "outcome_unknown"); jr.transition(i, "failed")
    e2 = jr.create("x"); jr.transition(e2["id"], "dispatching"); jr.transition(e2["id"], "submitted")
    jr.transition(e2["id"], "running"); jr.transition(e2["id"], "stuck"); jr.transition(e2["id"], "needs_attention")


def test_execute_persists_dispatching_before_network(jr):
    seen = {}

    def net(entry):
        seen["state_on_disk"] = jr.get(entry["id"])["state"]
        return {"task_id": "T1"}
    jr.execute("create_site", net)
    assert seen["state_on_disk"] == "dispatching"
    e = jr.list()[0]
    assert e["state"] == "submitted" and e["handle"] == "T1"


def test_crash_between_dispatching_and_response(jr):
    def boom(entry):
        raise TimeoutError("read timeout")
    with pytest.raises(TimeoutError):
        jr.execute("create_site", boom)
    e = jr.list()[0]
    assert e["state"] == "outcome_unknown" and "read timeout" in e["error"]


def test_hard_crash_leaves_dispatching_on_disk(jr):
    e = jr.create("create_site")
    jr.transition(e["id"], "dispatching")
    j2 = J.Journal(jr.dir, now=jr.now)  # "restart"
    assert j2.get(e["id"])["state"] == "dispatching"


def test_idempotency_table():
    assert J.idempotency("create_server") == "request_key"
    assert J.idempotency("pilot_post") == "idempotency_key"
    assert J.idempotency("create_site") == "non_idempotent"
    assert J.idempotency("drop_site") == "absent_ok"
    assert J.idempotency("set_config") == "converge"
    assert J.idempotency("unknown") == "non_idempotent"


def test_prune(jr):
    e = jr.create("x"); jr.transition(e["id"], "failed")
    jr.clock["t"] += 31 * 86400
    assert jr.prune() == 1 and jr.list() == []


def _dispatching(jr, op):
    e = jr.create(op); jr.transition(e["id"], "dispatching"); return e["id"]


def test_reconcile_skips_fresh_dispatching(jr):
    i = _dispatching(jr, "create_site")
    r = reconcile(jr, {"create_site": lambda e: "succeeded"}, ttl=300)
    assert r["skipped"] == [i] and jr.get(i)["state"] == "dispatching"


def test_reconcile_succeeded_and_failed(jr):
    a, b = _dispatching(jr, "create_site"), _dispatching(jr, "create_backup")
    jr.clock["t"] += 1000
    r = reconcile(jr, {"create_site": lambda e: ("succeeded", {"x": 1}), "create_backup": lambda e: "failed"})
    assert r["succeeded"] == [a] and r["failed"] == [b]
    assert jr.get(a)["state"] == "succeeded" and jr.get(a)["result"] == {"x": 1}
    assert jr.get(b)["state"] == "failed"


def test_reconcile_unresolved_stays_for_human(jr):
    i = _dispatching(jr, "create_site"); jr.clock["t"] += 1000
    for resolvers in ({}, {"create_site": lambda e: None}, {"create_site": lambda e: 1 / 0}):
        r = reconcile(jr, resolvers)
        assert r["unresolved"] == [i] and jr.get(i)["state"] == "outcome_unknown"


def test_reconcile_never_resubmits_non_idempotent(jr):
    i = _dispatching(jr, "create_site"); jr.clock["t"] += 1000
    calls = []
    r = reconcile(jr, {"create_site": lambda e: "not_happened"}, resubmit=lambda e: calls.append(e) or {})
    assert calls == [] and r["unresolved"] == [i] and jr.get(i)["state"] == "outcome_unknown"


def test_reconcile_resubmits_when_proved_and_safe(jr):
    i = _dispatching(jr, "create_server"); jr.clock["t"] += 1000
    r = reconcile(jr, {"create_server": lambda e: "not_happened"}, resubmit=lambda e: {"handle": "H"})
    assert r["resubmitted"] == [i]
    assert jr.get(i)["state"] == "submitted" and jr.get(i)["handle"] == "H"


def test_reconcile_no_resubmit_callable(jr):
    i = _dispatching(jr, "create_server"); jr.clock["t"] += 1000
    r = reconcile(jr, {"create_server": lambda e: "not_happened"})
    assert r["unresolved"] == [i]


# ---- guard markers ----
@pytest.fixture
def guard(tmp_path):
    return Guard(home=tmp_path / "home", key="k", secret="s", user="alice")


def _approve(g, fn):
    REGISTRY["t_op"] = _Op(fn, "central")
    import fcctl.guard as G
    G.load_all = lambda: None
    rec = g.request("t_op", "tgt", {"n": 1})
    return rec, lambda: g.approve(rec.id, "bob", "s")


def test_marker_carries_journal_id_and_clears_on_success(guard):
    seen = {}

    def fn(client, n):
        seen["markers"] = guard.markers()
        return {"ok": n}
    rec, run = _approve(guard, fn)
    assert run() == {"ok": 1}
    assert seen["markers"][0]["journal_id"] and guard.markers() == []
    j = guard._journal().list()[0]
    assert j["state"] == "succeeded" and j["id"] == seen["markers"][0]["journal_id"]


def test_failure_keeps_marker_and_never_rearms(guard):
    def fn(client, n):
        raise RuntimeError("net down")
    rec, run = _approve(guard, fn)
    with pytest.raises(RuntimeError):
        run()
    assert len(guard.markers()) == 1
    assert guard._journal().list()[0]["state"] == "outcome_unknown"
    from fcctl.guard import NotPending
    with pytest.raises(NotPending):
        guard.approve(rec.id, "bob", "s")  # not re-armed


def test_orphan_marker_cleared_only_after_journal_resolved(guard):
    def fn(client, n):
        raise RuntimeError("net down")
    rec, run = _approve(guard, fn)
    with pytest.raises(RuntimeError):
        run()
    m = guard.markers()[0]
    # make marker orphaned (dead pid)
    p = guard._marker(rec.id)
    p.write_text(json.dumps({**m, "pid": 2 ** 22 + 12345}))
    jr = guard._journal()
    assert clear_orphan_markers(guard, jr)["kept"] == [rec.id]  # journal unresolved -> kept
    jr.transition(m["journal_id"], "succeeded", result={"ok": 1})
    assert clear_orphan_markers(guard, jr)["cleared"] == [rec.id]
    assert guard.markers() == []
    assert guard._load()[rec.id].status == "executed"


def test_live_marker_kept(guard):
    def fn(client, n):
        raise RuntimeError("x")
    rec, run = _approve(guard, fn)
    with pytest.raises(RuntimeError):
        run()
    jr = guard._journal()
    jr.transition(guard.markers()[0]["journal_id"], "failed")
    assert clear_orphan_markers(guard, jr)["kept"] == [rec.id]  # own pid alive, ttl fresh


def test_expired_ttl_marker_cleared_to_failed(guard):
    def fn(client, n):
        raise RuntimeError("x")
    rec, run = _approve(guard, fn)
    with pytest.raises(RuntimeError):
        run()
    jr = guard._journal()
    jr.transition(guard.markers()[0]["journal_id"], "failed", error="gone")
    out = clear_orphan_markers(guard, jr, now=guard.now() + 10 ** 6)
    assert out["cleared"] == [rec.id] and guard._load()[rec.id].status == "failed"
