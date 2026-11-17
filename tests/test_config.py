import os
import pytest
from fcctl import config as C
from fcctl.profiles import Profile
from fcctl.request import ReauthFailed


@pytest.fixture
def store(tmp_path):
    return C.ProfileStore(tmp_path / "c" / "config.toml")


def test_config_path(monkeypatch, tmp_path):
    assert C.config_path({"FCCTL_HOME": str(tmp_path)}) == tmp_path / "config.toml"


def test_roundtrip_and_mode(store):
    store.create(Profile("a", "a@x.io", "T1", "protected", ("backup.create",), "ops", "automation", "h1", "envkey"))
    store.create(Profile("b", "b@x.io", "T2"))
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    a = store.get("a")
    assert a.allow == ("backup.create",) and a.session_ref == "ops" and a.owner_id == "h1" and a.session_store == "envkey"
    assert store.get("b").policy == "guarded"
    with pytest.raises(C.ConfigError):
        store.create(Profile("b", "b@x.io", "T2"))


def test_quoting():
    d = {"profiles": {"p": {"email": 'a"b\\c', "team": "T", "allow": ["x", "y"]}}}
    import tomllib
    assert tomllib.loads(C.dumps(d)) == d


def test_use_delete_select(store):
    store.set(Profile("a", "a@x.io", "T"))
    store.set(Profile("b", "b@x.io", "T"))
    with pytest.raises(C.ConfigError):
        C.select_profile(env={}, store=store)
    store.use("a")
    assert C.select_profile(env={}, store=store).name == "a"
    assert C.select_profile(env={"FCCTL_PROFILE": "b"}, store=store).name == "b"
    assert C.select_profile("a", env={"FCCTL_PROFILE": "b"}, store=store).name == "a"
    store.delete("a")
    assert store.current() is None and [p.name for p in store.list()] == ["b"]
    with pytest.raises(C.ConfigError):
        store.use("zzz")


def test_session_key_and_owner():
    assert C.session_key(Profile("src", "l", "T", session_ref="ops")) == "ops"
    assert C.session_key(Profile("ops", "l", "T")) == "ops"
    p = Profile("ops", "l", "T", owner="automation", owner_id="h1")
    C.check_owner(p, {"FCCTL_OWNER_ID": "h1"})
    with pytest.raises(ReauthFailed, match="owned by h1"):
        C.check_owner(p, {})
    C.check_owner(Profile("me", "l", "T"), {})


def test_conflicts():
    ps = [Profile("a", "l", "T", "full"), Profile("b", "l", "T", "protected"), Profile("c", "l", "U")]
    w = C.conflicts(ps)
    assert len(w) == 1 and "a, b" in w[0]
    assert C.effective_policy(ps[0], ps).policy == "protected"


def test_credential_chain(tmp_path):
    assert C.resolve_credentials({"sid": "S"}, env={"FCCTL_SID": "E"}).source == "explicit"
    c = C.resolve_credentials(env={"FCCTL_SID": "E", "FCCTL_CSRF": "c"})
    assert (c.sid, c.csrf, c.source) == ("E", "c", "env")
    f = tmp_path / "e.env"
    f.write_text("# c\nexport FCCTL_SID='F'\nFCCTL_EMAIL=a@x.io\n")
    os.chmod(f, 0o644)
    with pytest.raises(C.InsecureEnvFile):
        C.resolve_credentials(env={}, env_file=f)
    os.chmod(f, 0o600)
    c = C.resolve_credentials(env={"FCCTL_ENV_FILE": str(f)})
    assert (c.sid, c.login, c.source) == ("F", "a@x.io", "env_file")
    with pytest.raises(C.NoCredentials):
        C.resolve_credentials(env={"FCCTL_HOME": str(tmp_path / "none")})


def test_encrypted_file_fallback(tmp_path):
    from cryptography.fernet import Fernet
    from fcctl.session_store import SessionStore
    key = Fernet.generate_key().decode()
    s = SessionStore(path=tmp_path / "session.enc", key=key)
    s.set_central(sid="ENC", csrf="cc")
    s.save()
    c = C.resolve_credentials(env={"FCCTL_HOME": str(tmp_path), "FCCTL_KEY": key})
    assert (c.sid, c.source) == ("ENC", "encrypted_file")
