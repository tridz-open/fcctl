import json

import pytest

from fcctl import guard, servers, sites
from fcctl.alerts import Alerter
from fcctl.guard import Guard, totp

T0 = 1_000_000.0


class Clock:
    t = T0

    def __call__(self):
        return self.t


class FakeC:
    def __init__(self):
        self.calls = []

    def call(self, method, path, **kw):
        self.calls.append((method, path, kw))
        return {"ok": True}


@pytest.fixture
def env(tmp_path):
    clock, al = Clock(), Alerter(env={})
    g = Guard(tmp_path, key="k" * 32, secret="GEZDGNBVGY3TQOJQ", alerter=al, now=clock, user="bot")  # gitleaks:allow
    guard.set_default_guard(g)
    yield g, clock, al, FakeC()
    guard.set_default_guard(None)


def audit(g):
    return [json.loads(l) for l in g.audit_path.read_text().splitlines()]


def test_pending_created_not_executed(env):
    g, _, al, c = env
    p = servers.terminate_server(c, "server-1")
    assert p.status == "pending" and p.op == "terminate_server" and p.target == "server-1"
    assert p.requested_by == "bot" and p.expires_at == T0 + 1800
    assert c.calls == [] and [r.id for r in g.list_pending()] == [p.id]
    assert al.sent and "terminate_server" in al.sent[0][0]


def test_all_destructive_ops_guarded(env):
    guard.load_all()
    for op in ("terminate_server", "delete_snapshots", "drop_site", "delete_bucket", "uninstall_app",
               "restore_backup", "remove_app", "switch_branch"):
        assert op in guard.REGISTRY


def test_self_approval_rejected(env):
    g, _, _, c = env
    p = servers.terminate_server(c, "s")
    with pytest.raises(guard.SelfApproval):
        g.approve(p.id, "bot", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    assert c.calls == [] and "self_approval_rejected" in [e["event"] for e in audit(g)]


def test_bad_token_rejected_totp_accepted(env):
    g, clock, _, c = env
    p = servers.terminate_server(c, "s")
    with pytest.raises(guard.BadToken):
        g.approve(p.id, "alice", "nope", lambda k, s: c)
    assert c.calls == []
    g.approve(p.id, "alice", totp("GEZDGNBVGY3TQOJQ", clock.t), lambda k, s: c)
    assert len(c.calls) == 1


def test_expired_rejected(env):
    g, clock, _, c = env
    p = servers.terminate_server(c, "s")
    clock.t += 1801
    with pytest.raises(guard.Expired):
        g.approve(p.id, "alice", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    assert c.calls == [] and g.list_pending() == []


def test_tampered_store_rejected(env):
    g, _, _, c = env
    p = servers.terminate_server(c, "s")
    d = json.loads(g.store_path.read_text())
    d[p.id]["rec"]["target"] = "server-OTHER"
    g.store_path.write_text(json.dumps(d))
    with pytest.raises(guard.Tampered):
        g.approve(p.id, "alice", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    with pytest.raises(guard.Tampered):
        g.list_pending()
    assert c.calls == []


def test_approve_executes_exactly_once(env):
    g, _, _, c = env
    p = servers.terminate_server(c, "server-9", take_snapshot=True)
    r1 = g.approve(p.id, "alice", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    r2 = g.approve(p.id, "bob", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    assert r1 == r2 == {"ok": True} and len(c.calls) == 1
    assert c.calls[0][2]["json"] == {"resource_id": "server-9", "take_snapshot": True}


def test_pilot_op_and_deny(env):
    g, _, _, _ = env

    class P:
        server = "server-x"

        def __init__(self):
            self.calls = []

        def request(self, *a, **k):
            self.calls.append((a, k))
            return {"task_id": "t"}

    p = P()
    pa = sites.drop_site(p, "a.example.com", no_backup=True)
    assert pa.kind == "pilot" and pa.server == "server-x" and p.calls == []
    g.approve(pa.id, "alice", "GEZDGNBVGY3TQOJQ", lambda kind, srv: p)
    assert p.calls[0][0] == ("DELETE", "/sites/a.example.com")
    d = sites.drop_site(p, "b.example.com")
    g.deny(d.id, "alice")
    with pytest.raises(guard.NotPending):
        g.approve(d.id, "alice", "GEZDGNBVGY3TQOJQ", lambda k, s: p)


def test_audit_written_and_chained(env):
    g, _, _, c = env
    p = servers.terminate_server(c, "s")
    g.approve(p.id, "alice", "GEZDGNBVGY3TQOJQ", lambda k, s: c)
    ev = audit(g)
    assert [e["event"] for e in ev] == ["requested", "approved", "executed"]
    assert ev[0]["prev"] == "" and ev[1]["prev"] and ev[2]["actor"] == "alice"


def test_totp_rfc6238_vector_and_helpers(tmp_path):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 SHA1 seed "12345678901234567890" gitleaks:allow
    assert totp(secret, at=59) == "287082"
    assert totp(secret, at=1111111109) == "081804"
    s = guard.new_secret()
    assert len(s) == 32 and totp(s, at=0)
    assert guard.provisioning_uri(s, "bob").startswith("otpauth://totp/fcctl:bob?secret=" + s)


def test_state_dir_is_private(tmp_path):
    import stat
    g = Guard(tmp_path / "h", key="k" * 32, secret="x", user="u")
    assert stat.S_IMODE(g.home.stat().st_mode) == 0o700
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    import os
    os.environ["FCCTL_HOME"] = str(loose)
    try:
        Guard(loose, key="k" * 32, secret="x", user="u")
    finally:
        del os.environ["FCCTL_HOME"]
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


# destructive policy ops with no guarded CLI path (no run_op approval is ever filed for them)
_NO_APPROVAL_PATH = {"objects.delete", "bucket.rotate_credentials", "backup.offsite_download",
                     "backup.restore_upload"}


def test_every_approval_op_has_registry_entry():
    from fcctl import policy
    guard.load_all()
    missing = [o for o, v in policy.REGISTRY.items()
               if v["kind"] == "destructive" and o not in _NO_APPROVAL_PATH and o not in guard.REGISTRY]
    assert not missing, f"policy ops needing approval but not replayable: {missing}"
    for o, v in policy.REGISTRY.items():
        if o in guard.REGISTRY:
            assert guard.REGISTRY[o].kind == v["plane"], o
