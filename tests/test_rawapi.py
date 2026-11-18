import pytest

from fcctl import policy as pol
from fcctl.audit import AuditLog
from fcctl.guard import Guard
from fcctl.journal import Journal
from fcctl.ops import ApprovalPending, OpContext, PolicyDenied
from fcctl.rawapi import ScopeMismatch, call_raw, classify


class Fake:
    def __init__(self): self.calls = []
    def request(self, method, path, json=None):
        self.calls.append((method, path, json)); return {"ok": True}


def mk(tmp_path, policy="guarded", **kw):
    f = Fake()
    eff = pol.EffectivePolicy("a@x", "T", policy)
    c = OpContext(eff=eff, journal=Journal(tmp_path / "j"), audit=AuditLog(tmp_path / "a.jsonl"),
                  guard=Guard(home=tmp_path / "g", key="k", secret="s", user="u"),
                  clients={"pilot": f, "central": f}, **kw)
    return c, f


def test_classify_by_id_and_unknown():
    assert classify("POST", "server.terminate")["kind"] == "destructive"
    assert classify("POST", "/some/unknown") is None


def test_get_free_and_unaudited(tmp_path):
    c, f = mk(tmp_path, "read_only")
    assert call_raw(c, "pilot", "get", "/sites") == {"ok": True}
    assert c.audit.list() == [] and f.calls == [("GET", "/sites", None)]


def test_unknown_post_needs_approval_not_confirm(tmp_path):
    c, f = mk(tmp_path)
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "POST", "/sites", {"a": 1}, confirm=True)
    assert not f.calls and c.audit.list()[-1]["outcome"] == "approval_requested"


def test_full_policy_post_free(tmp_path):
    c, f = mk(tmp_path, "full")
    call_raw(c, "pilot", "POST", "/sites", {})
    assert f.calls


def test_delete_and_destructive_path_guarded(tmp_path):
    c, f = mk(tmp_path)
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "DELETE", "/sites/x", confirm=True)
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "POST", "/ops/terminate", confirm=True)
    assert not f.calls


def test_guarded_flag(tmp_path):
    c, f = mk(tmp_path, "full")
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "POST", "/sites", {}, guarded=True)


def test_unclassified_write_protected_needs_approval(tmp_path):
    c, f = mk(tmp_path, "protected")
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "POST", "/sites", {}, confirm=True)
    assert not f.calls


def test_unclassified_write_rejected(tmp_path):
    c, f = mk(tmp_path, "read_only")
    with pytest.raises(PolicyDenied):
        call_raw(c, "pilot", "POST", "/sites", {}, confirm=True)
    assert not f.calls and c.audit.list()[-1]["outcome"] == "denied"


def test_membership(tmp_path):
    def member(sid):
        if sid != "mine": raise ScopeMismatch(f"{sid} not in team")
    c, f = mk(tmp_path, team_membership=member)
    call_raw(c, "pilot", "GET", "/servers/mine/sites")
    with pytest.raises(ScopeMismatch):
        call_raw(c, "pilot", "GET", "/servers/other/sites")


def test_dry_run(tmp_path):
    c, f = mk(tmp_path, dry_run=True)
    plan = call_raw(c, "pilot", "POST", "/sites", {}, confirm=True)
    assert plan["dry_run"] and not f.calls


# F1: GETs used to bypass policy entirely
@pytest.mark.parametrize("plane,path", [("central", "press.api.site.archive"),
                                        ("central", "/api/method/press.api.site.archive"),
                                        ("pilot", "/sites/x/drop"), ("pilot", "/sites/x/restore")])
def test_risky_get_denied_read_only_and_protected(tmp_path, plane, path):
    for policy in ("read_only", "protected"):
        c, f = mk(tmp_path / policy, policy)
        with pytest.raises((PolicyDenied, ApprovalPending)):
            call_raw(c, plane, "GET", path)
        assert not f.calls


@pytest.mark.parametrize("plane,path", [("central", "press.api.site.archive"), ("pilot", "/sites/x/archive")])
def test_risky_get_needs_approval_guarded(tmp_path, plane, path):
    c, f = mk(tmp_path)
    with pytest.raises(ApprovalPending):
        call_raw(c, plane, "GET", path, confirm=True)
    assert not f.calls


def test_read_only_denies_risky_get_outright(tmp_path):
    c, f = mk(tmp_path, "read_only")
    with pytest.raises(PolicyDenied):
        call_raw(c, "central", "GET", "press.api.site.archive")
    assert not f.calls and c.audit.list()[-1]["outcome"] == "denied"


@pytest.mark.parametrize("policy", ["read_only", "guarded", "protected", "full"])
def test_plain_safe_get_still_allowed(tmp_path, policy):
    c, f = mk(tmp_path, policy)
    assert call_raw(c, "pilot", "GET", "/api/method/ping") == {"ok": True}
    assert call_raw(c, "central", "GET", "server.list") == {"ok": True}
    assert len(f.calls) == 2


def test_guarded_flag_applies_to_get(tmp_path):
    c, f = mk(tmp_path, "full")
    with pytest.raises(ApprovalPending):
        call_raw(c, "pilot", "GET", "/sites", guarded=True)
    assert not f.calls


# N1: Central raw reads were all blocked because REGISTRY is keyed by op id, not dotted method
@pytest.mark.parametrize("policy", ["read_only", "guarded", "protected", "full"])
@pytest.mark.parametrize("path", ["central.api.servers.registry",
                                  "/api/method/central.api.servers.registry",
                                  "/api/resource/Site", "/api/resource/Site/abc"])
def test_central_registered_read_get_allowed(tmp_path, policy, path):
    c, f = mk(tmp_path, policy)
    assert call_raw(c, "central", "GET", path) == {"ok": True}
    assert f.calls


def test_central_read_methods_index_consistent():
    assert pol.CENTRAL_READ_METHODS
    for m, op in pol.CENTRAL_READ_METHODS.items():
        assert pol.REGISTRY[op]["kind"] == "read" and pol.REGISTRY[op]["plane"] == "central", (m, op)
        assert pol.central_read_op(m) == op


def test_unregistered_archive_still_denied_read_only(tmp_path):
    c, f = mk(tmp_path, "read_only")
    with pytest.raises(PolicyDenied):
        call_raw(c, "central", "GET", "press.api.site.archive")
    assert not f.calls
