"""approval: list / show / approve / deny / execute / enroll (wraps fcctl.guard).

A speed bump against accidents and agents, not a two-person control: approver names are not authenticated.
"""
from __future__ import annotations

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
    g = sub.add_parser("approval", help="approval step for guarded ops (a speed bump, not a two-person control)", parents=[common])
    gs = g.add_subparsers(dest="approval_cmd", required=True)
    gs.add_parser("list", parents=[common]).set_defaults(func=list_)
    x = gs.add_parser("show", parents=[common]); x.add_argument("id"); x.set_defaults(func=show)
    for name, fn in (("approve", approve), ("execute", execute)):
        x = gs.add_parser(name, parents=[common]); x.add_argument("id")
        x.add_argument("--approver")
        if name == "approve":
            x.add_argument("--execute", action="store_true", help="(default) execute on approval")
        x.set_defaults(func=fn)
    x = gs.add_parser("deny", parents=[common]); x.add_argument("id"); x.add_argument("--approver")
    x.set_defaults(func=deny)
    gs.add_parser("enroll", parents=[common], help="generate an approval secret").set_defaults(func=enroll)
