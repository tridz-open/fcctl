"""Facade guard: destructive methods are guarded by default; unsafe=True and owned_by_workflow opt out."""
import json

import pytest

from fcctl.errors import PolicyDenied
from fcctl.guard import Guard, set_default_guard
from fcctl.journal import Journal
from test_client_facade import async_client, call, sync_client  # noqa: F401


@pytest.fixture(params=["sync", "async"])
def cl(request, tmp_path, monkeypatch):
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    st = []
    set_default_guard(Guard(home=tmp_path / "g", key="k", secret="s", user="alice"))
    yield (sync_client if request.param == "sync" else async_client)(st), st
    set_default_guard(None)


def _restores(st):
    return [b for m, p, b in st if p.endswith("actions/restore")]


def _drops(st):
    return [p for m, p, b in st if m == "DELETE"]


def _audit_rows(tmp_path):
    f = tmp_path / "home" / "audit" / "audit.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_drop_default_pending(cl):
    c, st = cl
    rec = call(c, c.sites.drop, "srv", "a.test")
    assert rec.op == "drop_site" and not _drops(st)


def test_restore_default_pending(cl):
    c, st = cl
    rec = call(c, c.backups.restore, "srv", "a.test", from_upload="u1")
    assert rec.op == "restore_backup" and not _restores(st)


def test_drop_unsafe_executes_and_audits(cl, tmp_path):
    c, st = cl
    call(c, c.sites.drop, "srv", "a.test", unsafe=True)
    assert _drops(st) == ["/api/v1/sites/a.test"]
    rows = _audit_rows(tmp_path)
    assert rows[-1]["op"] == "site.drop" and rows[-1]["extra"]["unsafe"] is True


def test_restore_unsafe_executes_and_audits(cl, tmp_path):
    c, st = cl
    call(c, c.backups.restore, "srv", "a.test", from_upload="u1", unsafe=True)
    assert _restores(st)[0]["upload_id"] == "u1"
    rows = _audit_rows(tmp_path)
    assert rows[-1]["op"] == "backup.restore" and rows[-1]["extra"]["unsafe"] is True


def test_owned_path_unchanged(cl, tmp_path, monkeypatch):
    c, st = cl
    for mod in ("_async", "_sync"):
        monkeypatch.setattr(f"fcctl.{mod}.facade._check_owned", lambda *a, **k: None)
    call(c, c.sites.drop, "srv", "a.test", owned_by_workflow="wf-x")
    call(c, c.backups.restore, "srv", "a.test", from_upload="u1", owned_by_workflow="wf-x")
    assert _drops(st) and _restores(st)
    assert not _audit_rows(tmp_path)  # owned path is not an unsafe call


def test_wrong_ownership_denied(cl, tmp_path):
    c, st = cl
    j = Journal(tmp_path / "j")
    with pytest.raises(PolicyDenied):
        call(c, c.sites.drop, "srv", "a.test", owned_by_workflow="wf-x", journal=j)
    with pytest.raises(PolicyDenied):
        call(c, c.backups.restore, "srv", "a.test", from_upload="u1", owned_by_workflow="wf-x", journal=j)
    assert not _drops(st) and not _restores(st)


def test_approval_then_executes(tmp_path, monkeypatch):
    import time

    from fcctl.guard import totp
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    st = []
    c = sync_client(st)
    g = Guard(home=tmp_path / "g", key="k" * 32, secret="GEZDGNBVGY3TQOJQ", user="alice")  # gitleaks:allow
    set_default_guard(g)
    try:
        d = c.sites.drop("srv", "a.test")
        r = c.backups.restore("srv", "a.test", from_upload="u1")
        assert not _drops(st) and not _restores(st)
        for rec in (d, r):
            g.approve(rec.id, "bob", totp("GEZDGNBVGY3TQOJQ", time.time()), lambda k, s: c.pilot("srv"))
        assert _drops(st) == ["/api/v1/sites/a.test"] and _restores(st)
    finally:
        set_default_guard(None)
