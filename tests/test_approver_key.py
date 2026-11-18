import json

import pytest

from fcctl import guard, servers
from fcctl.guard import BadApprovalSignature, Guard, GuardError

AK = "approver-key-aaaaaaaaaaaaaaaa"  # gitleaks:allow


class FakeC:
    server = None

    def call(self, method, path, **kw):
        return {"ok": True}


def mk(tmp_path, **kw):
    kw.setdefault("approver_key", "")
    g = Guard(tmp_path, key="k" * 32, secret="tok", now=lambda: 1000.0, user="bot", **kw)
    guard.set_default_guard(g)
    return g


@pytest.fixture(autouse=True)
def _reset():
    yield
    guard.set_default_guard(None)


def request(g):
    return servers.terminate_server(FakeC(), "srv1")


def test_signed_approve_executes(tmp_path):
    g = mk(tmp_path, approver_key=AK)
    rec = request(g)
    g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    r = g._load()[rec.id]
    assert r.status == "executed" and r.approver_sig and r.approved_at == 1000.0


def test_tampered_record_rejected(tmp_path):
    g = mk(tmp_path, approver_key=AK)
    rec = request(g)
    recs = g._load()
    recs[rec.id].approver_sig, recs[rec.id].approved_at, recs[rec.id].decided_by = "0" * 64, 1.0, "mallory"
    g._save(recs)
    with pytest.raises(BadApprovalSignature):
        g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    assert g._load()[rec.id].status == "pending"


def test_wrong_verifier_key_rejected(tmp_path):
    g = mk(tmp_path, approver_key=AK, verifier_key="other-key-bbbbbbbbbbbbbbbb")
    rec = request(g)
    with pytest.raises(BadApprovalSignature):
        g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())


@pytest.mark.parametrize("same", ["k" * 32, "tok"])
def test_same_secret_refused(tmp_path, same):
    g = mk(tmp_path, approver_key=same)
    rec = request(g)
    with pytest.raises(GuardError) as ei:
        g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    assert "distinct" in ei.value.fix and g._load()[rec.id].status == "pending"


def test_unset_behaves_as_before(tmp_path):
    g = mk(tmp_path)
    rec = request(g)
    g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    r = g._load()[rec.id]
    assert r.status == "executed" and r.approver_sig is None


def test_require_signed_rejects_unsigned(tmp_path):
    g = mk(tmp_path, require_signed=True)
    rec = request(g)
    with pytest.raises(BadApprovalSignature) as ei:
        g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    assert ei.value.why and ei.value.fix


def test_key_never_in_record_or_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("FCCTL_APPROVER_KEY", AK)
    g = mk(tmp_path, approver_key=AK)
    rec = request(g)
    g.approve(rec.id, "alice", "tok", lambda k, s: FakeC())
    assert AK not in g.store_path.read_text() and AK not in g.audit_path.read_text()
    from fcctl.redact import redact_text
    assert AK not in redact_text(f"FCCTL_APPROVER_KEY={AK}")
    json.loads(g.store_path.read_text())


def test_cli_note_once_on_stderr(tmp_path, capsys, monkeypatch):
    from types import SimpleNamespace
    from fcctl.cli import g_approval
    g = mk(tmp_path)
    rec = request(g)
    ctx = SimpleNamespace(guard=g, env={"FCCTL_APPROVAL_TOKEN": "tok"}, client_factory=lambda: (lambda k, s: FakeC()))
    g_approval.approve(ctx, SimpleNamespace(id=rec.id, approver="alice"))
    out = capsys.readouterr()
    assert out.err.count("speed bump") == 1 and "speed bump" not in out.out
