"""config.toml, profile store, credential chain."""
from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ._fs import private_dir
from .policy import EffectivePolicy, resolve_policy
from .profiles import Profile, parse_profiles
from .request import AuthError, FcError, ReauthFailed

_FIELDS = ("email", "team", "policy", "allow", "owner", "owner_id", "session_store", "session_ref", "approver_pubkeys")


class ConfigError(FcError):
    default_why = "The fcctl configuration (config.toml, profiles or env file) is missing or invalid."
    default_fix = "Check it with `fcctl doctor`; list profiles with `fcctl profile list`."


class InsecureEnvFile(ConfigError):
    default_why = "The env file holds secrets but is readable by other users."
    default_fix = "Run `chmod 600` on the env file and retry."


class NoCredentials(AuthError):
    default_why = "No Frappe Cloud credentials were found in any source."
    default_fix = "Run `fcctl auth login`, or set FCCTL_SID / FCCTL_EMAIL (or FCCTL_ENV_FILE) and FCCTL_KEY."


def home(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FCCTL_HOME") or Path.home() / ".fcctl")


def config_path(env: Mapping[str, str] | None = None) -> Path:
    return home(env) / "config.toml"


def load_config(path: Path | str | None = None) -> dict:
    p = Path(path) if path else config_path()
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"cannot parse config file {p}", what=f"config file {p} is not valid TOML",
                          why=f"TOML syntax error: {e}",
                          fix=f"Fix the syntax in {p} (or move it aside and re-create profiles with `fcctl profile create`).") from e


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{s}"'


def _key(k: str) -> str:
    return k if k and all(c.isalnum() or c in "_-" for c in k) else _val(k)


def dumps(data: dict) -> str:
    """Minimal TOML writer: scalars/lists at top of a table, nested dicts as [a.b] tables."""
    lines: list[str] = []

    def emit(table: dict, prefix: str):
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subs = {k: v for k, v in table.items() if isinstance(v, dict)}
        if prefix and (scalars or not subs):
            lines.append(f"[{prefix}]")
        for k, v in scalars.items():
            lines.append(f"{_key(k)} = {_val(v)}")
        if prefix and (scalars or not subs):
            lines.append("")
        for k, v in subs.items():
            emit(v, f"{prefix}.{_key(k)}" if prefix else _key(k))

    emit(data, "")
    return "\n".join(lines).strip() + "\n"


def save_config(data: dict, path: Path | str | None = None) -> Path:
    p = Path(path) if path else config_path()
    private_dir(p.parent)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return p


class ProfileStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else config_path()

    def _data(self) -> dict:
        return load_config(self.path)

    def list(self) -> list[Profile]:
        return parse_profiles(self._data())

    def get(self, name: str) -> Profile:
        for p in self.list():
            if p.name == name:
                return p
        raise ConfigError(f"unknown profile {name!r}", what=f"unknown profile {name!r}",
                          why="no profile with that name exists in config.toml",
                          fix="List profiles with `fcctl profile list`, or create one with `fcctl profile create`.")

    def current(self) -> str | None:
        return (self._data().get("fcctl") or {}).get("current")

    def set(self, profile: Profile) -> Profile:
        d = self._data()
        row: dict[str, Any] = {"email": profile.login, "team": profile.team, "policy": profile.policy}
        if profile.allow:
            row["allow"] = list(profile.allow)
        for k in ("owner", "owner_id", "session_store", "session_ref"):
            if getattr(profile, k):
                row[k] = getattr(profile, k)
        if profile.approver_pubkeys:
            row["approver_pubkeys"] = dict(profile.approver_pubkeys)
        d.setdefault("profiles", {})[profile.name] = row
        save_config(d, self.path)
        return profile

    def create(self, profile: Profile) -> Profile:
        if profile.name in (self._data().get("profiles") or {}):
            raise ConfigError(f"profile {profile.name!r} already exists", why="profile names must be unique",
                              fix="Choose another name, or remove the old one with `fcctl profile delete`.")
        return self.set(profile)

    def delete(self, name: str) -> None:
        d = self._data()
        if name not in (d.get("profiles") or {}):
            raise ConfigError(f"unknown profile {name!r}", what=f"unknown profile {name!r}",
                              why="no profile with that name exists in config.toml",
                              fix="List profiles with `fcctl profile list`.")
        del d["profiles"][name]
        if (d.get("fcctl") or {}).get("current") == name:
            del d["fcctl"]["current"]
        save_config(d, self.path)

    def use(self, name: str) -> None:
        self.get(name)
        d = self._data()
        d.setdefault("fcctl", {})["current"] = name
        save_config(d, self.path)


