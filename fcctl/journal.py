"""File-backed operation journal. One JSON per op, atomic write + fsync.

pending -> dispatching -> submitted(handle) -> running -> succeeded|failed ; dispatching -> outcome_unknown.
`dispatching` is persisted BEFORE any network I/O; an exception after it means outcome_unknown (never retried
silently).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from ._fs import open_private, private_dir

PENDING, DISPATCHING, SUBMITTED, RUNNING = "pending", "dispatching", "submitted", "running"
SUCCEEDED, FAILED, UNKNOWN, STUCK, NEEDS_ATTENTION = (
    "succeeded", "failed", "outcome_unknown", "stuck", "needs_attention")
TERMINAL = {SUCCEEDED, FAILED}

TRANSITIONS = {
    PENDING: {DISPATCHING, FAILED},
    DISPATCHING: {SUBMITTED, UNKNOWN, FAILED, SUCCEEDED},
    SUBMITTED: {RUNNING, SUCCEEDED, FAILED, UNKNOWN, STUCK},
    RUNNING: {SUCCEEDED, FAILED, STUCK, UNKNOWN},
    STUCK: {RUNNING, SUCCEEDED, FAILED},
    UNKNOWN: {SUCCEEDED, FAILED, DISPATCHING},  # DISPATCHING only via proven-safe resubmit (reconcile)
    NEEDS_ATTENTION: {RUNNING, SUCCEEDED, FAILED},
    SUCCEEDED: set(),
    FAILED: set(),
}
for _s in (PENDING, DISPATCHING, SUBMITTED, RUNNING, STUCK):
    TRANSITIONS[_s] = TRANSITIONS[_s] | {NEEDS_ATTENTION}

# Idempotency classes (data, per op). Unknown ops default to NON_IDEMPOTENT.
REQUEST_KEY, IDEMPOTENCY_KEY, NON_IDEMPOTENT, CONVERGE, ABSENT_OK = (
    "request_key", "idempotency_key", "non_idempotent", "converge", "absent_ok")
IDEMPOTENCY = {
    "create_server": REQUEST_KEY,       # safe resend with same request_key
    "pilot_post": IDEMPOTENCY_KEY,      # Pilot POST routes honour Idempotency-Key
    "create_site": NON_IDEMPOTENT,
    "create_backup": NON_IDEMPOTENT,
    "restore_backup": NON_IDEMPOTENT,
    "set_config": CONVERGE,             # converge-on-state: reapply is a no-op
    "install_app": CONVERGE,
    "drop_site": ABSENT_OK,             # absent == success
    "delete_server": ABSENT_OK,
    "delete_app": ABSENT_OK,
}
SAFE_RESEND = {REQUEST_KEY, IDEMPOTENCY_KEY, CONVERGE, ABSENT_OK}


def idempotency(op: str) -> str:
    return IDEMPOTENCY.get(op, NON_IDEMPOTENT)


class JournalError(Exception): ...
class InvalidTransition(JournalError): ...


class Journal:
    def __init__(self, state_dir, now=time.time):
        self.dir = Path(state_dir)
        private_dir(self.dir)
        self.now = now

    def _path(self, jid):
        return self.dir / f"{jid}.json"

    def _write(self, e: dict):
        p = self._path(e["id"])
        tmp = p.with_suffix(".tmp")
        with open_private(tmp, "w") as f:
            f.write(json.dumps(e, indent=1, sort_keys=True, default=str))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        try:
            fd = os.open(self.dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def get(self, jid) -> dict:
        return json.loads(self._path(jid).read_text())

    def list(self, states=None) -> list[dict]:
        out = [json.loads(p.read_text()) for p in sorted(self.dir.glob("*.json"))]
        return [e for e in out if states is None or e["state"] in states]

    def create(self, op, args=None, idem_key=None, jid=None) -> dict:
        t = self.now()
        e = {"id": jid or uuid.uuid4().hex[:12], "op": op, "args": args or {},
             "idempotency": idempotency(op), "idem_key": idem_key or uuid.uuid4().hex,
             "state": PENDING, "handle": None, "result": None, "error": None,
             "created_at": t, "updated_at": t, "history": [[PENDING, t]]}
        self._write(e)
        return e

    def transition(self, jid, state, **fields) -> dict:
        e = self.get(jid)
        if state not in TRANSITIONS.get(e["state"], set()):
            raise InvalidTransition(f"{e['state']} -> {state} not allowed for {jid}")
        t = self.now()
        e.update(fields)
        e["state"], e["updated_at"] = state, t
        e["history"].append([state, t])
        self._write(e)
        return e

    def prune(self, max_age=30 * 86400) -> int:
        n = 0
        for e in self.list(TERMINAL):
            if self.now() - e["updated_at"] > max_age:
                self._path(e["id"]).unlink()
                n += 1
        return n

    def execute(self, op, fn, args=None, jid=None, idem_key=None):
        """Run fn(entry) (the network call) under journal protection; returns fn's result.
        `fn` may return a dict with 'handle'/'task_id'/'id' to be recorded as the submitted handle."""
        e = self.get(jid) if jid else self.create(op, args, idem_key)
        jid = e["id"]
        self.transition(jid, DISPATCHING)  # fsynced before any network I/O
        try:
            res = fn(self.get(jid))
        except BaseException as exc:
            self.transition(jid, UNKNOWN, error=f"{type(exc).__name__}: {exc}")
            raise
        handle = None
        if isinstance(res, dict):
            handle = res.get("handle") or res.get("task_id") or res.get("id")
        self.transition(jid, SUBMITTED, handle=handle)
        return res
