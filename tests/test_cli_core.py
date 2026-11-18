import json

import pytest

from fcctl.cli import context, main
from fcctl.testing.fake import FakeFrappeCloud


@pytest.fixture
def env(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FCCTL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FCCTL_USER", "alice")
    monkeypatch.setenv("FCCTL_APPROVAL_SECRET", "s3cret")
    for k in ("FCCTL_PROFILE", "FCCTL_SERVER", "FCCTL_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


@pytest.fixture
def fake(env, monkeypatch):
    f = FakeFrappeCloud(env / "fake")
    monkeypatch.setattr(context, "HTTP", f.http())
    return f


def run(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def mkprofile(capsys, name="main", policy="full", team="TEAM-1"):
    rc, *_ = run(capsys, "profile", "create", name, "--email", "user@example.com", "--team", team,
                 "--policy", policy)
    assert rc == 0
    run(capsys, "profile", "use", name)


def test_help_version_no_io(env, capsys):
    rc, out, err = run(capsys, "--version")
    assert rc == 0 and "fcctl" in out
    rc, out, err = run(capsys, "--help")
    assert rc == 0 and "profile" in out and "auth" in out and "audit" in out
    assert not (env / "home").exists()


def test_no_command_and_bad_flag(env, capsys):
    assert run(capsys)[0] == 2
    assert run(capsys, "nosuch")[0] == 2


def test_global_flags_after_subcommand(env, capsys):
    mkprofile(capsys)
    rc, out, _ = run(capsys, "profile", "list", "-o", "ndjson")
    assert rc == 0 and json.loads(out.splitlines()[0])["name"] == "main"


def test_profile_lifecycle(env, capsys):
    mkprofile(capsys)
    rc, out, _ = run(capsys, "profile", "list")
    rows = json.loads(out)
    assert rows[0]["current"] is True
    rc, out, _ = run(capsys, "profile", "set", "main", "--policy", "read_only")
    assert json.loads(out)["policy"] == "read_only"
    rc, out, _ = run(capsys, "profile", "show")
    assert json.loads(out)["effective_policy"]["policy"] == "read_only"
    rc, _, err = run(capsys, "profile", "delete", "main")
    assert rc == 2 and "error:" in err
    assert run(capsys, "profile", "delete", "main", "--yes")[0] == 0
    assert json.loads(run(capsys, "profile", "list")[1]) == []


def test_error_goes_to_stderr_with_exit_code(env, capsys):
    rc, out, err = run(capsys, "profile", "show", "ghost")
    assert rc != 0 and out == "" and err.startswith("error:") and "fix:" in err


def test_auth_login_status_whoami_logout(env, fake, capsys, monkeypatch):
    import io
    mkprofile(capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO("123456\n"))
    rc, out, err = run(capsys, "auth", "login", "--code-stdin")
    assert rc == 0 and json.loads(out)["logged_in"] is True
    assert json.loads(run(capsys, "auth", "status")[1])["authenticated"] is True
    rc, out, _ = run(capsys, "auth", "whoami")
    assert rc == 0 and json.loads(out)["email"] == "user@example.com"
    assert run(capsys, "auth", "keepalive")[0] == 0
    run(capsys, "auth", "logout")
    assert json.loads(run(capsys, "auth", "status")[1])["authenticated"] is False


def test_auth_wrong_code(env, fake, capsys, monkeypatch):
    import io
    mkprofile(capsys)
    monkeypatch.setattr("sys.stdin", io.StringIO("000000\n"))
    rc, out, err = run(capsys, "auth", "login", "--code-stdin")
    assert rc == 3 and out == "" and "error:" in err


def test_audit_list_verify_export(env, capsys):
    from fcctl.audit import AuditLog
    log = AuditLog(env / "home" / "audit" / "audit.jsonl")
    log.append(op="site.drop", target="a.b", outcome="denied")
    log.append(op="site.list", target="-", outcome="ok")
    rows = json.loads(run(capsys, "audit", "list", "--op", "site.drop")[1])
    assert len(rows) == 1
    assert json.loads(run(capsys, "audit", "verify")[1])["ok"] is True
    rc, out, _ = run(capsys, "audit", "export")
    assert rc == 0 and len(out.strip().splitlines()) == 2
    p = env / "home" / "audit" / "audit.jsonl"
    p.write_text(p.read_text().replace("denied", "deniEd"))
    rc, out, _ = run(capsys, "audit", "verify")
    assert rc == 1 and json.loads(out)["ok"] is False


def test_approval_list_deny_enroll(env, capsys, monkeypatch):
    monkeypatch.setenv("FCCTL_APPROVAL_TOKEN", "s3cret")
    from fcctl.guard import Guard
    g = Guard(home=env / "home", key="k" * 8, secret="s3cret", user="alice")
    # the CLI builds its own Guard from FCCTL_KEY, so file under that key
    import os
    g = Guard(home=env / "home", key=os.environ["FCCTL_KEY"], secret="s3cret", user="bob")
    rec = g.request("site.drop", "a.b", {})
    rows = json.loads(run(capsys, "approval", "list")[1])
    assert rows[0]["id"] == rec.id
    assert json.loads(run(capsys, "approval", "show", rec.id)[1])["op"] == "site.drop"
    rc, out, err = run(capsys, "approval", "approve", rec.id, "--approver", "bob")
    assert rc == 4 and "approver name must differ" in err  # self approval rejected
    rc, out, _ = run(capsys, "approval", "deny", rec.id, "--approver", "alice")
    assert rc == 0 and json.loads(out)["status"] == "denied"
    rc, out, _ = run(capsys, "approval", "enroll")
    d = json.loads(out)
    assert len(d["FCCTL_APPROVAL_SECRET"]) == 32 and d["otpauth_uri"].startswith("otpauth://totp/")
    assert "current_code" not in d
    assert run(capsys, "approval", "approve", "x", "--token", "t")[0] == 2  # --token on argv no longer exists
    assert run(capsys, "approval", "show", "nope")[0] == 7


def test_misc_version_guide_completion_alert_doctor(env, capsys):
    assert "fcctl" in json.loads(run(capsys, "version")[1])
    assert "fcctl quick guide" in run(capsys, "guide")[1]
    out = run(capsys, "completion", "bash")[1]
    assert "complete -F _fcctl fcctl" in out and "profile" in out and "doctor" in out
    assert "#compdef" in run(capsys, "completion", "zsh")[1]
    assert run(capsys, "alert", "test")[0] == 0
    rc, out, _ = run(capsys, "doctor")
    checks = {c["check"]: c["ok"] for c in json.loads(out)}
    assert rc == 1 and checks["profile selected"] is False


def test_doctor_api(env, fake, capsys):
    mkprofile(capsys)
    fake_store = None  # session absent -> doctor reports it
    rc, out, _ = run(capsys, "doctor")
    assert {c["check"]: c["ok"] for c in json.loads(out)}["session present"] is False


def test_reconcile_and_watch(env, capsys):
    rc, out, _ = run(capsys, "reconcile", "--once")
    assert rc == 0 and "journal" in json.loads(out)
    rc, out, _ = run(capsys, "watch", "--count", "1", "--interval", "0")
    assert rc == 0 and "journal" in json.loads(out)


def test_api_central_read_and_policy(env, fake, capsys):
    mkprofile(capsys)
    fake.store()  # noqa
    s = None
    # seed an authenticated session for the CLI's store
    from cryptography.fernet import Fernet  # noqa
    from fcctl.session_store import SessionStore
    import os
    SessionStore(env / "home" / "session-main.enc", os.environ["FCCTL_KEY"]).set_central(
        sid=fake.sid, csrf=fake.csrf)
    rc, out, err = run(capsys, "api", "central", "GET", "central.api.identity.my_profile")
    assert rc == 0 and json.loads(out)["email"] == "user@example.com"
    run(capsys, "profile", "set", "main", "--policy", "read_only")
    rc, out, err = run(capsys, "api", "central", "POST", "central.api.servers.create_server", "--data",
                       '{"title": "x"}')
    assert rc == 4 and out == "" and "error:" in err
    rc, out, _ = run(capsys, "api", "central", "POST", "central.api.servers.create_server", "--dry-run")
    assert rc == 0
