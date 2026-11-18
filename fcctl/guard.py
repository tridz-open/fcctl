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

Optional hardening: set FCCTL_APPROVER_KEY, a secret SEPARATE from FCCTL_KEY and FCCTL_APPROVAL_SECRET (it must
differ from both; `approve` refuses otherwise). `approve` then stores an HMAC-SHA256 signature over (approval id,
op, target, args digest, approver name, timestamp) in the record, and execution verifies it against the verifier's
FCCTL_APPROVER_KEY; a missing/invalid signature is refused. This only adds real separation when the signing
key lives with the approver (e.g. a CI job or second machine) and the executing side verifies with the same key
while the requester never holds it. With FCCTL_APPROVER_KEY unset nothing changes and single-machine
self-approval remains a speed bump. FCCTL_REQUIRE_SIGNED_APPROVAL=1 (or --require-signed) makes execution reject
unsigned approvals.

Env: FCCTL_KEY (signing), FCCTL_APPROVAL_SECRET, FCCTL_APPROVER_KEY, FCCTL_REQUIRE_SIGNED_APPROVAL, FCCTL_USER (requester identity, default OS user),
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
from typing import Any, Callable
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


class BadApprovalSignature(GuardError):
    default_why = "The approval carries no valid approver signature for this verifier's FCCTL_APPROVER_KEY."
    default_fix = ("Have the approver approve with the shared FCCTL_APPROVER_KEY set, and make sure the executing "
                   "side has the same key; or unset --require-signed / FCCTL_REQUIRE_SIGNED_APPROVAL.")


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
    args: dict[str, Any] = field(default_factory=dict)
    kind: str = "central"  # central|pilot
    server: str | None = None
    created_at: float = 0.0
    decided_by: str | None = None
    result: object = None
    approver_sig: str | None = None
    approved_at: float | None = None


def _args_digest(args: Any) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def approval_signature(key: str, rec: "PendingApproval", approver: str, ts: float) -> str:
    """HMAC-SHA256 over (id, op, target, args digest, approver, timestamp) with the approver key."""
    body = json.dumps([rec.id, rec.op, rec.target, _args_digest(rec.args), approver, ts],
                      separators=(",", ":"), default=str).encode()
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


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
    def __init__(self, home: str | Path | None = None, key: str | None = None, secret: str | None = None,
                 alerter: Any = None, now: Callable[[], float] = time.time, user: str | None = None,
                 approver_key: str | None = None, verifier_key: str | None = None,
                 require_signed: bool | None = None) -> None:
        self.home = Path(home or os.environ.get("FCCTL_HOME") or Path.home() / ".fcctl")
        private_dir(self.home)
        from .keys import get_key
        self.key = (key or get_key(home=self.home) or "").encode()
        if not self.key:
            raise GuardError("FCCTL_KEY is not set", why="the approvals store is HMAC-signed with FCCTL_KEY",
                             fix="run: fcctl init")
        self.secret = secret if secret is not None else os.environ.get("FCCTL_APPROVAL_SECRET", "")
        env = os.environ
        self.approver_key = approver_key if approver_key is not None else env.get("FCCTL_APPROVER_KEY", "")
        self.verifier_key = verifier_key if verifier_key is not None else self.approver_key
        self.require_signed = (require_signed if require_signed is not None
                               else env.get("FCCTL_REQUIRE_SIGNED_APPROVAL", "") in ("1", "true", "yes"))
        self.alerter, self.now = alerter, now
        self.user = user or os.environ.get("FCCTL_USER") or getpass.getuser()
        self.store_path, self.audit_path = self.home / "approvals.json", self.home / "audit.jsonl"

    # ---- signed store ----
    def _sig(self, rec: dict[str, Any]) -> str:
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

    def _save(self, recs: dict[str, PendingApproval]) -> None:
        data = {pid: {"rec": asdict(r), "sig": self._sig(asdict(r))} for pid, r in recs.items()}
        tmp = self.store_path.with_suffix(".tmp")
        with open_private(tmp, "w") as f:
            f.write(json.dumps(data, indent=1, default=str))
        os.replace(tmp, self.store_path)

    # ---- audit (append-only, hash-chained JSONL) ----
    def audit(self, event: str, rec: PendingApproval, actor: str, **extra: Any) -> None:
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
    def _marker(self, pid: str) -> Path:
        return self.home / f"{pid}.executing"

    def markers(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.home.glob("*.executing")):
            try:
                out.append(json.loads(p.read_text()))
            except ValueError:
                pass
        return out

    def remove_marker(self, pid: str) -> None:
        self._marker(pid).unlink(missing_ok=True)

    def finish_approval(self, pid: str, status: str, result: Any = None) -> None:
        """Used by reconcile only: record the proven outcome. Never resets to pending."""
        recs = self._load()
        rec = recs[pid]
        rec.status, rec.result = status, result
        self._save(recs)
        self.audit("reconciled_" + status, rec, "reconcile")

    def _journal(self) -> Any:
        from .journal import Journal
        return Journal(self.home / "journal", now=self.now)

    # ---- API ----
    def request(self, op: str, target: Any, args: dict[str, Any], kind: str = "central",
                server: str | None = None, reason: str = "") -> PendingApproval:
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

    def _check_token(self, token: str) -> None:
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

    def _signature_step(self, rec: PendingApproval, approver: str) -> None:
        """Sign (when an approver key is set) and verify the approval record. Mutates rec only on success."""
        if self.approver_key and self.approver_key in (self.key.decode(), self.secret):
            raise GuardError("FCCTL_APPROVER_KEY equals the requester secret",
                             why="a signing key shared with FCCTL_KEY or FCCTL_APPROVAL_SECRET adds no separation",
                             fix="set FCCTL_APPROVER_KEY to a distinct secret held only by the approver/verifier")
        sig, ts, who = rec.approver_sig, rec.approved_at, rec.decided_by or approver
        if not sig and self.approver_key:
            ts, who = self.now(), approver
            sig = approval_signature(self.approver_key, rec, approver, ts)
        if sig:
            if not self.verifier_key:
                if self.require_signed:
                    raise BadApprovalSignature("cannot verify approval signature: FCCTL_APPROVER_KEY is not set")
            elif not (ts is not None and hmac.compare_digest(
                    approval_signature(self.verifier_key, rec, who, ts), sig)):
                raise BadApprovalSignature(f"approval {rec.id} signature does not verify")
        elif self.require_signed:
            raise BadApprovalSignature(f"approval {rec.id} is unsigned and signed approval is required")
        rec.approver_sig, rec.approved_at = sig, ts

    def approve(self, pid: str, approver: str, token: str,
                client_factory: Callable[[str, str | None], Any] | None = None) -> Any:
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
        try:
            self._signature_step(rec, approver)
        except GuardError as e:
            self.audit("signature_rejected", rec, approver, error=str(e))
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

    def deny(self, pid: str, approver: str) -> PendingApproval:
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
    fn: Any
    kind: str


