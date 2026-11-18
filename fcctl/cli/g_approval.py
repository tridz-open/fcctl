"""approval: list / show / approve / deny / execute / enroll (wraps fcctl.guard).

A speed bump against accidents and agents, not a two-person control: approver names are not authenticated.
Optional hardening: set FCCTL_APPROVER_KEY (distinct from FCCTL_KEY and FCCTL_APPROVAL_SECRET) so approvals are
HMAC-signed and verified at execution; this is real separation only if the requester never holds that key.
"""
from __future__ import annotations

from .cmd import add_cmd, add_group

import dataclasses
import getpass
import sys


def _token(ctx, args):
    """Token from FCCTL_APPROVAL_TOKEN, else an interactive prompt. Never from argv (shell history, ps)."""
    tok = ctx.env.get("FCCTL_APPROVAL_TOKEN", "")
    if not tok and sys.stdin.isatty():
        tok = getpass.getpass("Approval token or authenticator code: ")
    return tok


def list_(ctx, args):
    return [dataclasses.asdict(r) for r in ctx.guard.list_pending()]


def show(ctx, args):
    from ..guard import NotPending
    rec = ctx.guard._load().get(args.id)
    if rec is None:
        raise NotPending(f"no such approval {args.id}")
    return dataclasses.asdict(rec)


def approve(ctx, args):
    g, env = ctx.guard, ctx.env
    if env.get("FCCTL_APPROVER_KEY"):
        g.approver_key = env["FCCTL_APPROVER_KEY"]
        g.verifier_key = g.verifier_key or g.approver_key
    else:
        print("fcctl: note: FCCTL_APPROVER_KEY is not set; single-machine self-approval is a speed bump, "
              "not a two-person control.", file=sys.stderr)
    if getattr(args, "require_signed", False) or env.get("FCCTL_REQUIRE_SIGNED_APPROVAL") in ("1", "true", "yes"):
        g.require_signed = True
    approver = args.approver or ctx.guard.user
    return ctx.guard.approve(args.id, approver, _token(ctx, args), ctx.client_factory())


def deny(ctx, args):
    return dataclasses.asdict(ctx.guard.deny(args.id, args.approver or ctx.guard.user))


def execute(ctx, args):
    """Idempotent: re-running an executed approval returns its stored result."""
    return approve(ctx, args)


def enroll(ctx, args):
    from ..guard import new_secret, provisioning_uri
    secret = new_secret()
    return {"FCCTL_APPROVAL_SECRET": secret, "otpauth_uri": provisioning_uri(secret, ctx.guard.user),
            "note": "base32 secret: add it to an authenticator app (or render otpauth_uri as a QR code) and "
                    "set FCCTL_APPROVAL_SECRET for the process that checks codes; it is shown once"}


def register(sub, common):
    gs = add_group(sub, common, "approval", "approval step for guarded ops (a speed bump unless FCCTL_APPROVER_KEY is set)",
                   ["fcctl approval list", "fcctl approval approve APPROVAL_ID --approver alice"], "approval_cmd",
                   description="Approval step for guarded ops. Self-approval on one machine is a speed bump, not a "
                               "two-person control. Set FCCTL_APPROVER_KEY (a secret distinct from FCCTL_KEY and "
                               "FCCTL_APPROVAL_SECRET, held by the approver/verifier, e.g. CI) to sign approvals; "
                               "execution then verifies the signature. --require-signed rejects unsigned ones.")
    add_cmd(gs, common, "list", "list pending approvals", "fcctl approval list").set_defaults(func=list_)
    x = add_cmd(gs, common, "show", "show one approval request", "fcctl approval show APPROVAL_ID")
    x.add_argument("id"); x.set_defaults(func=show)
    for name, fn, h, ex in (
            ("approve", approve, "approve a pending request (executes it by default)",
             "fcctl approval approve APPROVAL_ID --approver alice --require-signed"),
            ("execute", execute, "execute an approved request", "fcctl approval execute APPROVAL_ID")):
        x = add_cmd(gs, common, name, h, ex); x.add_argument("id")
        x.add_argument("--approver")
        x.add_argument("--require-signed", action="store_true",
                       help="reject approvals lacking a valid FCCTL_APPROVER_KEY signature "
                            "(or set FCCTL_REQUIRE_SIGNED_APPROVAL=1)")
        if name == "approve":
            x.add_argument("--execute", action="store_true", help="(default) execute on approval")
        x.set_defaults(func=fn)
    x = add_cmd(gs, common, "deny", "deny a pending request", "fcctl approval deny APPROVAL_ID --approver alice")
    x.add_argument("id"); x.add_argument("--approver")
    x.set_defaults(func=deny)
    add_cmd(gs, common, "enroll", "generate an approval secret", "fcctl approval enroll").set_defaults(func=enroll)
