import io
import json
import os

import pytest

from fcctl.cli import context, main
from fcctl.testing.fake import FakeFrappeCloud

SECRET = "TOPSECRETVALUE"
OP = "central.api.servers.create_server"


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    from cryptography.fernet import Fernet
    from fcctl.session_store import SessionStore
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FCCTL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FCCTL_USER", "alice")
    monkeypatch.setenv("FCCTL_APPROVAL_SECRET", "s3cret")
    for k in ("FCCTL_PROFILE", "FCCTL_SERVER", "FCCTL_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    fake = FakeFrappeCloud(tmp_path / "fake")
    monkeypatch.setattr(context, "HTTP", fake.http())

    def run(*argv):
        rc = main(list(argv))
        o = capsys.readouterr()
        return rc, o.out, o.err

    run("profile", "create", "main", "--email", "user@example.com", "--team", "TEAM-1", "--policy", "full")
    run("profile", "use", "main")
    SessionStore(tmp_path / "home" / "session-main.enc", os.environ["FCCTL_KEY"]).set_central(
        sid=fake.sid, csrf=fake.csrf)
    run("profile", "set", "main", "--policy", "read_only")
    return run


def test_data_from_file(cli, tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"title": SECRET}))
    rc, out, err = cli("api", "central", "POST", OP, "--data", f"@{f}")
    assert rc == 4 and "warning" not in err and SECRET not in err  # parsed, then policy-denied


def test_data_from_stdin(cli, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"title": SECRET})))
    rc, out, err = cli("api", "central", "POST", OP, "--data", "-")
    assert rc == 4 and "warning" not in err and SECRET not in err


def test_inline_warns_on_stderr_without_body(cli):
    rc, out, err = cli("api", "central", "POST", OP, "--data", json.dumps({"title": SECRET}))
    assert "visible in `ps`" in err and "--data @file" in err
    assert SECRET not in err and SECRET not in out and "warning" not in out


def test_missing_file(cli, tmp_path):
    rc, out, err = cli("api", "central", "POST", OP, "--data", f"@{tmp_path}/nope.json")
    assert rc == 2 and out == "" and "nope.json" in err and "fix" in err.lower()


def test_invalid_json_does_not_echo_body(cli, tmp_path):
    f = tmp_path / "bad.json"
    f.write_text('{"title": ' + SECRET)
    rc, out, err = cli("api", "central", "POST", OP, "--data", f"@{f}")
    assert rc == 2 and "line 1" in err and SECRET not in err
    rc, out, err = cli("api", "central", "POST", OP, "--data", '{"t": ' + SECRET)
    assert rc == 2 and SECRET not in err


def test_dry_run_with_file(cli, tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"title": SECRET}))
    rc, out, err = cli("api", "central", "POST", OP, "--data", f"@{f}", "--dry-run")
    assert rc == 0 and "warning" not in err
