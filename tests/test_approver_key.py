import json

import pytest

from fcctl import guard, servers
from fcctl.guard import BadApprovalSignature, Guard

PRIV, PUB = guard.generate_keypair()
PRIV2, PUB2 = guard.generate_keypair()  # an attacker's / wrong key
PINS = {"prod": {"alice": PUB}}


class FakeC:
    server = None

    def call(self, method, path, **kw):
        return {"ok": True}


def mk(tmp_path, **kw):
    kw.setdefault("approver_private_key", "")
    kw.setdefault("profile", "prod")
    kw.setdefault("team", "T1")
    g = Guard(tmp_path, key="k" * 32, secret="tok", now=lambda: 1000.0, user="bot", **kw)
    guard.set_default_guard(g)
    return g


@pytest.fixture(autouse=True)
def _reset():
    yield
    guard.set_default_guard(None)


def request(g):
    return servers.terminate_server(FakeC(), "srv1")


def run(g, rec, who="alice", **kw):
    return g.approve(rec.id, who, "tok", lambda k, s: FakeC(), **kw)


def test_valid_signature_executes(tmp_path):
    g = mk(tmp_path, approver_private_key=PRIV, approver_pubkeys=PINS)
    rec = request(g)
    assert rec.profile == "prod" and rec.team == "T1"
    run(g, rec)
    r = g._load()[rec.id]
    assert r.status == "executed" and r.approver_sig and r.approved_at == 1000.0


def test_signature_made_elsewhere_and_passed_in(tmp_path):
    g = mk(tmp_path, approver_pubkeys=PINS)  # executor holds no private key
    rec = request(g)
    sig = guard.sign_approval(PRIV, g._load()[rec.id], "alice")
    run(g, rec, signature=sig)
    assert g._load()[rec.id].status == "executed"


def test_forged_signature_wrong_key_rejected(tmp_path):
    g = mk(tmp_path, approver_private_key=PRIV2, approver_pubkeys=PINS)
    rec = request(g)
    with pytest.raises(BadApprovalSignature):
        run(g, rec)
    assert g._load()[rec.id].status == "pending"


def test_missing_signature_when_key_pinned(tmp_path):
    g = mk(tmp_path, approver_pubkeys=PINS)
    rec = request(g)
    with pytest.raises(BadApprovalSignature) as ei:
        run(g, rec)
    assert ei.value.why and ei.value.fix and g._load()[rec.id].status == "pending"


def test_unpinned_approver_name_rejected(tmp_path):
    g = mk(tmp_path, approver_private_key=PRIV, approver_pubkeys=PINS)
    rec = request(g)
    with pytest.raises(BadApprovalSignature):
        run(g, rec, who="mallory")


def test_signature_bound_to_approver_name(tmp_path):
    g = mk(tmp_path, approver_pubkeys={"prod": {"alice": PUB, "bob": PUB}})
    rec = request(g)
    sig = guard.sign_approval(PRIV, g._load()[rec.id], "alice")
    with pytest.raises(BadApprovalSignature):
        run(g, rec, who="bob", signature=sig)


@pytest.mark.parametrize("field,value", [("op", "server.stop"), ("target", "srv-other"),
                                         ("expires_at", 9e9), ("profile", "staging"), ("team", "T2"),
                                         ("args", {"x": 1})])
def test_tampered_body_rejected(tmp_path, field, value):
    g = mk(tmp_path, approver_pubkeys={"prod": {"alice": PUB}, "staging": {"alice": PUB}})
    rec = request(g)
    sig = guard.sign_approval(PRIV, g._load()[rec.id], "alice")
    recs = g._load()
    setattr(recs[rec.id], field, value)
    g._save(recs)
    with pytest.raises(BadApprovalSignature):
        run(g, rec, signature=sig)


def test_unsigned_profile_unaffected(tmp_path):
    g = mk(tmp_path, profile="other", approver_pubkeys=PINS)  # pins exist only for "prod"
    rec = request(g)
    run(g, rec)
    r = g._load()[rec.id]
    assert r.status == "executed" and r.approver_sig is None


def test_unset_behaves_as_before(tmp_path):
    g = mk(tmp_path)
    rec = request(g)
    run(g, rec)
    r = g._load()[rec.id]
    assert r.status == "executed" and r.approver_sig is None


def test_unverifiable_signature_not_recorded_when_unpinned(tmp_path):
    g = mk(tmp_path, approver_private_key=PRIV)
    rec = request(g)
    run(g, rec)
    assert g._load()[rec.id].approver_sig is None


def test_require_signed_rejects_unsigned(tmp_path):
    g = mk(tmp_path, require_signed=True)
    rec = request(g)
    with pytest.raises(BadApprovalSignature) as ei:
        run(g, rec)
    assert ei.value.why and ei.value.fix


def test_private_key_never_in_record_or_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("FCCTL_APPROVER_PRIVATE_KEY", PRIV)
    g = mk(tmp_path, approver_private_key=PRIV, approver_pubkeys=PINS)
    rec = request(g)
    run(g, rec)
    assert PRIV not in g.store_path.read_text() and PRIV not in g.audit_path.read_text()
    from fcctl.redact import redact_text
    assert PRIV not in redact_text(f"FCCTL_APPROVER_PRIVATE_KEY={PRIV}")
    json.loads(g.store_path.read_text())


def test_private_key_from_file(tmp_path):
    f = tmp_path / "k"
    f.write_text(PRIV + "\n")
    assert guard.load_private_key({"FCCTL_APPROVER_PRIVATE_KEY_FILE": str(f)}) == PRIV
    assert guard.load_private_key({"FCCTL_APPROVER_PRIVATE_KEY": PRIV}) == PRIV
    assert guard.load_private_key({}) == ""


def test_profile_pins_roundtrip_config(tmp_path):
    from fcctl.config import ProfileStore
    from fcctl.profiles import Profile
    st = ProfileStore(tmp_path / "config.toml")
    st.create(Profile("prod", "a@x.io", "T1", approver_pubkeys={"alice": PUB}))
    assert st.get("prod").approver_pubkeys == {"alice": PUB}


def test_cli_note_once_on_stderr(tmp_path, capsys):
    from types import SimpleNamespace
    from fcctl.cli import g_approval
    g = mk(tmp_path, profile="other")
    rec = request(g)
    ctx = SimpleNamespace(guard=g, env={"FCCTL_APPROVAL_TOKEN": "tok"}, client_factory=lambda: (lambda k, s: FakeC()))
    g_approval.approve(ctx, SimpleNamespace(id=rec.id, approver="alice"))
    out = capsys.readouterr()
    assert out.err.count("speed bump") == 1 and "speed bump" not in out.out


def test_cli_keygen_and_sign(tmp_path):
    from types import SimpleNamespace
    from fcctl.cli import g_approval
    out = g_approval.keygen(None, None)
    assert out["FCCTL_APPROVER_PRIVATE_KEY"] and out["public_key"]
    g = mk(tmp_path, approver_pubkeys=PINS)
    rec = request(g)
    ctx = SimpleNamespace(guard=g, env={"FCCTL_APPROVER_PRIVATE_KEY": PRIV})
    sig = g_approval.sign(ctx, SimpleNamespace(id=rec.id, approver="alice"))["signature"]
    run(g, rec, signature=sig)
    assert g._load()[rec.id].status == "executed"
