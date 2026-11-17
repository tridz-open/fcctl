"""Private-by-default filesystem helpers for fcctl state (directories 0700, files 0600)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def _home() -> Path:
    return Path(os.environ.get("FCCTL_HOME") or Path.home() / ".fcctl")


def private_dir(path: Path | str) -> Path:
    """Create `path` (and parents) with mode 0700.

    A directory this call creates is always 0700. An existing directory is tightened to 0700 only when it
    is FCCTL_HOME itself (we never chmod a directory the caller merely pointed us at).
    """
    p = Path(path)
    existed = p.exists()
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed or p.resolve() == _home().resolve():
        try:
            os.chmod(p, 0o700)
        except OSError:
            pass
    return p


def open_private(path: Path | str, mode: str = "w") -> IO:
    """open() that creates the file with mode 0600."""
    return open(path, mode, opener=lambda p, flags: os.open(p, flags, 0o600))