def select_profile(explicit: str | None = None, env: Mapping[str, str] | None = None,
                   store: ProfileStore | None = None) -> Profile:
    env = os.environ if env is None else env
    store = store or ProfileStore(config_path(env))
    name = explicit or env.get("FCCTL_PROFILE") or store.current()
    if not name:
        raise ConfigError("no profile selected", what="no profile selected",
                          why="neither --profile, FCCTL_PROFILE nor a current profile is set",
                          fix="Pass --profile NAME, set FCCTL_PROFILE, or run `fcctl profile use NAME`.")
    return store.get(name)


def session_key(profile: Profile) -> str:
    """Profiles sharing a login share one session; session_ref names the profile that holds it."""
    return profile.session_ref or profile.name


def session_login(profile: Profile, profiles: list[Profile]) -> str:
    ref = next((p for p in profiles if p.name == profile.session_ref), None)
    return ref.login if ref else profile.login


def check_owner(profile: Profile, env: Mapping[str, str] | None = None) -> None:
    """Call only when a new OTP is needed."""
    env = os.environ if env is None else env
    if profile.owner == "automation" and profile.owner_id != env.get("FCCTL_OWNER_ID"):
        raise ReauthFailed(f"this profile's session is owned by {profile.owner_id}")


def conflicts(profiles: list[Profile]) -> list[str]:
    out: list[str] = []
    seen: set = set()
    for p in profiles:
        k = (p.login, p.team)
        if k in seen:
            continue
        seen.add(k)
        out.extend(resolve_policy(p.login, p.team, profiles).warnings)
    return out


def effective_policy(profile: Profile, profiles: list[Profile]) -> EffectivePolicy:
    return resolve_policy(profile.login, profile.team, profiles)


@dataclass(frozen=True)
class Credentials:
    login: str | None = None
    sid: str | None = None
    csrf: str | None = None
    source: str = ""


def check_env_file(path: Path | str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"env file not found: {p}", why="FCCTL_ENV_FILE / --env-file points to a path that is not a file",
                          fix="Correct the path or create the file with mode 600.")
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise InsecureEnvFile(f"env file {p} is accessible by group/others", fix=f"Run: chmod 600 {p}")
    return p


def parse_env_file(path: Path | str) -> dict[str, str]:
    out = {}
    for line in check_env_file(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.removeprefix("export ").split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def _from_map(m: Mapping[str, str], source: str) -> Credentials | None:
    sid, login = m.get("FCCTL_SID"), m.get("FCCTL_EMAIL") or m.get("FCCTL_LOGIN")
    if not (sid or login):
        return None
    return Credentials(login, sid, m.get("FCCTL_CSRF"), source)


def _keyring(profile: Profile | None) -> Credentials | None:
    if profile is None:
        return None
    try:
        import json
        import keyring
        raw = keyring.get_password("fcctl", session_key(profile))
        d = json.loads(raw) if raw else None
    except Exception:
        return None
    return Credentials(profile.login, d.get("sid"), d.get("csrf"), "keyring") if d else None


def _encrypted(env: Mapping[str, str]) -> Credentials | None:
    try:
        from .keys import get_key
        from .session_store import SessionStore
        key = get_key(env, home(env))
        if not key:
            return None
        s = SessionStore(path=home(env) / "session.enc", key=key)
    except Exception:
        return None
    return Credentials(None, s.sid, s.csrf, "encrypted_file") if s.sid else None


def resolve_credentials(explicit: Credentials | Mapping[str, str] | None = None,
                        env: Mapping[str, str] | None = None, env_file: Path | str | None = None,
                        profile: Profile | None = None) -> Credentials:
    env = os.environ if env is None else env
    c: Credentials | None
    if explicit:
        c = explicit if isinstance(explicit, Credentials) else Credentials(
            explicit.get("login"), explicit.get("sid"), explicit.get("csrf"), "explicit")
        if c.sid or c.login:
            return Credentials(c.login, c.sid, c.csrf, "explicit")
    c = _from_map(env, "env")
    if c:
        return c
    ef = env_file or env.get("FCCTL_ENV_FILE")
    if ef:
        c = _from_map(parse_env_file(ef), "env_file")
        if c:
            return c
    c = _keyring(profile) or _encrypted(env)
    if c:
        return c
    raise NoCredentials("no credentials found", what="no credentials found",
                        why="checked explicit values, environment, env file, keyring and the encrypted session file",
                        fix="Run `fcctl auth login`, or set FCCTL_SID/FCCTL_EMAIL, and FCCTL_KEY for the encrypted store.")
