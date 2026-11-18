"""FCCTL_KEY resolution and first-run key creation.

Order: env FCCTL_KEY -> OS keyring (service "fcctl", user "session-key", optional `keyring` package)
-> 0600 key file `<FCCTL_HOME>/session.key`. The key is never printed.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping

from cryptography.fernet import Fernet

from ._fs import open_private, private_dir
from .errors import FcError

KEYRING_SERVICE = "fcctl"
KEYRING_USER = "session-key"
KEY_FILE = "session.key"


class KeyFileInsecure(FcError):
    default_why = "The key file is readable by group or other users."
    default_fix = "Run `chmod 600` on the key file."


def _home(env: Mapping[str, str] | None, home: Path | str | None) -> Path:
    if home:
        return Path(home)
    env = os.environ if env is None else env
    return Path(env.get("FCCTL_HOME") or Path.home() / ".fcctl")


def key_file(env: Mapping[str, str] | None = None, home: Path | str | None = None) -> Path:
    return _home(env, home) / KEY_FILE


def keyring_get() -> str | None:
    """The key from the OS keyring, or None when keyring/backend is unavailable or empty."""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or None
    except Exception:
        return None


def keyring_set(key: str) -> bool:
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) == key
    except Exception:
        return False


def read_key_file(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise KeyFileInsecure(f"key file {path} is accessible by group/others", fix=f"Run: chmod 600 {path}")
    return path.read_text().strip() or None


def resolve_key(env: Mapping[str, str] | None = None, home: Path | str | None = None) -> tuple[str | None, str]:
    """Return (key, source) where source is env|keyring|file|none."""
    env = os.environ if env is None else env
    if env.get("FCCTL_KEY"):
        return env["FCCTL_KEY"], "env"
    k = keyring_get()
    if k:
        return k, "keyring"
    k = read_key_file(key_file(env, home))
    if k:
        return k, "file"
    return None, "none"


def get_key(env: Mapping[str, str] | None = None, home: Path | str | None = None) -> str | None:
    return resolve_key(env, home)[0]


def init_key(env: Mapping[str, str] | None = None, home: Path | str | None = None,
             store: str | None = None) -> tuple[str, str]:
    """Ensure a key exists. Returns (status, location): status is created|exists."""
    h = _home(env, home)
    private_dir(h)
    key, source = resolve_key(env, h)
    if key:
        return "exists", {"env": "environment FCCTL_KEY", "keyring": "OS keyring",
                          "file": str(key_file(env, h))}[source]
    new = Fernet.generate_key().decode()
    if store != "file" and keyring_set(new):
        return "created", "OS keyring"
    if store == "keyring":
        raise FcError("OS keyring unavailable", what="cannot store the key in the OS keyring",
                      why="the optional `keyring` package or a keyring backend is not available",
                      fix="pip install 'fcctl[keyring]', or run `fcctl init --store file`")
    p = key_file(env, h)
    with open_private(p, "w") as f:
        f.write(new + "\n")
    os.chmod(p, 0o600)
    return "created", str(p)
