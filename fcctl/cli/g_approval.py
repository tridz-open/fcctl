"""approval: list / show / approve / deny / execute / enroll / keygen / sign (wraps fcctl.guard).

A speed bump against accidents and agents, not a two-person control, unless the profile pins an approver Ed25519
public key (`approver_pubkeys`): approvals must then carry a valid signature from the approver's private key
(FCCTL_APPROVER_PRIVATE_KEY[_FILE]), which the executing side never needs to hold.
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


def _display(rec):
    """Redacted copy for display; the stored record stays raw (replay needs the real args)."""
    from ..redact import redact
    d = dataclasses.asdict(rec)
    d["args"] = redact(d.get("args"))
    return d


def list_(ctx, args):
    return [_display(r) for r in ctx.guard.list_pending()]


def show(ctx, args):
    from ..guard import NotPending
    rec = ctx.guard._load().get(args.id)
    if rec is None:
        raise NotPending(f"no such approval {args.id}")
    return _display(rec)


def approve(ctx, args):
    g, env = ctx.guard, ctx.env
    if getattr(args, "require_signed", False) or env.get("FCCTL_REQUIRE_SIGNED_APPROVAL") in ("1", "true", "yes"):
        g.require_signed = True
    if not g.approver_pubkeys.get(g.profile):
        print("fcctl: note: no approver_pubkeys pinned for this profile; single-machine self-approval is a speed "
              "bump, not a two-person control.", file=sys.stderr)
    approver = args.approver or ctx.guard.user
    sig = getattr(args, "signature", None) or env.get("FCCTL_APPROVAL_SIGNATURE") or None
    return ctx.guard.approve(args.id, approver, _token(ctx, args), ctx.client_factory(), signature=sig)


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


def keygen(ctx, args):
    from ..guard import generate_keypair
    priv, pub = generate_keypair()
    return {"FCCTL_APPROVER_PRIVATE_KEY": priv, "public_key": pub,
            "note": "keep the private key with the approver only (env FCCTL_APPROVER_PRIVATE_KEY, or a 0600 file "
                    f"named by FCCTL_APPROVER_PRIVATE_KEY_FILE); it is shown once. Pin the public key: "
                    f"fcctl profile set PROFILE --approver-pubkey NAME={pub}"}


def sign(ctx, args):
    """Sign a pending approval with the approver's private key; pass the result to `approve --signature`."""
    from ..guard import GuardError, NotPending, load_private_key, sign_approval
    rec = ctx.guard._load().get(args.id)
    if rec is None:
        raise NotPending(f"no such approval {args.id}")
    priv = load_private_key(ctx.env)
    if not priv:
        raise GuardError("no approver private key", why="signing needs FCCTL_APPROVER_PRIVATE_KEY[_FILE]",
                         fix="run `fcctl approval keygen` and export the private key on the approver's machine")
    approver = args.approver or ctx.guard.user
    return {"id": rec.id, "approver": approver, "signature": sign_approval(priv, rec, approver)}


def register(sub, common):
    gs = add_group(sub, common, "approval", "approval step for guarded ops (a speed bump unless the profile pins an approver key)",
                   ["fcctl approval list", "fcctl approval approve APPROVAL_ID --approver alice"], "approval_cmd",
                   description="Approval step for guarded ops. Self-approval on one machine is a speed bump, not a "
                               "two-person control. Pin an approver Ed25519 public key on the profile "
                               "(`fcctl profile set P --approver-pubkey NAME=KEY`, key from `approval keygen`) and "
                               "approvals must be signed by that approver's private key; the profile then requires "
                               "signatures automatically. --require-signed also rejects unsigned ones elsewhere.")
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
                       help="reject approvals lacking a valid approver signature "
                            "(or set FCCTL_REQUIRE_SIGNED_APPROVAL=1); automatic when the profile pins keys")
        x.add_argument("--signature", help="base64 signature from `fcctl approval sign` "
                                           "(or env FCCTL_APPROVAL_SIGNATURE)")
        if name == "approve":
            x.add_argument("--execute", action="store_true", help="(default) execute on approval")
        x.set_defaults(func=fn)
    x = add_cmd(gs, common, "deny", "deny a pending request", "fcctl approval deny APPROVAL_ID --approver alice")
    x.add_argument("id"); x.add_argument("--approver")
    x.set_defaults(func=deny)
    add_cmd(gs, common, "enroll", "generate an approval secret", "fcctl approval enroll").set_defaults(func=enroll)
    add_cmd(gs, common, "keygen", "generate an approver Ed25519 key pair", "fcctl approval keygen").set_defaults(func=keygen)
    x = add_cmd(gs, common, "sign", "sign a pending approval with the approver private key",
                "fcctl approval sign APPROVAL_ID --approver alice")
    x.add_argument("id"); x.add_argument("--approver")
    x.set_defaults(func=sign)
