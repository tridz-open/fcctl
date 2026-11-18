"""audit: list / verify / export / remote."""
from __future__ import annotations

from .cmd import add_cmd, add_group

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
    gs = add_group(sub, common, "audit", "local hash-chained audit log",
                   ["fcctl audit list --limit 20", "fcctl audit verify"], "audit_cmd")

    def filt(x):
        x.add_argument("--op"); x.add_argument("--audit-profile", metavar="PROFILE")
        x.add_argument("--outcome")
    x = add_cmd(gs, common, "list", "list local audit entries",
                ["fcctl audit list --limit 20", "fcctl audit list --op site.drop --outcome ok"])
    filt(x); x.add_argument("--limit", type=int, default=50)
    x.set_defaults(func=list_)
    add_cmd(gs, common, "verify", "verify the audit hash chain", "fcctl audit verify").set_defaults(func=verify)
    x = add_cmd(gs, common, "export", "export audit entries as JSON lines",
                "fcctl audit export --audit-profile main")
    filt(x); x.set_defaults(func=export)
    add_cmd(gs, common, "remote", "team activity from Frappe Cloud", "fcctl audit remote").set_defaults(func=remote)
