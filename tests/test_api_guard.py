"""Unclassified `fcctl api` writes need approval under guarded/protected; approved request replays."""
import json
import os

import pytest

from fcctl import policy as pol
from tests.test_cli_core import env, fake, mkprofile, run  # noqa: F401

OPS = ("api", "central", "POST", "central.api.identity.my_profile")


def _seed(env, fake):
    from fcctl.session_store import SessionStore
    SessionStore(env / "home" / "session-main.enc", os.environ["FCCTL_KEY"]).set_central(
        sid=fake.sid, csrf=fake.csrf)


def _posts(fake):
    return [c for c in fake.calls if c.method == "POST" and "my_profile" in c.path]


@pytest.mark.parametrize("policy", ["guarded", "protected"])
def test_unknown_post_pending(env, fake, capsys, policy):
    mkprofile(capsys, policy=policy)
    _seed(env, fake)
    rc, out, err = run(capsys, *OPS, "--data", "{}")
    assert rc == 7 and "pending" in err
    assert not _posts(fake)
    rows = json.loads(run(capsys, "approval", "list")[1])
    assert rows[0]["op"] == "api.raw" and rows[0]["args"]["method"] == "POST"


def test_yes_and_confirm_do_not_bypass(env, fake, capsys):
    mkprofile(capsys, policy="guarded")
    _seed(env, fake)
    assert run(capsys, *OPS, "--data", "{}", "--yes")[0] == 7
    assert run(capsys, *OPS, "--data", "{}", "--confirm")[0] == 7
    assert not _posts(fake)


def test_get_passes_and_full_passes(env, fake, capsys):
    mkprofile(capsys, policy="guarded")
    _seed(env, fake)
    assert run(capsys, "api", "central", "GET", "central.api.identity.my_profile")[0] == 0
    run(capsys, "profile", "set", "main", "--policy", "full")
    assert run(capsys, *OPS, "--data", "{}")[0] == 0
    assert _posts(fake)


def test_read_only_denies(env, fake, capsys):
    mkprofile(capsys, policy="read_only")
    _seed(env, fake)
    rc, out, err = run(capsys, *OPS, "--data", "{}")
    assert rc == 4 and not _posts(fake)


def test_approve_then_execute(env, fake, capsys, monkeypatch):
    mkprofile(capsys, policy="guarded")
    _seed(env, fake)
    assert run(capsys, *OPS, "--data", "{\"a\": 1}")[0] == 7
    rid = json.loads(run(capsys, "approval", "list")[1])[0]["id"]
    monkeypatch.setenv("FCCTL_APPROVAL_TOKEN", "s3cret")
    rc, out, err = run(capsys, "approval", "approve", rid, "--approver", "bob")
    assert rc == 0, err
    posts = _posts(fake)
    assert len(posts) == 1 and posts[0].body["a"] == 1
    assert run(capsys, "approval", "execute", rid, "--approver", "bob")[0] == 0
    assert len(_posts(fake)) == 1  # idempotent


def test_policy_unit():
    e = lambda p: pol.EffectivePolicy("a@x", "T", p)  # noqa: E731
    assert isinstance(pol.check(("POST", "/api/x"), e("guarded")), pol.NeedsApproval)
    assert isinstance(pol.check(("PUT", "/api/x"), e("protected")), pol.NeedsApproval)
    assert isinstance(pol.check(("POST", "/api/x"), e("full")), pol.Allow)
    assert isinstance(pol.check(("POST", "/api/x"), e("read_only")), pol.Deny)
