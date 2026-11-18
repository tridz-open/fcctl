import io
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
    o = capsys.readouterr()
    return rc, o.out, o.err


def login(capsys, monkeypatch, policy="full", name="main"):
    assert run(capsys, "profile", "create", name, "--email", "user@example.com", "--team", "TEAM-1",
               "--policy", policy)[0] == 0
    run(capsys, "profile", "use", name)
    monkeypatch.setattr("sys.stdin", io.StringIO("123456\n"))
    assert run(capsys, "auth", "login", "--code-stdin")[0] == 0


@pytest.fixture
def cli(env, fake, capsys, monkeypatch):
    login(capsys, monkeypatch)
    monkeypatch.setenv("FCCTL_APPROVAL_TOKEN", "s3cret")
    fake.servers["srv-1"] = {"resource_id": "srv-1", "name": "srv-1", "status": "Running", "plan": "p1"}
    return fake


def j(out):
    return json.loads(out)


def test_server_list_get_and_not_found(cli, capsys):
    rc, out, _ = run(capsys, "server", "list")
    assert rc == 0 and j(out)[0]["resource_id"] == "srv-1"
    assert j(run(capsys, "server", "get", "srv-1")[1])["plan"] == "p1"
    rc, out, err = run(capsys, "server", "get", "nope")
    assert rc == 5 and out == "" and "error:" in err


def test_server_create_idempotent_key(cli, capsys):
    a = j(run(capsys, "server", "create", "--title", "t", "--plan", "p", "--image", "img", "--request-key", "k1", "--no-wait")[1])
    b = j(run(capsys, "server", "create", "--title", "t", "--plan", "p", "--image", "img", "--request-key", "k1", "--no-wait")[1])
    assert a["resource_id"] == b["resource_id"]


def test_power_and_wait(cli, capsys):
    rc, out, _ = run(capsys, "server", "restart", "srv-1", "--no-wait")
    assert rc == 0 and j(out)["status"] == "Running"
    rc, out, _ = run(capsys, "server", "start", "srv-1", "--wait", "--poll", "0")
    assert rc == 0 and j(out)["status"] == "Running"
    cli.servers["srv-1"]["status"] = "Pending"
    rc, out, err = run(capsys, "server", "wait", "srv-1", "--timeout", "0", "--poll", "0")
    assert rc == 8 and "error:" in err
    cli.servers["srv-1"]["status"] = "Running"
    assert run(capsys, "server", "wait", "srv-1", "--poll", "0")[0] == 0


def test_destructive_server_ops_hit_guard(cli, capsys):
    rc, out, err = run(capsys, "server", "resize", "srv-1", "--plan", "p2")
    assert rc != 7 and "approval" not in err  # cost write: confirm (guarded) / allowed (full), never approval
    for argv in (["server", "stop", "srv-1"],
                 ["server", "protect", "srv-1", "off"], ["server", "terminate", "srv-1"]):
        rc, out, err = run(capsys, *argv)
        assert rc == 7 and "approval" in err, argv
    assert "srv-1" in cli.servers and cli.servers["srv-1"]["status"] == "Running"
    pend = j(run(capsys, "approval", "list")[1])
    assert {p["op"] for p in pend} == {"server.stop", "server.set_termination_protection",
                                       "server.terminate"}


def test_approved_terminate_executes(cli, capsys):
    assert run(capsys, "server", "terminate", "srv-1")[0] == 7
    pid = j(run(capsys, "approval", "list")[1])[0]["id"]
    rc, out, err = run(capsys, "approval", "approve", pid, "--approver", "bob")
    assert rc == 0, err
    assert "srv-1" not in cli.servers


def test_protect_on_allowed_and_dry_run(cli, capsys, monkeypatch):
    from fcctl._sync import servers
    monkeypatch.setattr(servers, "set_termination_protection", lambda c, rid, en: {"enabled": en})
    rc, out, _ = run(capsys, "server", "protect", "srv-1", "on")
    assert rc == 0 and j(out) == {"enabled": True}
    rc, out, _ = run(capsys, "server", "terminate", "srv-1", "--dry-run")
    assert rc == 0 and j(out)["dry_run"] is True and "srv-1" in cli.servers


def test_read_only_profile_blocks_writes(env, fake, capsys, monkeypatch):
    login(capsys, monkeypatch, policy="read_only")
    fake.servers["srv-1"] = {"resource_id": "srv-1", "name": "srv-1", "status": "Running"}
    assert run(capsys, "server", "list")[0] == 0
    for argv in (["server", "start", "srv-1"], ["server", "terminate", "srv-1"],
                 ["snapshot", "take", "srv-1"], ["snapshot", "delete", "s1"], ["bucket", "create", "b"],
                 ["bucket", "delete", "b"], ["notification", "read", "N-1"]):
        rc, out, err = run(capsys, *argv)
        assert rc == 4 and "error:" in err, argv


