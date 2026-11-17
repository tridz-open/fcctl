"""auth: login / logout / status / whoami / keepalive."""
from __future__ import annotations

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
    g = sub.add_parser("auth", help="sign in and inspect the session", parents=[common])
    gs = g.add_subparsers(dest="auth_cmd", required=True)
    x = gs.add_parser("login", parents=[common], help="email-code login")
    x.add_argument("--email")
    x.add_argument("--code-stdin", action="store_true", help="read the code from stdin")
    x.add_argument("--code-source", choices=["manual", "imap"], default="manual")
    x.set_defaults(func=login)
    gs.add_parser("logout", parents=[common], help="end the server session and clear the local one").set_defaults(func=logout)
    gs.add_parser("status", parents=[common]).set_defaults(func=status)
    w = gs.add_parser("whoami", parents=[common])
    w.add_argument("--caps", action="store_true", help="include capabilities")
    w.set_defaults(func=whoami)
    gs.add_parser("keepalive", parents=[common]).set_defaults(func=keepalive)
    sub.add_parser("keepalive", parents=[common], help="one cheap Central GET to slide the session").set_defaults(func=keepalive)
