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
    monkeypatch.setenv("FCCTL_POLL", "0")
    for k in ("FCCTL_PROFILE", "FCCTL_SERVER", "FCCTL_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


_FAKE = [None]


@pytest.fixture
def fake(env, monkeypatch):
    f = FakeFrappeCloud(env / "fake")
    _FAKE[0] = f
    monkeypatch.setattr(context, "HTTP", f.http())
    return f


def run(capsys, *argv):
    rc = main(list(argv))
    o = capsys.readouterr()
    return rc, o.out, o.err


def prof(capsys, policy="full"):
    import os
    from fcctl.session_store import SessionStore
    assert run(capsys, "profile", "create", "main", "--email", "user@example.com", "--team", "TEAM-1",
               "--policy", policy)[0] == 0
    run(capsys, "profile", "use", "main")
    home = __import__("pathlib").Path(os.environ["FCCTL_HOME"])
    SessionStore(home / "session-main.enc", os.environ["FCCTL_KEY"]).set_central(sid=_FAKE[0].sid, csrf=_FAKE[0].csrf)


@pytest.fixture
def ready(fake, capsys):
    prof(capsys)
    fake.sites["a.x.com"] = {"name": "a.x.com", "status": "active", "apps": [], "maintenance_mode": 0,
                             "pause_scheduler": 0}
    fake.backups["a.x.com"] = []
    return fake


S = ("-s", "server-fake", "-o", "json")


def test_site_list_get_create(ready, capsys):
    rc, out, _ = run(capsys, "site", "list", *S)
    assert rc == 0 and json.loads(out)[0]["name"] == "a.x.com"
    assert run(capsys, "site", "get", "a.x.com", *S)[0] == 0
    rc, out, _ = run(capsys, "site", "create", "b.x.com", "--app", "erpnext", *S)
    assert rc == 0 and json.loads(out)["status"] == "success"
    assert "b.x.com" in ready.sites


def test_dry_run_and_no_wait(ready, capsys):
    rc, out, _ = run(capsys, "site", "create", "c.x.com", "--dry-run", *S)
    assert rc == 0 and json.loads(out)["dry_run"] and "c.x.com" not in ready.sites
    rc, out, _ = run(capsys, "site", "migrate", "a.x.com", "--no-wait", *S)
    assert rc == 0 and json.loads(out)["task_id"]


def test_site_actions_and_config(ready, capsys):
    assert run(capsys, "site", "maintenance", "a.x.com", "on", *S)[0] == 0
    assert ready.sites["a.x.com"]["maintenance_mode"] == 1
    assert run(capsys, "site", "clear-cache", "a.x.com", *S)[0] == 0
    rc, out, _ = run(capsys, "site", "login", "a.x.com", *S)
    assert "sid=" in json.loads(out)["url"]
    assert run(capsys, "site", "config", "set", "a.x.com", "maintenance_mode=0", *S)[0] == 0
    assert ready.sites["a.x.com"]["maintenance_mode"] == 0


def test_drop_needs_approval_exit7(ready, capsys):
    rc, _, err = run(capsys, "site", "drop", "a.x.com", *S)
    assert rc == 7 and "a.x.com" in ready.sites


def test_read_only_policy_exit4(fake, capsys):
    prof(capsys, "read_only")
    assert run(capsys, "site", "migrate", "a.x.com", *S)[0] == 4


def test_backup_create_list_restore_guarded(ready, capsys):
    rc, out, _ = run(capsys, "backup", "create", "a.x.com", *S)
    assert rc == 0
    rc, out, _ = run(capsys, "backup", "list", "a.x.com", *S)
    assert rc == 0 and len(json.loads(out)) == 1
    rc, out, _ = run(capsys, "backup", "download", "a.x.com", "20260101_000001", *S)
    assert rc == 0 and "links" in json.loads(out)
    assert run(capsys, "backup", "restore", "a.x.com", "20260101_000001", *S)[0] == 7


def test_app_commands(ready, capsys):
    assert run(capsys, "app", "add", "erpnext", *S)[0] == 0
    assert run(capsys, "app", "list", *S)[0] == 0
    assert run(capsys, "app", "install", "erpnext", "--site", "a.x.com", *S)[0] == 0
    assert run(capsys, "app", "uninstall", "erpnext", "--site", "a.x.com", *S)[0] == 7
    assert run(capsys, "app", "remove", "erpnext", *S)[0] == 7


def test_task_wait_timeout_fail_and_output(ready, capsys):
    rc, out, _ = run(capsys, "site", "migrate", "a.x.com", "--no-wait", *S)
    tid = json.loads(out)["task_id"]
    assert run(capsys, "task", "wait", tid, "--timeout", "0", *S)[0] == 8
    ready.fail_task(tid)
    assert run(capsys, "task", "wait", tid, *S)[0] == 9
    rc, out, _ = run(capsys, "task", "list", *S)
    assert rc == 0 and json.loads(out)[0]["task_id"] == tid
    assert run(capsys, "task", "get", tid, *S)[0] == 0
    assert run(capsys, "task", "cancel", tid, *S)[0] == 7


def test_failed_task_on_command_exit9(ready, capsys, monkeypatch):
    orig = ready.new_task

    def failing(kind, **kw):
        t = orig(kind, **kw)
        t["forced"] = "failed"
        return t
    monkeypatch.setattr(ready, "new_task", failing)
    assert run(capsys, "site", "migrate", "a.x.com", *S)[0] == 9


def test_attach_detach_130(ready, capsys, monkeypatch):
    import fcctl.liveness as lv
    rc, out, _ = run(capsys, "site", "migrate", "a.x.com", "--no-wait", *S)
    tid = json.loads(out)["task_id"]

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(lv, "wait", lv.wait)
    monkeypatch.setattr("fcctl.liveness.TaskPoller", lv.TaskPoller)
    orig = ready.tasks[tid]
    monkeypatch.setattr(lv, "_tasks", type("T", (), {"TERMINAL": lv.TERMINAL, "stream_events": None}))
    import fcctl._sync.pilot as rq
    monkeypatch.setattr(rq.PilotClient, "get", boom)
    assert run(capsys, "task", "attach", tid, *S)[0] == 130
    assert orig


def test_logs(ready, capsys):
    ready.logs["web.log"] = "l1\nl2\n"
    rc, out, _ = run(capsys, "logs", "list", *S)
    assert rc == 0 and json.loads(out)[0]["name"] == "web.log"
    rc, out, _ = run(capsys, "logs", "tail", "web.log", *S)
    assert rc == 0 and "l2" in out
    rc, out, _ = run(capsys, "logs", "tail", "web.log", "--follow", *S)
    assert rc == 0 and "l1" in out


def test_provision_and_resume(ready, capsys):
    rc, out, err = run(capsys, "site", "provision", "p.x.com", "--workflow-id", "wf-1", "--dry-run", *S)
    assert rc == 0 and json.loads(out)["dry_run"]
    rc, out, err = run(capsys, "site", "provision", "p.x.com", "--hook", "bad", *S)
    assert rc == 2


def test_fleet_run_status_resume(ready, capsys):
    ready.sites["b.x.com"] = dict(ready.sites["a.x.com"], name="b.x.com")
    ready.backups["b.x.com"] = []
    rc, out, _ = run(capsys, "fleet", "run", "site.clear_cache", "--select", "server-fake", "--parallel", "2", "-o", "json")
    assert rc == 0, out
    summ = json.loads(out)
    assert summ["done"] == 2 and summ["state"] == "succeeded"
    rc, out, _ = run(capsys, "fleet", "status", summ["run"], "-o", "json")
    assert json.loads(out)["total"] == 2
    assert run(capsys, "fleet", "resume", summ["run"], "-o", "json")[0] == 0
    assert run(capsys, "fleet", "run", "site.drop", "--select", "server-fake")[0] == 2
    assert run(capsys, "fleet", "status", "fleet-nope")[0] == 5


def test_fleet_failure_resume(ready, capsys, monkeypatch):
    orig = ready.new_task

    def failing(kind, **kw):
        t = orig(kind, **kw)
        t["forced"] = "failed"
        return t
    monkeypatch.setattr(ready, "new_task", failing)
    rc, out, _ = run(capsys, "fleet", "run", "backup.create", "--select", "server-fake:a.x.com", "-o", "json")
    assert rc == 9
    run_id = json.loads(out)["run"]
    monkeypatch.setattr(ready, "new_task", orig)
    rc, out, _ = run(capsys, "fleet", "resume", run_id, "-o", "json")
    assert rc == 0 and json.loads(out)["done"] == 1


def test_fleet_policy_denied_exit4(fake, capsys):
    prof(capsys, "read_only")
    fake.sites["a.x.com"] = {"name": "a.x.com", "status": "active", "apps": []}
    assert run(capsys, "fleet", "run", "site.migrate", "--select", "server-fake:a.x.com")[0] == 4


def _capture_install(monkeypatch):
    from fcctl import sites
    calls = []

    def fake_install(p, site, app=None, repo=None, branch=None):
        calls.append((site, app, repo, branch))
        return {"task_id": ""}
    monkeypatch.setattr(sites, "install_app", fake_install)
    return calls


def test_app_add_with_site_routes_to_site_install(ready, capsys, monkeypatch):
    """app add NAME --site S uses the site install body {app}, as the dashboard does, not bench get-app."""
    calls = _capture_install(monkeypatch)
    assert run(capsys, "app", "add", "india_compliance", "--site", "a.x.com", "--site", "b.x.com", *S)[0] == 0
    assert calls == [("a.x.com", "india_compliance", None, None),
                     ("b.x.com", "india_compliance", None, None)]


def test_app_check_updates_cli(ready, capsys):
    rc, out, _ = run(capsys, "app", "check-updates", *S)
    assert rc == 0 and "erpnext" in json.loads(out)
