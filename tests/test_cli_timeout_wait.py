"""--no-wait / --timeout are consumed by every long-running command group."""
import json
import os
import pathlib

import pytest
from cryptography.fernet import Fernet

from fcctl.cli import context, main
from fcctl.session_store import SessionStore
from fcctl.testing.fake import FakeFrappeCloud

S = ("-s", "server-fake", "-o", "json")


def run(capsys, *argv):
    rc = main(list(argv))
    o = capsys.readouterr()
    return rc, o.out, o.err


@pytest.fixture
def fk(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FCCTL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FCCTL_USER", "alice")
    monkeypatch.setenv("FCCTL_APPROVAL_SECRET", "s3cret")
    monkeypatch.setenv("FCCTL_POLL", "0")
    for k in ("FCCTL_PROFILE", "FCCTL_SERVER", "FCCTL_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    f = FakeFrappeCloud(tmp_path / "fake", task_polls=10_000)  # tasks never finish
    monkeypatch.setattr(context, "HTTP", f.http())
    assert run(capsys, "profile", "create", "main", "--email", "u@example.com", "--team", "TEAM-1",
               "--policy", "full")[0] == 0
    run(capsys, "profile", "use", "main")
    SessionStore(pathlib.Path(os.environ["FCCTL_HOME"]) / "session-main.enc", os.environ["FCCTL_KEY"]).set_central(
        sid=f.sid, csrf=f.csrf)
    f.sites["a.x.com"] = {"name": "a.x.com", "status": "active", "apps": [], "maintenance_mode": 0,
                          "pause_scheduler": 0}
    f.backups["a.x.com"] = []
    f.servers["srv-1"] = {"resource_id": "srv-1", "name": "srv-1", "status": "Pending", "plan": "p1"}
    return f


PILOT_CMDS = [
    ("site", "migrate", "a.x.com"),
    ("backup", "create", "a.x.com"),
    ("app", "install", "erpnext", "--site", "a.x.com"),
]


@pytest.mark.parametrize("cmd", PILOT_CMDS)
def test_pilot_no_wait_returns_handle(fk, capsys, cmd):
    rc, out, _ = run(capsys, *cmd, "--no-wait", *S)
    assert rc == 0 and json.loads(out)["task_id"]


@pytest.mark.parametrize("cmd", PILOT_CMDS)
def test_pilot_timeout_exit_8(fk, capsys, cmd):
    rc, _, err = run(capsys, *cmd, "--timeout", "0", *S)
    assert rc == 8
    assert "task attach" in err and "task-" in err


def test_task_wait_timeout(fk, capsys):
    tid = json.loads(run(capsys, "site", "migrate", "a.x.com", "--no-wait", *S)[1])["task_id"]
    rc, _, err = run(capsys, "task", "wait", tid, "--timeout", "0", *S)
    assert rc == 8 and tid in err


def test_server_no_wait_and_timeout(fk, capsys):
    calls = []
    orig = context.HTTP
    assert run(capsys, "server", "start", "srv-1", "--no-wait", "-o", "json")[0] == 0
    assert run(capsys, "server", "wait", "srv-1", "--status", "Archived", "--timeout", "0", "-o", "json")[0] == 8


def test_snapshot_take_no_wait_and_timeout(fk, capsys):
    fk.servers["srv-1"]["status"] = "Running"
    rc, out, _ = run(capsys, "snapshot", "take", "srv-1", "--title", "t", "--no-wait", "-o", "json")
    assert rc == 0 and json.loads(out)["name"]
    assert run(capsys, "snapshot", "wait", "srv-1", "ghost", "--timeout", "0", "-o", "json")[0] == 8


def test_site_create_timeout_and_no_wait(fk, capsys):
    rc, _, err = run(capsys, "site", "create", "n0.x.com", "--timeout", "0", *S)
    assert rc == 8 and "task attach" in err
    rc, out, _ = run(capsys, "site", "create", "n1.x.com", "--no-wait", *S)
    assert rc == 0 and json.loads(out)["task_id"]


def test_provision_workflow_timeout_exit_8(fk, capsys):
    fk.sites.clear()
    rc, _, err = run(capsys, "site", "provision", "p0.x.com", "--timeout", "0", *S)
    assert rc == 8, err
    assert "task attach" in err or "remotely" in err


def test_server_create_default_waits_and_times_out_exit_8(fk, capsys):
    rc, _, err = run(capsys, "server", "create", "--title", "t", "--plan", "p1", "--image", "img",
                     "--timeout", "0", "-o", "json")
    assert rc == 8 and "remotely" in err


def test_server_wait_zero_skips_wait(fk, capsys):
    rc, out, _ = run(capsys, "server", "start", "srv-1", "--wait", "0", "-o", "json")
    assert rc == 0


def test_snapshot_take_default_waits_exit_8(fk, capsys):
    class Pending(dict):
        def __setitem__(self, k, v):
            super().__setitem__(k, {**v, "status": "Pending"})
    fk.snapshots = Pending()
    fk.servers["srv-1"]["status"] = "Running"
    rc, _, err = run(capsys, "snapshot", "take", "srv-1", "--title", "t", "--timeout", "0", "-o", "json")
    assert rc == 8
