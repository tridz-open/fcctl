"""Policy -> guard -> journal -> audit wiring for registry operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import policy as pol

from .errors import ApprovalPending, FcError, PolicyDenied


class ConfirmRequired(FcError):
    exit_code = 2


@dataclass
class OpContext:
    eff: pol.EffectivePolicy
    journal: Any = None
    guard: Any = None
    audit: Any = None
    alerter: Any = None
    profile: str = ""
    actor: str = ""
    agent: bool = False
    dry_run: bool = False
    record_dry_run: bool = False  # run fn under DryRunRecorder to capture would-be requests (real clients only)
    confirm: Any = None  # True, or the target string echoed back
    args: dict[str, Any] = field(default_factory=dict)
    kind: str = "central"  # plane for guard.request
    server: str | None = None
    workflow_id: str | None = None
    team_membership: Callable[[str], None] | None = None
    clients: dict[str, Any] = field(default_factory=dict)  # plane -> client (raw api)


def _audit(ctx: OpContext, op: str, target: Any, outcome: str, **extra: Any) -> Any:
    if ctx.audit is None:
        return None
    return ctx.audit.append(
        actor=ctx.actor, agent=ctx.agent, profile=ctx.profile, login=ctx.eff.login, team=ctx.eff.team,
        op=op, target=str(target), workflow_id=ctx.workflow_id, outcome=outcome, **extra)


def _confirmed(ctx: OpContext, target: Any) -> bool:
    c = ctx.confirm
    return c is True or (isinstance(c, str) and c == str(target))


def execute_decision(decision: Any, label: str, target: Any, fn: Callable[[], Any], ctx: OpContext, *,
                     is_read: bool = False, journal_op: str | None = None) -> Any:
    """Shared engine. `label` is the audit op name; `journal_op` the journal/idempotency op id."""
    dname = type(decision).__name__
    if ctx.dry_run:
        plan = {"dry_run": True, "op": label, "target": str(target), "decision": dname,
                "reason": getattr(decision, "reason", None), "team": ctx.eff.team, "login": ctx.eff.login,
                "policy": ctx.eff.policy}
        if ctx.record_dry_run and not is_read and not isinstance(decision, pol.Deny):
            from .request import DryRunRecorder
            DryRunRecorder.start()
            try:
                fn()
            except Exception:  # only the recorded would-be requests matter
                pass
            finally:
                reqs = DryRunRecorder.stop()
            if reqs:
                plan["request"] = reqs[0]
                plan["requests"] = reqs
        return plan
    if isinstance(decision, pol.Deny):
        _audit(ctx, label, target, "denied", reason=decision.reason)
        raise PolicyDenied(decision.reason)
    if isinstance(decision, pol.NeedsApproval):
        if ctx.guard is None:
            raise PolicyDenied(f"{label} requires approval but no guard is configured")
        rec = ctx.guard.request(label, target, dict(ctx.args), kind=ctx.kind, server=ctx.server)
        _audit(ctx, label, target, "approval_requested", approval_id=rec.id)
        err = ApprovalPending(f"approval {rec.id} pending for {label} on {target}")
        setattr(err, "approval_id", rec.id)  # dynamic attribute read by the CLI renderer
        raise err
    if isinstance(decision, pol.NeedsConfirm) and not _confirmed(ctx, target):
        _audit(ctx, label, target, "confirm_missing")
        raise ConfirmRequired(f"{label} on {target} needs --confirm")
    if is_read:
        return fn()
    _audit(ctx, label, target, "started")
    try:
        if ctx.journal is not None:
            res = ctx.journal.execute(journal_op or label, lambda _e: fn(), args=dict(ctx.args))
        else:
            res = fn()
    except BaseException as e:
        _audit(ctx, label, target, "outcome_unknown" if ctx.journal is not None else "failed",
               error=f"{type(e).__name__}: {e}")
        raise
    task = res.get("task_id") or res.get("handle") or res.get("id") if isinstance(res, dict) else None
    _audit(ctx, label, target, "submitted", task_id=task)
    return res


def run_op(op_id: str, target: Any, fn: Callable[[], Any], *, ctx: OpContext) -> Any:
    decision = pol.check(op_id, ctx.eff)
    meta = pol.REGISTRY.get(op_id) or {}
    return execute_decision(decision, op_id, target, fn, ctx, is_read=meta.get("kind") == "read")