def _central(name: str, mod: str = "servers") -> Callable[..., Any]:
    """Lazy callable resolving fcctl.<mod>.<name> at approval time (avoids import cycles)."""
    def call(c: Any, *a: Any, **k: Any) -> Any:
        import importlib
        fn = getattr(importlib.import_module(f"fcctl.{mod}"), name)
        return getattr(fn, "_unsafe", fn)(c, *a, **k)
    return call


def _pilot(name: str, mod: str) -> Callable[..., Any]:
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


def load_all() -> None:
    """Import every module holding guarded ops so REGISTRY is complete."""
    from . import apps, backups, objects, servers, sites, snapshots  # noqa: F401


def default_guard() -> Guard:
    global _default
    if _default is None:
        from .alerts import Alerter
        _default = Guard(alerter=Alerter())
    return _default


def set_default_guard(g: Guard | None) -> None:
    global _default
    _default = g


def _recording() -> bool:
    """True under --dry-run request capture: the raw op runs, but DryRunRecorder swallows every non-GET request,
    so nothing is sent and no approval is filed."""
    from .request import DryRunRecorder
    return DryRunRecorder.active()


def guarded(op: str, target: Callable[..., Any], kind: str = "central") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate `fn(client, *args)`. The wrapper files a PendingApproval; `wrapper._unsafe` is the
    raw implementation, used only by Guard.approve. `target(**bound_args)` names the victim."""
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)

        is_async = inspect.iscoroutinefunction(fn)
        if not is_async:  # the async twin never registers: approvals run the sync implementation
            REGISTRY[op] = _Op(fn, kind)

        def _file(client: Any, a: tuple[Any, ...], k: dict[str, Any]) -> PendingApproval:
            b = sig.bind(client, *a, **k)
            b.apply_defaults()
            args = {n: v for n, v in b.arguments.items() if n != next(iter(sig.parameters))}
            args = json.loads(json.dumps(args, default=list))
            return default_guard().request(op, target(**args), args, kind=kind,
                                           server=getattr(client, "server", None))

        if is_async:
            @functools.wraps(fn)
            async def wrapper(client: Any, *a: Any, **k: Any) -> Any:
                if _recording():
                    return await fn(client, *a, **k)
                return _file(client, a, k)
        else:
            @functools.wraps(fn)
            def wrapper(client: Any, *a: Any, **k: Any) -> Any:
                if _recording():
                    return fn(client, *a, **k)
                return _file(client, a, k)
        setattr(wrapper, "_unsafe", fn)
        setattr(wrapper, "guard_op", op)
        return wrapper
    return deco
