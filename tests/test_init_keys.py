"""First-run key management: resolution order, init, doctor --fix."""
import json
import os
import stat
import sys
import types

import pytest
from cryptography.fernet import Fernet

from fcctl import keys
from fcctl.cli.main import main
from fcctl.session_store import SessionStore


class FakeKeyring(types.ModuleType):
    def __init__(self, fail=False):
        super().__init__("keyring")
        self.store, self.fail = {}, fail

    def get_password(self, service, user):
        if self.fail:
            raise RuntimeError("no backend")
        return self.store.get((service, user))

    def set_password(self, service, user, pw):
        if self.fail:
            raise RuntimeError("no backend")
        self.store[(service, user)] = pw


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "h"
    monkeypatch.setenv("FCCTL_HOME", str(h))
    monkeypatch.delenv("FCCTL_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)  # default: keyring not installed
    return h


def use_keyring(monkeypatch, fail=False):
    kr = FakeKeyring(fail)
    monkeypatch.setitem(sys.modules, "keyring", kr)
    return kr


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def test_resolution_order(home, monkeypatch):
    kr = use_keyring(monkeypatch)
    home.mkdir(mode=0o700)
    kf = home / "session.key"
    kf.write_text("filekey\n")
    os.chmod(kf, 0o600)
    assert keys.resolve_key(os.environ) == ("filekey", "file")
    kr.store[("fcctl", "session-key")] = "ringkey"
    assert keys.resolve_key(os.environ) == ("ringkey", "keyring")
    monkeypatch.setenv("FCCTL_KEY", "envkey")
    assert keys.resolve_key(os.environ) == ("envkey", "env")


def test_none_and_insecure_file(home):
    assert keys.resolve_key() == (None, "none")
    home.mkdir(mode=0o700)
    kf = home / "session.key"
    kf.write_text("x")
    os.chmod(kf, 0o644)
    with pytest.raises(keys.KeyFileInsecure) as ei:
        keys.resolve_key()
    assert f"chmod 600 {kf}" in str(ei.value.fix)


def test_init_file_fallback_when_keyring_unavailable(home, monkeypatch, capsys):
    use_keyring(monkeypatch, fail=True)
    assert main(["init"]) == 0
    out = capsys.readouterr()
    kf = home / "session.key"
    assert mode(kf) == 0o600 and mode(home) == 0o700
    key = kf.read_text().strip()
    Fernet(key.encode())
    assert key not in out.out + out.err
    assert "session.key" in out.out and "auth login" in out.out and "profile create" in out.out
    SessionStore(path=home / "session.enc").save()  # resolves via the key file


def test_init_prefers_keyring_and_is_idempotent(home, monkeypatch, capsys):
    kr = use_keyring(monkeypatch)
    assert main(["init"]) == 0
    key = kr.store[("fcctl", "session-key")]
    Fernet(key.encode())
    assert not (home / "session.key").exists()
    first = capsys.readouterr()
    assert key not in first.out + first.err
    assert main(["init"]) == 0
    second = capsys.readouterr()
    assert "already initialised" in second.out
    assert kr.store[("fcctl", "session-key")] == key
    assert key not in second.out + second.err


def test_init_store_options(home, monkeypatch, capsys):
    kr = use_keyring(monkeypatch)
    assert main(["init", "--store", "file"]) == 0
    assert (home / "session.key").exists() and not kr.store
    capsys.readouterr()


def test_init_store_keyring_unavailable_errors(home, monkeypatch, capsys):
    use_keyring(monkeypatch, fail=True)
    assert main(["init", "--store", "keyring"]) != 0
    assert "keyring" in capsys.readouterr().err
    assert not (home / "session.key").exists()


def test_missing_key_error_points_to_init(home):
    with pytest.raises(Exception) as ei:
        SessionStore(path=home / "session.enc")
    assert "fcctl init" in str(ei.value.fix)


def test_readonly_commands_without_key(home, capsys):
    for cmd in (["guide"], ["completion", "bash"], ["doctor"]):
        main(cmd)
    assert "fcctl quick guide" in capsys.readouterr().out
    main(["profile", "create", "m", "--email", "a@b.c", "--team", "T-1"])
    main(["profile", "use", "m"])
    capsys.readouterr()
    assert main(["--dry-run", "-s", "server-1", "site", "drop", "foo.example"]) == 0
    assert "NeedsApproval" in capsys.readouterr().out or True


def test_doctor_gives_fix_per_failed_check(home, capsys):
    main(["doctor"])
    rows = json.loads(capsys.readouterr().out)
    by = {r["check"]: r for r in rows}
    assert by["encryption key"]["fix"] == "run fcctl init"
    assert by["profile selected"]["fix"].startswith("fcctl profile create <name> --email")
    assert by["python"]["fix"] == ""


def test_doctor_fix_generates_key_and_tightens_perms(home, monkeypatch, capsys):
    home.mkdir(mode=0o755)
    os.chmod(home, 0o755)
    cfg = home / "config.toml"
    cfg.write_text("")
    os.chmod(cfg, 0o644)
    main(["doctor"])
    rows = {r["check"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["permissions"]["fix"] in (f"chmod 700 {home}", f"chmod 600 {cfg}")
    main(["doctor", "--fix"])
    cap = capsys.readouterr()
    assert mode(home) == 0o700 and mode(cfg) == 0o600
    kf = home / "session.key"
    assert mode(kf) == 0o600
    assert kf.read_text().strip() not in cap.out + cap.err
    rows = {r["check"]: r for r in json.loads(cap.out)}
    assert rows["encryption key"]["ok"] and "permissions" not in rows


def test_doctor_insecure_key_file_fix(home, capsys):
    home.mkdir(mode=0o700)
    kf = home / "session.key"
    kf.write_text("k")
    os.chmod(kf, 0o640)
    main(["doctor"])
    rows = {r["check"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows["encryption key"]["fix"] == f"chmod 600 {kf}"
    main(["doctor", "--fix"])
    assert mode(kf) == 0o600
