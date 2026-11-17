"""notification: list [--unread] / read."""
from __future__ import annotations

from .g_server import run_central


def list_(ctx, args):
    from .. import notifications
    res = run_central(ctx, "notification.list", "-",
                      lambda: notifications.list_notifications(ctx.central, args.unread, args.start, args.limit))
    return res.get("notifications", res) if isinstance(res, dict) and "notifications" in res else res


def read(ctx, args):
    from .. import notifications
    from ..errors import UsageError
    if args.all:
        return run_central(ctx, "notification.read_all", "*", lambda: notifications.mark_all_read(ctx.central))
    if not args.name:
        raise UsageError("which notification?", what="no notification name", why="name or --all required",
                         fix="fcctl notification read N-1  (or --all)")
    return run_central(ctx, "notification.read", args.name, lambda: notifications.mark_read(ctx.central, args.name),
                       args={"name": args.name})


def register(sub, common):
    g = sub.add_parser("notification", help="team notifications", parents=[common])
    gs = g.add_subparsers(dest="notification_cmd", required=True)
    x = gs.add_parser("list", parents=[common])
    x.add_argument("--unread", action="store_true")
    x.add_argument("--start", type=int, default=0)
    x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=list_)
    x = gs.add_parser("read", parents=[common])
    x.add_argument("name", nargs="?")
    x.add_argument("--all", action="store_true")
    x.set_defaults(func=read)
