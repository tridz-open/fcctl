"""profile: list / show / create / use / set / delete."""
from __future__ import annotations

from .cmd import add_cmd, add_group

import dataclasses


def _row(p, current=None):
    d = dataclasses.asdict(p)
    d["allow"] = list(p.allow)
    d["current"] = p.name == current
    return d


def _pins(items):
    """NAME=BASE64_PUBKEY pairs (from `fcctl approval keygen`) -> dict."""
    from ..errors import UsageError
    out = {}
    for it in items or ():
        name, sep, key = it.partition("=")
        if not sep or not name.strip() or not key.strip():
            raise UsageError("bad --approver-pubkey", what=f"{it!r} is not NAME=PUBLIC_KEY",
                             why="a pinned key needs the approver name and the base64 public key",
                             fix="use e.g. --approver-pubkey alice=<key from `fcctl approval keygen`>")
        out[name.strip()] = key.strip()
    return out


def list_(ctx, args):
    cur = ctx.profiles.current()
    return [_row(p, cur) for p in ctx.profiles.list()]


def show(ctx, args):
    from .. import config
    p = ctx.profiles.get(args.name) if args.name else ctx.profile
    row = _row(p, ctx.profiles.current())
    row["effective_policy"] = dataclasses.asdict(config.effective_policy(p, ctx.profiles.list()))
    row["effective_policy"]["allow"] = sorted(row["effective_policy"]["allow"])
    row["effective_policy"]["warnings"] = list(row["effective_policy"]["warnings"])
    row["effective_policy"]["profiles"] = list(row["effective_policy"]["profiles"])
    return row


def create(ctx, args):
    from ..profiles import Profile
    p = Profile(args.name, args.email, args.team, args.policy, tuple(args.allow or ()), args.session_ref,
                approver_pubkeys=_pins(args.approver_pubkey))
    return _row(ctx.profiles.create(p))


def use(ctx, args):
    ctx.profiles.use(args.name)
    return {"current": args.name}


def set_(ctx, args):
    from ..profiles import Profile
    old = ctx.profiles.get(args.name)
    ch = {}
    if args.email:
        ch["login"] = args.email
    for k in ("team", "policy", "session_ref"):
        if getattr(args, k):
            ch[k] = getattr(args, k)
    if args.allow is not None:
        ch["allow"] = tuple(args.allow)
    if args.approver_pubkey:
        ch["approver_pubkeys"] = {**old.approver_pubkeys, **_pins(args.approver_pubkey)}
    return _row(ctx.profiles.set(Profile(**{**dataclasses.asdict(old), **ch})))


def delete(ctx, args):
    from ..errors import UsageError
    if not (args.yes or args.confirm in (True, args.name)):
        raise UsageError("confirmation required", what=f"refusing to delete profile {args.name!r}",
                         why="deleting a profile is irreversible", fix="re-run with --yes")
    ctx.profiles.delete(args.name)
    return {"deleted": args.name}


def register(sub, common):
    gs = add_group(sub, common, "profile", "manage profiles",
                   ["fcctl profile list", "fcctl profile use main"], "profile_cmd")
    add_cmd(gs, common, "list", "list profiles", "fcctl profile list").set_defaults(func=list_)
    x = add_cmd(gs, common, "show", "show a profile (default: the active one)", ["fcctl profile show", "fcctl profile show main"])
    x.add_argument("name", nargs="?"); x.set_defaults(func=show)
    x = add_cmd(gs, common, "create", "create a profile",
                "fcctl profile create main --email you@example.com --team TEAM-xxxx --policy guarded")
    x.add_argument("name"); x.add_argument("--email", required=True); x.add_argument("--team", required=True)
    x.add_argument("--policy", default="guarded", choices=["full", "guarded", "read_only", "protected"])
    x.add_argument("--allow", action="append"); x.add_argument("--session-ref")
    x.add_argument("--approver-pubkey", action="append", metavar="NAME=PUBKEY",
                   help="pin an approver Ed25519 public key; the profile then requires signed approvals (repeatable)")
    x.set_defaults(func=create)
    x = add_cmd(gs, common, "use", "make a profile the active one", "fcctl profile use main")
    x.add_argument("name"); x.set_defaults(func=use)
    x = add_cmd(gs, common, "set", "change fields of a profile", "fcctl profile set main --policy read_only")
    x.add_argument("name"); x.add_argument("--email"); x.add_argument("--team")
    x.add_argument("--policy", choices=["full", "guarded", "read_only", "protected"])
    x.add_argument("--allow", action="append"); x.add_argument("--session-ref")
    x.add_argument("--approver-pubkey", action="append", metavar="NAME=PUBKEY",
                   help="pin an approver Ed25519 public key; the profile then requires signed approvals (repeatable)")
    x.set_defaults(func=set_)
    x = add_cmd(gs, common, "delete", "delete a profile", "fcctl profile delete old-profile")
    x.add_argument("name"); x.set_defaults(func=delete)
