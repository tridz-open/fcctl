"""--dry-run of every named mutating op must show the would-be HTTP requests and do nothing else.

Iterates policy.REGISTRY so a new non-read op cannot silently lack dry-run coverage: it must either get an argv
in CASES (and then show requests[]) or be listed in NO_CLI with a reason.
"""
import json
import os
import pathlib

import pytest

from fcctl import policy
from tests.test_cli_pilot import S, prof, run  # noqa: F401  (fixtures env/fake/ready come via conftest import below)
from tests.test_cli_pilot import env, fake, ready  # noqa: F401

SITE = "a.x.com"

# op id -> (argv after global flags, note-required)
CASES = {
    "server.create": (["server", "create", "--title", "t", "--plan", "p", "--image", "i"], False),
    "server.resize": (["server", "resize", "srv-1", "--plan", "p"], False),
    "server.set_termination_protection": (["server", "protect", "srv-1", "off"], False),
    "server.terminate": (["server", "terminate", "srv-1"], False),
    "server.start": (["server", "start", "srv-1"], False),
    "server.restart": (["server", "restart", "srv-1"], False),
    "server.stop": (["server", "stop", "srv-1"], False),
    "snapshot.take": (["snapshot", "take", "srv-1"], False),
    "snapshot.delete": (["snapshot", "delete", "snap-1"], False),
    "bucket.create": (["bucket", "create", "bk"], False),
    "bucket.set_quota": (["bucket", "quota", "bk", "--size-gib", "5"], False),
    "bucket.delete": (["bucket", "delete", "bk"], False),
    "notification.read": (["notification", "read", "n1"], False),
    "notification.read_all": (["notification", "read", "--all"], False),
    "site.create": (["site", "create", "n.x.com"], False),
    "site.install_app": (["app", "install", "erpnext", "--site", SITE], False),
    "site.uninstall_app": (["app", "uninstall", "erpnext", "--site", SITE], False),
    "site.set_maintenance": (["site", "maintenance", SITE, "on"], False),
    "site.clear_cache": (["site", "clear-cache", SITE], False),
    "site.migrate": (["site", "migrate", SITE], False),
    "site.login_link": (["site", "login", SITE], False),
    "site.rename": (["site", "rename", SITE, "b.x.com"], False),
    "site.drop": (["site", "drop", SITE], False),
    "site.config_set": (["site", "config", "set", SITE, "k=v"], False),
    "app.check_updates": (["app", "check-updates"], False),
    "app.add_marketplace": (["app", "add", "erpnext"], False),
    "app.add_repo": (["app", "add", "--repo", "https://example.com/r.git"], False),
    "app.remove": (["app", "remove", "erpnext"], False),
    "backup.create": (["backup", "create", SITE], False),
    "backup.restore": (["backup", "restore", SITE, "20240101_000000"], False),
    "task.cancel": (["task", "cancel", "t1"], False),
    "workflow.provision_site": (["site", "provision", "p.x.com"], True),
}
# Registry ops with no named CLI command (reachable only through `fcctl api`, which already records requests).
NO_CLI = {
    "objects.upload_url": "library helper behind `bucket put`",
    "objects.upload": "library helper behind `bucket put`",
    "objects.delete": "library helper; no CLI command",
    "site.clone": "no CLI command",
    "site.build_assets": "no CLI command",
    "app.update": "no CLI command",
    "app.add_in_order": "library helper behind `app add --site`",
    "app.switch_branch": "no CLI command",
    "backup.set_schedule": "no CLI command",
    "backup.offsite_download": "no CLI command",
    "backup.restore_upload": "library helper; cross-site transfer is its own workflow",
}


def _mutating():
    return sorted(o for o, m in policy.REGISTRY.items() if m.get("kind") != "read")


def test_every_mutating_op_is_covered_or_exempt():
    missing = [o for o in _mutating() if o not in CASES and o not in NO_CLI]
    assert not missing, f"add a dry-run case (or a NO_CLI reason) for: {missing}"
    stale = [o for o in list(CASES) + list(NO_CLI) if o not in policy.REGISTRY]
    assert not stale, stale


def _files(root):
    return sorted(str(p.relative_to(root)) for p in pathlib.Path(root).rglob("*") if p.is_file())


@pytest.mark.parametrize("op", sorted(CASES))
def test_dry_run_shows_requests_and_writes_nothing(op, ready, capsys, tmp_path):
    argv, needs_note = CASES[op]
    home = pathlib.Path(os.environ["FCCTL_HOME"])
    before_files = _files(home)
    before_calls = len(ready.calls)
    rc, out, err = run(capsys, "--dry-run", *S, *argv)
    assert rc == 0, (out, err)
    d = json.loads(out)
    assert d["dry_run"] is True
    assert d["requests"], d
    for r in d["requests"]:
        assert r["method"] in ("POST", "PUT", "DELETE", "PATCH") and r["path"]
    if needs_note:
        assert "note" in d and "response" in d["note"]
    # nothing sent beyond what the baseline read-only lookups do; no state written
    assert not [c for c in ready.calls[before_calls:] if getattr(c, "method", "GET") != "GET"]
    after = _files(home)
    assert after == before_files, set(after) ^ set(before_files)
    assert not any("approval" in f or "journal" in f for f in set(after) - set(before_files))
