import pytest

from fcctl import policy as pol
from fcctl.audit import AuditLog
from fcctl.guard import Guard
from fcctl.journal import Journal
from fcctl.ops import ApprovalPending, OpContext, PolicyDenied, run_op


def ctx(tmp_path, policy="guarded", **kw):
    eff = pol.EffectivePolicy("a@x", "T", policy, allow=frozenset(kw.pop("allow", ())))
    return OpContext(eff=eff, journal=Journal(tmp_path / "j"), audit=AuditLog(tmp_path / "a.jsonl"),
                     guard=Guard(home=tmp_path / "g", key="k", secret="s", user="u"), profile="p", **kw)


def test_allow_executes_and_audits(tmp_path):
    c = ctx(tmp_path)
    assert run_op("site.create", "s1", lambda: {"task_id": "t9"}, ctx=c) == {"task_id": "t9"}
    rows = c.audit.list()
    assert [r["outcome"] for r in rows] == ["started", "submitted"] and rows[1]["task_id"] == "t9"
    assert c.journal.list()[0]["state"] == "submitted"


def test_read_not_audited(tmp_path):
    c = ctx(tmp_path)
    assert run_op("site.list", "-", lambda: [1], ctx=c) == [1]
    assert c.audit.list() == [] and c.journal.list() == []


def test_deny_audited(tmp_path):
    c = ctx(tmp_path, "read_only")
    called = []
    with pytest.raises(PolicyDenied) as e:
        run_op("site.create", "s1", lambda: called.append(1), ctx=c)
    assert e.value.exit_code == 4 and not called
    assert c.audit.list()[0]["outcome"] == "denied"


def test_destructive_files_approval(tmp_path):
    c = ctx(tmp_path)
    called = []
    with pytest.raises(ApprovalPending) as e:
        run_op("server.terminate", "srv", lambda: called.append(1), ctx=c)
    assert e.value.exit_code == 7 and e.value.approval_id and not called
    assert c.guard.list_pending()[0].id == e.value.approval_id
    assert c.audit.list()[0]["approval_id"] == e.value.approval_id


def test_dry_run_no_side_effects(tmp_path):
    c = ctx(tmp_path, dry_run=True)
    called = []
    plan = run_op("server.terminate", "srv", lambda: called.append(1), ctx=c)
    assert plan["dry_run"] and plan["decision"] == "NeedsApproval" and not called
    assert c.audit.list() == [] and c.journal.list() == [] and c.guard.list_pending() == []


def test_failure_is_outcome_unknown(tmp_path):
    c = ctx(tmp_path)
    def boom(): raise RuntimeError("net")
    with pytest.raises(RuntimeError):
        run_op("site.create", "s", boom, ctx=c)
    assert c.audit.list()[-1]["outcome"] == "outcome_unknown"
    assert c.journal.list()[0]["state"] == "outcome_unknown"


def test_protected_allow_list(tmp_path):
    c = ctx(tmp_path, "protected", allow=["backup.create"])
    assert run_op("backup.create", "s", lambda: {"id": "b"}, ctx=c) == {"id": "b"}
