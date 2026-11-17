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

DEFAULT_DIR = Path(os.environ.get("FCCTL_HOME", Path.home() / ".fcctl"))


class SessionStoreError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, path: Path | str | None = None, key: str | bytes | None = None,
                 profile_dir: Path | str | None = None):
        self.path = Path(path) if path else DEFAULT_DIR / "session.enc"
        self.profile_dir = Path(profile_dir) if profile_dir else DEFAULT_DIR / "profile"
        key = key or os.environ.get("FCCTL_KEY")
        if not key:
            raise SessionStoreError("FCCTL_KEY is not set (Fernet key required)")
        self._key = key
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        self.data: dict[str, Any] = self._read()

    # -- persistence
    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as e:
            raise SessionStoreError("cannot decrypt session file (wrong FCCTL_KEY?)") from e

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
