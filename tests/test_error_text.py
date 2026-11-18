"""Every error class and common raise site carries specific why/fix text."""
import inspect

import pytest
from cryptography.fernet import Fernet

from fcctl import config, errors, guard
from fcctl.config import ConfigError, ProfileStore
from fcctl.errors import GENERIC_FIX, FcError, format_error
from fcctl.session_store import SessionStore, SessionStoreError

SECRET = "sid=abc123SECRET"


def _all(mod, base):
    return [c for _, c in inspect.getmembers(mod, inspect.isclass) if issubclass(c, base) and c is not base]


def _check(e, secret=None):
    out = format_error(e)
    lines = out.splitlines()
    why = next(l for l in lines if l.startswith("why: "))[5:]
    fix = next(l for l in lines if l.startswith("fix: "))[5:]
    assert why and why != type(e).__name__
    assert fix and fix != GENERIC_FIX
    if secret:
        assert secret not in out
    return out


@pytest.mark.parametrize("cls", _all(errors, FcError) + [config.ConfigError, config.InsecureEnvFile,
                                                         config.NoCredentials, SessionStoreError])
def test_every_fcerror_has_specific_text(cls):
    e = cls("boom " + SECRET)
    _check(e, "abc123SECRET")
    assert cls.default_why and cls.default_fix and cls.default_fix != GENERIC_FIX


@pytest.mark.parametrize("cls", _all(guard, guard.GuardError) + [guard.GuardError])
def test_every_guard_error_has_specific_text(cls):
    _check(cls("x"))


def test_unknown_exception_gets_only_generic_fix():
    out = format_error(KeyError("k"))
    assert GENERIC_FIX in out and "why: KeyError" not in out


def test_missing_key(tmp_path):
    with pytest.raises(SessionStoreError) as ei:
        SessionStore(path=tmp_path / "s.enc", key="")
    out = _check(ei.value)
    assert "fcctl init" in out and "FCCTL_KEY" in out


def test_malformed_key_not_echoed(tmp_path):
    bad = "not-a-fernet-key-SECRETVALUE"
    with pytest.raises(SessionStoreError) as ei:
        SessionStore(path=tmp_path / "s.enc", key=bad)
    out = _check(ei.value, bad)
    assert "fcctl init" in out and "ValueError" not in out


def test_wrong_key(tmp_path):
    p = tmp_path / "s.enc"
    s = SessionStore(path=p, key=Fernet.generate_key().decode())
    s.set_central(sid="x")
    with pytest.raises(SessionStoreError) as ei:
        SessionStore(path=p, key=Fernet.generate_key().decode())
    _check(ei.value)


def test_unknown_profile_and_none_selected(tmp_path):
    store = ProfileStore(tmp_path / "config.toml")
    with pytest.raises(ConfigError) as ei:
        store.get("nope")
    assert "nope" in _check(ei.value)
    with pytest.raises(ConfigError) as ei:
        config.select_profile(None, env={}, store=store)
    _check(ei.value)


def test_bad_config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("this is [not toml")
    with pytest.raises(ConfigError) as ei:
        config.load_config(p)
    assert str(p) in _check(ei.value)


def test_missing_credentials_and_env_file(tmp_path):
    with pytest.raises(config.NoCredentials) as ei:
        config.resolve_credentials(env={"FCCTL_HOME": str(tmp_path)})
    _check(ei.value)
    with pytest.raises(ConfigError) as ei:
        config.check_env_file(tmp_path / "missing.env")
    _check(ei.value)


def test_guard_missing_key(tmp_path):
    with pytest.raises(guard.GuardError) as ei:
        guard.Guard(home=tmp_path, key="", secret="")
    assert "fcctl init" in _check(ei.value)


def test_exit_codes_unchanged():
    assert errors.AuthError.exit_code == 3 and errors.PolicyDenied.exit_code == 4
    assert errors.WaitTimeout.exit_code == 8 and errors.OutcomeUnknown.exit_code == 13
    assert errors.exit_code_for(SessionStoreError("x")) == 1
