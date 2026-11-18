"""Append-only, hash-chained JSONL audit log."""
from __future__ import annotations

import fcntl
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Protocol

from ._fs import open_private, private_dir
from .redact import REDACTED, SECRET_KEY_RE, redact  # noqa: F401

FIELDS = ("actor", "agent", "profile", "login", "team", "op", "target", "approval_id", "workflow_id",
          "request_id", "cf_ray", "task_id", "outcome")


def _canon(row: dict) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _hash(row: dict) -> str:
    return hashlib.sha256(_canon({k: v for k, v in row.items() if k != "hash"}).encode()).hexdigest()


class AuditSink(Protocol):
    def emit(self, row: dict) -> None: ...


class WebhookSink:
    """Off-host copy. `poster(url, row)` is injected (e.g. httpx.post); failures never break auditing."""

    def __init__(self, url: str, poster: Callable[[str, dict], object]):
        self.url, self.poster = url, poster

    def emit(self, row: dict) -> None:
        self.poster(self.url, row)


class AuditLog:
    def __init__(self, path, sinks=(), now=time.time):
        self.path = Path(path)
        private_dir(self.path.parent)
        self.sinks, self.now = list(sinks), now

    def _rows_raw(self):
        if not self.path.exists():
            return []
        return self.path.read_text().splitlines()

    def append(self, **fields) -> dict:
        extra = {k: v for k, v in fields.items() if k not in FIELDS}
        with open_private(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
                seq, prev = 1, ""
                if lines:
                    last = json.loads(lines[-1])
                    seq, prev = last["seq"] + 1, last.get("hash", "")
                row = {"seq": seq, "time": self.now(), **{k: None for k in FIELDS}, "prev": prev}
                row.update({k: v for k, v in fields.items() if k in FIELDS})
                if extra:
                    row["extra"] = extra
                row = redact(row)
                row["hash"] = _hash(row)
                f.write(_canon(row) + "\n")
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        for s in self.sinks:
            try:
                s.emit(row)
            except Exception:
                pass
        return row

    def verify(self) -> tuple[bool, int | None]:
        prev, expect = "", 1
        for i, ln in enumerate(self._rows_raw(), 1):
            try:
                row = json.loads(ln)
            except ValueError:
                return False, expect
            if (row.get("seq") != expect or row.get("prev") != prev or row.get("hash") != _hash(row)):
                return False, row.get("seq") if isinstance(row.get("seq"), int) else expect
            prev, expect = row["hash"], expect + 1
        return True, None

    def list(self, **filters) -> list[dict]:
        out = []
        for ln in self._rows_raw():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if all(r.get(k) == v for k, v in filters.items() if v is not None):
                out.append(r)
        return out

    def export(self, **filters) -> str:
        return "\n".join(_canon(r) for r in self.list(**filters)) + "\n"
