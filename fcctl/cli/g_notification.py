"""notification: list [--unread] / read."""
from __future__ import annotations

from .cmd import add_cmd, add_group

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
    gs = add_group(sub, common, "notification", "team notifications",
                   ["fcctl notification list --unread", "fcctl notification read --all"], "notification_cmd")
    x = add_cmd(gs, common, "list", "list notifications", ["fcctl notification list", "fcctl notification list --unread --limit 5"])
    x.add_argument("--unread", action="store_true")
    x.add_argument("--start", type=int, default=0)
    x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=list_)
    x = add_cmd(gs, common, "read", "mark a notification (or all) as read",
                ["fcctl notification read NOTIFICATION_NAME", "fcctl notification read --all"])
    x.add_argument("name", nargs="?")
    x.add_argument("--all", action="store_true")
    x.set_defaults(func=read)
