"""Encrypted persistence of sid/csrf/pilot tokens (Fernet, key from env FCCTL_KEY).

Generate a key:  python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ._fs import open_private, private_dir
from .errors import FcError
from .keys import get_key

DEFAULT_DIR = Path(os.environ.get("FCCTL_HOME", Path.home() / ".fcctl"))


class SessionStoreError(FcError, RuntimeError):
    default_why = "The encrypted session file cannot be opened with the configured FCCTL_KEY."
    default_fix = "Check FCCTL_KEY matches the key used to create the file, or delete session.enc and run `fcctl auth login`."


class SessionStore:
    def __init__(self, path: Path | str | None = None, key: str | bytes | None = None,
                 profile_dir: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_DIR / "session.enc"
        self.profile_dir = Path(profile_dir) if profile_dir else DEFAULT_DIR / "profile"
        key = key or get_key(home=self.path.parent)
        if not key:
            raise SessionStoreError(
                "FCCTL_KEY is not set", what="FCCTL_KEY is not set",
                why="the session file is encrypted and needs a Fernet key (env, OS keyring or key file)",
                fix="run: fcctl init")
        self._key = key
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, TypeError) as e:
            raise SessionStoreError(
                "FCCTL_KEY is malformed", what="FCCTL_KEY is not a valid Fernet key",
                why="a Fernet key must be 32 url-safe base64-encoded bytes (44 characters)",
                fix="Unset the bad FCCTL_KEY, or replace the stored key; then run: fcctl init") from e
        self.data: dict[str, Any] = self._read()

    # -- persistence
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as e:
            raise SessionStoreError(
                "cannot decrypt session file", what=f"cannot decrypt {self.path}",
                why="FCCTL_KEY differs from the key that encrypted this file, or the file is corrupt",
                fix="Export the original FCCTL_KEY, or delete the file and run `fcctl auth login` to start over.") from e

    def save(self) -> None:
        private_dir(self.path.parent)
        tmp = self.path.with_suffix(".tmp")
        with open_private(tmp, "wb") as f:
            f.write(self._fernet.encrypt(json.dumps(self.data).encode()))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # -- central
    @property
    def sid(self) -> str | None:
        return self.data.get("sid")

    @property
    def csrf(self) -> str | None:
        return self.data.get("csrf")

    def set_central(self, sid: str | None = None, csrf: str | None = None, **extra: Any) -> None:
        if sid:
            self.data["sid"] = sid
            self.data["sid_updated"] = time.time()
        if csrf:
            self.data["csrf"] = csrf
        self.data.update(extra)
        self.save()

    # -- pilot (one token per server)
    def get_pilot(self, server: str, skew: float = 60.0) -> dict[str, Any] | None:
        p = self.data.get("pilots", {}).get(server)
        if not p or (p.get("expires_at") and p["expires_at"] - skew <= time.time()):
            return None
        return p

    def set_pilot(self, server: str, token: str, gateway: str, ttl: float = 3000.0) -> None:
        self.data.setdefault("pilots", {})[server] = {
            "token": token, "gateway": gateway, "expires_at": time.time() + ttl}
        self.save()

    def drop_pilot(self, server: str) -> None:
        self.data.get("pilots", {}).pop(server, None)
        self.save()
