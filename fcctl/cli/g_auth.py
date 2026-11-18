"""auth: login / logout / status / whoami / keepalive."""
from __future__ import annotations

from .cmd import add_cmd, add_group

import sys

WHOAMI_FN = "central.api.identity.my_profile"
# verified against the live API during development; method name is literally "." on identity
# (GET, team param, returns list of "scope:action" strings). Deployed list has no my_capabilities-style alias in use.
CAPS_FN = "central.api.identity.."


def _code_provider(ctx, args):
    from .. import auth
    if args.code_source == "imap":
        e = ctx.env
        return auth.ImapCodePoller(e["FCCTL_IMAP_HOST"], e["FCCTL_IMAP_USER"], e["FCCTL_IMAP_PASS"])
    if args.code_stdin:
        return lambda: sys.stdin.readline().strip()

    def manual():
        print("6-digit Frappe Cloud code: ", end="", file=sys.stderr, flush=True)
        return sys.stdin.readline().strip()
    return manual


def login(ctx, args):
    from .. import auth, config
    from ..errors import UsageError
    p = ctx.profile_or_none
    email = args.email or (p.login if p else None) or ctx.env.get("FCCTL_EMAIL")
    if not email:
        raise UsageError("no email", what="no login email known", why="no profile selected and --email not given",
                         fix="pass --email or select a profile")
    if p:
        config.check_owner(p, ctx.env)
    auth.login_with_code(ctx.central, email, _code_provider(ctx, args))
    return {"logged_in": True, "email": email, "profile": p.name if p else None}


def logout(ctx, args):
    s = ctx.store
    remote = False
    try:
        remote = bool(ctx.central.logout_remote())
    except Exception:  # noqa: BLE001 - best effort; always clear locally
        pass
    if s.sid and not remote:
        print("warning: server-side logout failed or was skipped; local session cleared anyway "
              "(the server session may stay valid until it expires)", file=sys.stderr)
    for k in ("sid", "csrf", "sid_updated", "pilots"):
        s.data.pop(k, None)
    s.save()
    return {"logged_out": True, "server_logout": remote}


def status(ctx, args):
    s = ctx.store
    p = ctx.profile_or_none
    return {"profile": p.name if p else None, "team": p.team if p else None, "login": p.login if p else None,
            "policy": p.policy if p else None, "authenticated": bool(s.sid),
            "pilot_servers": sorted((s.data.get("pilots") or {}).keys())}


def whoami(ctx, args):
    from .. import auth
    out = dict(auth.my_profile(ctx.central) or {})
    if args.caps:
        out["capabilities"] = ctx.central.call("GET", CAPS_FN)
    return out


def keepalive(ctx, args):
    from .. import auth
    return auth.keepalive(ctx.central)


def register(sub, common):
    gs = add_group(sub, common, "auth", "sign in and inspect the session",
                   ["fcctl auth login --email you@example.com", "fcctl auth whoami --caps"], "auth_cmd")
    x = add_cmd(gs, common, "login", "email-code login",
                ["fcctl auth login --email you@example.com", "fcctl auth login --email you@example.com --code-stdin"])
    x.add_argument("--email")
    x.add_argument("--code-stdin", action="store_true", help="read the code from stdin")
    x.add_argument("--code-source", choices=["manual", "imap"], default="manual")
    x.set_defaults(func=login)
    add_cmd(gs, common, "logout", "end the server session and clear the local one",
            "fcctl auth logout").set_defaults(func=logout)
    add_cmd(gs, common, "status", "show the local session state", "fcctl auth status").set_defaults(func=status)
    w = add_cmd(gs, common, "whoami", "show the signed-in user (and capabilities with --caps)",
                "fcctl auth whoami --caps")
    w.add_argument("--caps", action="store_true", help="include capabilities")
    w.set_defaults(func=whoami)
    add_cmd(gs, common, "keepalive", "one cheap Central GET to slide the session",
            "fcctl auth keepalive").set_defaults(func=keepalive)
    add_cmd(sub, common, "keepalive", "one cheap Central GET to slide the session",
            "fcctl keepalive").set_defaults(func=keepalive)
