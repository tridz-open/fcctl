"""audit: list / verify / export / remote."""
from __future__ import annotations

import sys

REMOTE_FN = "central.api.activity.team_activity"  # verified against the live API (GET, team+limit)


def list_(ctx, args):
    rows = ctx.audit.list(op=args.op, profile=args.audit_profile, outcome=args.outcome)
    return rows[-args.limit:] if args.limit else rows


def verify(ctx, args):
    ok, bad = ctx.audit.verify()
    if not ok:
        ctx.exit_code = 1
    return {"ok": ok, "first_bad_seq": bad}


def export(ctx, args):
    sys.stdout.write(ctx.audit.export(op=args.op, profile=args.audit_profile, outcome=args.outcome))
    return 0


def remote(ctx, args):
    return ctx.central.call("GET", REMOTE_FN, params={"limit": getattr(args, "limit", None) or 20})


def register(sub, common):
    g = sub.add_parser("audit", help="local hash-chained audit log", parents=[common])
    gs = g.add_subparsers(dest="audit_cmd", required=True)

    def filt(x):
        x.add_argument("--op"); x.add_argument("--audit-profile", metavar="PROFILE")
        x.add_argument("--outcome")
    x = gs.add_parser("list", parents=[common]); filt(x); x.add_argument("--limit", type=int, default=50)
    x.set_defaults(func=list_)
    gs.add_parser("verify", parents=[common]).set_defaults(func=verify)
    x = gs.add_parser("export", parents=[common]); filt(x); x.set_defaults(func=export)
    gs.add_parser("remote", parents=[common], help="team activity from Frappe Cloud").set_defaults(func=remote)
