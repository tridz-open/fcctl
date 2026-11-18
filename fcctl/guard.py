"""Destructive-operation guard: approval step, signed store, append-only audit log.

Calling a guarded function does NOT execute it: it persists a PendingApproval (HMAC-signed with
FCCTL_KEY, 30 min TTL), alerts the admins, and returns the record. Someone then runs
`approve(id, approver, token)` with the approval secret (env FCCTL_APPROVAL_SECRET, as the secret itself
or a 6-digit RFC 6238 TOTP code derived from a base32 secret, so authenticator apps work). Only then is
the private unsafe implementation executed, once.

What this is: a speed bump against accidents, buggy scripts and over-eager agents that go through fcctl.
What it is not: a two-person control. Requester and approver are plain strings taken from the environment
(FCCTL_USER) or `--approver`; they are not authenticated, and anyone who holds FCCTL_KEY and the approval
secret on one machine can approve their own request by supplying a different approver name. For real
separation of duties use separate Frappe Cloud accounts with restricted roles.

Env: FCCTL_KEY (signing), FCCTL_APPROVAL_SECRET, FCCTL_USER (requester identity, default OS user),
FCCTL_HOME (store dir: approvals.json, audit.jsonl).
"""
from __future__ import annotations

import base64
import binascii
import functools
import getpass
import hashlib
import hmac
import inspect
import json
import os
import struct
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from ._fs import open_private, private_dir

TTL = 30 * 60


class GuardError(Exception):
    default_why = "The approvals guard refused the request."
    default_fix = "List pending approvals with `fcctl approval list` and check FCCTL_KEY."

    def __init__(self, message: str = "", *, why: str | None = None, fix: str | None = None):
        super().__init__(message)
        self.why, self.fix = why or self.default_why, fix or self.default_fix


class SelfApproval(GuardError):
    default_why = "The requester and the approver are the same identity, which is not allowed."
    default_fix = "Ask a different person to approve, or pass a different --approver."


class Expired(GuardError):
    default_why = "Approvals expire 30 minutes after they are requested."
    default_fix = "Re-run the original command to create a fresh approval request."


class BadToken(GuardError):
    default_why = "The approval token or one-time code does not match."
    default_fix = "Re-enter the current code, or have the approver use the approval secret."


class Tampered(GuardError):
    default_why = "The approvals store failed its signature check, so it was modified or FCCTL_KEY changed."
    default_fix = "Restore the original FCCTL_KEY; if it was edited by hand, move approvals.json aside and re-request."


class NotPending(GuardError):
    default_why = "That approval is not pending; it was already executed, denied or expired."
    default_fix = "Check its state with `fcctl approval list`; re-run the command for a new approval."


@dataclass
class PendingApproval:
    id: str
    op: str
    target: str
    requested_by: str
    expires_at: float
    reason: str = ""
    status: str = "pending"  # pending|executing|executed|failed|outcome_unknown|denied|expired
    args: dict = field(default_factory=dict)
    kind: str = "central"  # central|pilot
    server: str | None = None
    created_at: float = 0.0
    decided_by: str | None = None
    result: object = None


def new_secret() -> str:
    """A fresh 160-bit base32 TOTP secret (RFC 4648), as authenticator apps expect."""
    return base64.b32encode(os.urandom(20)).decode()


def provisioning_uri(secret: str, user: str = "approver", issuer: str = "fcctl") -> str:
    """otpauth:// URI for authenticator apps (render it as a QR code with any tool)."""
    return f"otpauth://totp/{quote(issuer)}:{quote(user)}?secret={secret}&issuer={quote(issuer)}"


def _b32(secret: str) -> bytes:
    s = secret.strip().replace(" ", "").upper()
    try:
        return base64.b32decode(s + "=" * (-len(s) % 8))
    except (binascii.Error, ValueError) as e:
        raise ValueError("approval secret is not valid base32") from e