def test_plans_images_metrics(cli, capsys, monkeypatch):
    from fcctl import infra
    from fcctl._sync import monitor
    monkeypatch.setattr(infra, "plans_eligible", lambda c, **k: [{"plan": "p1", **k}])
    monkeypatch.setattr(infra, "images", lambda c, **k: [{"id": "img", **k}])
    monkeypatch.setattr(monitor, "central_metrics", lambda c, rid, period: {"rid": rid, "period": period})
    monkeypatch.setattr(monitor, "metrics", lambda p: {"cpu": 1})
    assert j(run(capsys, "server", "plans", "--region", "bom")[1])[0]["region"] == "bom"
    assert j(run(capsys, "server", "images")[1])[0]["offering"] == "pilot"
    assert j(run(capsys, "server", "metrics", "srv-1", "--period", "6h")[1]) == {"rid": "srv-1", "period": "6h"}
    cli.tokens  # pilot reachable via fake; live path uses monitor.metrics
    rc, out, _ = run(capsys, "server", "metrics", "srv-1", "--live")
    assert rc == 0 and j(out) == {"cpu": 1}


def test_snapshot_flow(cli, capsys):
    snap = j(run(capsys, "snapshot", "take", "srv-1", "--title", "t", "--wait", "--poll", "0")[1])
    assert snap["status"] == "Completed"
    rows = j(run(capsys, "snapshot", "list", "srv-1")[1])
    assert [r["name"] for r in rows] == [snap["name"]]
    assert run(capsys, "snapshot", "wait", "srv-1", snap["name"], "--poll", "0")[0] == 0
    rc, _, err = run(capsys, "snapshot", "wait", "srv-1", "ghost", "--timeout", "0", "--poll", "0")
    assert rc == 8
    rc, _, err = run(capsys, "snapshot", "delete", snap["name"])
    assert rc == 7 and snap["name"] in cli.snapshots
    pid = j(run(capsys, "approval", "list")[1])[0]["id"]
    assert run(capsys, "approval", "approve", pid, "--approver", "bob")[0] == 0
    assert snap["name"] not in cli.snapshots


def test_bucket_flow(cli, capsys):
    b = j(run(capsys, "bucket", "create", "mybkt")[1])
    assert b["secret_key"]
    assert [x["name"] for x in j(run(capsys, "bucket", "list")[1])] == ["mybkt"]
    assert j(run(capsys, "bucket", "usage", "mybkt")[1])["used_bytes"] == 0
    assert run(capsys, "bucket", "quota", "mybkt", "--size-gib", "5")[0] == 0
    assert run(capsys, "bucket", "quota", "mybkt")[0] == 2
    assert run(capsys, "bucket", "usage", "ghost")[0] == 5
    rc, _, err = run(capsys, "bucket", "delete", "mybkt")
    assert rc == 7 and "mybkt" in cli.buckets


def test_billing(cli, capsys):
    assert j(run(capsys, "billing", "tier")[1])["tier"] == "verified"
    assert j(run(capsys, "billing", "balance")[1])["balance"] == 25.0
    assert j(run(capsys, "billing", "invoices")[1])[0]["name"] == "INV-1"
    assert run(capsys, "billing", "usage")[0] == 0
    rc, out, _ = run(capsys, "billing", "health")
    assert rc == 0 and j(out)["ok"] is True
    rc, out, _ = run(capsys, "billing", "health", "--min-balance", "100")
    assert rc == 1 and j(out)["ok"] is False


def test_notifications(cli, capsys, monkeypatch):
    from fcctl import notifications
    calls = []
    monkeypatch.setattr(notifications, "list_notifications",
                        lambda c, unread, start, limit: calls.append(unread) or [{"name": "N-1"}])
    monkeypatch.setattr(notifications, "mark_read", lambda c, n: {"read": n})
    monkeypatch.setattr(notifications, "mark_all_read", lambda c: {"all": True})
    assert j(run(capsys, "notification", "list", "--unread")[1])[0]["name"] == "N-1" and calls == [True]
    assert j(run(capsys, "notification", "read", "N-1")[1]) == {"read": "N-1"}
    assert j(run(capsys, "notification", "read", "--all")[1]) == {"all": True}
    assert run(capsys, "notification", "read")[0] == 2


def test_unauthenticated_exit_3(env, fake, capsys):
    run(capsys, "profile", "create", "m", "--email", "user@example.com", "--team", "TEAM-1", "--policy", "full")
    run(capsys, "profile", "use", "m")
    assert run(capsys, "server", "list")[0] == 3


def test_region_default_is_configurable(cli, capsys, monkeypatch):
    from fcctl import infra
    from fcctl.region import DEFAULT_REGION, default_region
    monkeypatch.setattr(infra, "plans_eligible", lambda c, **k: [{"plan": "p1", **k}])
    assert default_region({}) == DEFAULT_REGION == "bom"
    assert default_region({"FCCTL_REGION": " blr "}) == "blr"
    assert default_region({"FCCTL_REGION": ""}) == "bom"
    monkeypatch.setenv("FCCTL_REGION", "blr")
    assert j(run(capsys, "server", "plans")[1])[0]["region"] == "blr"
    assert j(run(capsys, "server", "plans", "--region", "fra")[1])[0]["region"] == "fra"
    monkeypatch.delenv("FCCTL_REGION")
    assert j(run(capsys, "server", "plans")[1])[0]["region"] == "bom"