def totp(secret: str, at: float | None = None, step: int = 30) -> str:
    """RFC 6238 TOTP (HMAC-SHA1, 6 digits). `secret` is base32."""
    counter = int((time.time() if at is None else at) // step)
    h = hmac.new(_b32(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0xF
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


class Guard:
    def __init__(self, home=None, key=None, secret=None, alerter=None, now=time.time, user=None):
        self.home = Path(home or os.environ.get("FCCTL_HOME") or Path.home() / ".fcctl")
        private_dir(self.home)
        from .keys import get_key
        self.key = (key or get_key(home=self.home) or "").encode()
        if not self.key:
            raise GuardError("FCCTL_KEY is not set", why="the approvals store is HMAC-signed with FCCTL_KEY",
                             fix="run: fcctl init")
        self.secret = secret if secret is not None else os.environ.get("FCCTL_APPROVAL_SECRET", "")
        self.alerter, self.now = alerter, now
        self.user = user or os.environ.get("FCCTL_USER") or getpass.getuser()
        self.store_path, self.audit_path = self.home / "approvals.json", self.home / "audit.jsonl"

    # ---- signed store ----
    def _sig(self, rec: dict) -> str:
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hmac.new(self.key, body, hashlib.sha256).hexdigest()

    def _load(self) -> dict[str, PendingApproval]:
        if not self.store_path.exists():
            return {}
        try:
            raw = json.loads(self.store_path.read_text())
        except ValueError as e:
            raise Tampered("approvals store is not valid JSON") from e
        out = {}
        for pid, item in raw.items():
            rec, sig = item.get("rec"), item.get("sig", "")
            if not isinstance(rec, dict) or not hmac.compare_digest(self._sig(rec), sig):
                raise Tampered(f"approval {pid} failed signature check; store rejected")
            out[pid] = PendingApproval(**rec)
        return out

    def _save(self, recs: dict[str, PendingApproval]):
        data = {pid: {"rec": asdict(r), "sig": self._sig(asdict(r))} for pid, r in recs.items()}
        tmp = self.store_path.with_suffix(".tmp")
        with open_private(tmp, "w") as f:
            f.write(json.dumps(data, indent=1, default=str))
        os.replace(tmp, self.store_path)

    # ---- audit (append-only, hash-chained JSONL) ----
    def audit(self, event: str, rec: PendingApproval, actor: str, **extra):
        prev = ""
        if self.audit_path.exists():
            lines = self.audit_path.read_text().splitlines()
            if lines:
                prev = hashlib.sha256(lines[-1].encode()).hexdigest()
        entry = {"ts": self.now(), "event": event, "id": rec.id, "op": rec.op, "target": rec.target,
                 "actor": actor, "prev": prev, **extra}
        with open_private(self.audit_path, "a") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")

    # ---- .executing markers (carry journal_id) ----
    def _marker(self, pid):
        return self.home / f"{pid}.executing"

    def markers(self) -> list[dict]:
        out = []
        for p in sorted(self.home.glob("*.executing")):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                pass
        return out

    def remove_marker(self, pid):
        self._marker(pid).unlink(missing_ok=True)

    def finish_approval(self, pid, status, result=None):
        """Used by reconcile only: record the proven outcome. Never resets to pending."""
        recs = self._load()
        rec = recs[pid]
        rec.status, rec.result = status, result
        self._save(recs)
        self.audit("reconciled_" + status, rec, "reconcile")

    def _journal(self):
        from .journal import Journal
        return Journal(self.home / "journal", now=self.now)

    # ---- API ----
    def request(self, op, target, args, kind="central", server=None, reason="") -> PendingApproval:
        recs = self._load()
        t = self.now()
        rec = PendingApproval(id=uuid.uuid4().hex[:12], op=op, target=str(target), requested_by=self.user,
                              expires_at=t + TTL, reason=reason, args=args, kind=kind, server=server,
                              created_at=t)
        recs[rec.id] = rec
        self._save(recs)
        self.audit("requested", rec, self.user, reason=reason)
        if self.alerter:
            try:
                self.alerter.send(f"Approval needed: {op} {target}",
                                  f"{self.user} requested {op} on {target} (id {rec.id}, expires in 30 min). "
                                  f"Approve: fcctl approval approve {rec.id} --approver <you>")
            except Exception:
                pass
        return rec

    def list_pending(self) -> list[PendingApproval]:
        t = self.now()
        return [r for r in self._load().values() if r.status == "pending" and r.expires_at > t]

    def _check_token(self, token: str):
        if not self.secret:
            raise BadToken("FCCTL_APPROVAL_SECRET is not configured")
        token = (token or "").strip()
        ok = hmac.compare_digest(token, self.secret)
        t = self.now()
        if not ok:
            try:
                ok = any(hmac.compare_digest(token, totp(self.secret, t + d)) for d in (-30, 0, 30))
            except ValueError:  # secret is not base32: only the secret itself works as a token
                ok = False
        if not ok:
            raise BadToken("invalid approval token")

    def approve(self, pid: str, approver: str, token: str, client_factory=None):
        recs = self._load()
        rec = recs.get(pid)
        if rec is None:
            raise NotPending(f"no such approval {pid}")
        if rec.status == "executed":  # idempotent: never run twice
            self.audit("approve_noop", rec, approver)
            return rec.result
        if rec.status != "pending":
            raise NotPending(f"approval {pid} is {rec.status}")
        if self.now() >= rec.expires_at:
            rec.status = "expired"
            self._save(recs)
            self.audit("expired", rec, approver)
            raise Expired(f"approval {pid} expired")
        if approver == rec.requested_by:
            self.audit("self_approval_rejected", rec, approver)
            raise SelfApproval("approver name must differ from requester name (names are not authenticated)")
        try:
            self._check_token(token)
        except BadToken:
            self.audit("bad_token", rec, approver)
            raise
        # mark executing (signed + persisted) BEFORE running so a crash/retry cannot double-execute
        rec.status, rec.decided_by = "executing", approver
        self._save(recs)
        self.audit("approved", rec, approver)
        load_all()
        spec = REGISTRY.get(rec.op)
        if spec is None:
            raise GuardError(f"unknown op {rec.op}")
        jr = self._journal()
        entry = jr.create(rec.op, rec.args)  # pending entry first, then marker carries its id
        with open_private(self._marker(rec.id), "w") as f:
            f.write(json.dumps(
                {"approval_id": rec.id, "journal_id": entry["id"], "pid": os.getpid(), "ts": self.now()}))
        try:
            client = client_factory(rec.kind, rec.server) if client_factory else None
            result = jr.execute(rec.op, lambda _e: spec.fn(client, **rec.args), jid=entry["id"])
        except Exception as e:
            # outcome unknown: marker + journal entry stay for reconcile; never auto re-armed
            rec.status, rec.result = "outcome_unknown", str(e)
            self._save(recs)
            self.audit("execute_outcome_unknown", rec, approver, error=str(e))
            raise
        jr.transition(entry["id"], "running")
        jr.transition(entry["id"], "succeeded", result=json.loads(json.dumps(result, default=str)))
        self.remove_marker(rec.id)
        rec.status, rec.result = "executed", json.loads(json.dumps(result, default=str))
        self._save(recs)
        self.audit("executed", rec, approver)
        return rec.result

    def deny(self, pid: str, approver: str):
        recs = self._load()
        rec = recs.get(pid)
        if rec is None or rec.status != "pending":
            raise NotPending(f"approval {pid} is not pending")
        rec.status, rec.decided_by = "denied", approver
        self._save(recs)
        self.audit("denied", rec, approver)
        return rec


# ---- registry + decorator ----
@dataclass
class _Op:
    fn: object
    kind: str


def _central(name: str, mod: str = "servers"):
    """Lazy callable resolving fcctl.<mod>.<name> at approval time (avoids import cycles)."""
    def call(c, *a, **k):
        import importlib
        fn = getattr(importlib.import_module(f"fcctl.{mod}"), name)
        return getattr(fn, "_unsafe", fn)(c, *a, **k)
    return call


def _pilot(name: str, mod: str):
    """Lazy callable resolving the raw fcctl.<mod>.<name>._unsafe at approval time (Pilot ops)."""
    return _central(name, mod)


# Op ids as used by the CLI (dotted); @guarded adds the function-name ids on module import.
REGISTRY: dict[str, _Op] = {
    "server.terminate": _Op(_central("terminate_server"), "central"),
    "server.stop": _Op(_central("stop_server"), "central"),
    "server.resize": _Op(_central("resize_server"), "central"),
    "server.set_termination_protection": _Op(_central("set_termination_protection"), "central"),
    "snapshot.delete": _Op(_central("delete", "snapshots"), "central"),
    "bucket.delete": _Op(_central("delete_bucket", "objects"), "central"),
    "site.drop": _Op(_pilot("drop_site", "sites"), "pilot"),
    "site.uninstall_app": _Op(_pilot("uninstall_app", "sites"), "pilot"),
    "backup.restore": _Op(_pilot("restore", "backups"), "pilot"),
    "app.remove": _Op(_pilot("remove_app", "apps"), "pilot"),
    "app.switch_branch": _Op(_pilot("switch_branch", "apps"), "pilot"),
    "task.cancel": _Op(_pilot("cancel_task", "taskops"), "pilot"),
    # unclassified `fcctl api` writes: replayed verbatim (method, path, body) once approved
    "api.raw": _Op(_central("replay_raw", "rawapi"), "central"),
}
_default: Guard | None = None


def load_all():
    """Import every module holding guarded ops so REGISTRY is complete."""
    from . import apps, backups, objects, servers, sites, snapshots  # noqa: F401


def default_guard() -> Guard:
    global _default
    if _default is None:
        from .alerts import Alerter
        _default = Guard(alerter=Alerter())
    return _default


def set_default_guard(g: Guard | None):
    global _default
    _default = g


def _recording() -> bool:
    """True under --dry-run request capture: the raw op runs, but DryRunRecorder swallows every non-GET request,
    so nothing is sent and no approval is filed."""
    from .request import DryRunRecorder
    return DryRunRecorder.active()


def guarded(op: str, target, kind: str = "central"):
    """Decorate `fn(client, *args)`. The wrapper files a PendingApproval; `wrapper._unsafe` is the
    raw implementation, used only by Guard.approve. `target(**bound_args)` names the victim."""
    def deco(fn):
        sig = inspect.signature(fn)

        is_async = inspect.iscoroutinefunction(fn)
        if not is_async:  # the async twin never registers: approvals run the sync implementation
            REGISTRY[op] = _Op(fn, kind)

        def _file(client, a, k):
            b = sig.bind(client, *a, **k)
            b.apply_defaults()
            args = {n: v for n, v in b.arguments.items() if n != next(iter(sig.parameters))}
            args = json.loads(json.dumps(args, default=list))
            return default_guard().request(op, target(**args), args, kind=kind,
                                           server=getattr(client, "server", None))

        if is_async:
            @functools.wraps(fn)
            async def wrapper(client, *a, **k):
                if _recording():
                    return await fn(client, *a, **k)
                return _file(client, a, k)
        else:
            @functools.wraps(fn)
            def wrapper(client, *a, **k):
                if _recording():
                    return fn(client, *a, **k)
                return _file(client, a, k)
        wrapper._unsafe = fn
        wrapper.guard_op = op
        return wrapper
    return deco
