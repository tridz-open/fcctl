"""logs: list / tail [--follow]."""
from __future__ import annotations

from .cmd import add_cmd, add_group

import sys


def l_list(ctx, args):
    from .. import ops
    from .._sync import logs
    from .g_task import items
    p = ctx.pilot()
    return items(ops.run_op("logs.list", "logs", lambda: logs.list_logs(p), ctx=ctx.op_context("pilot")), "logs")


def l_tail(ctx, args):
    from .. import ops
    from .._sync import logs
    p = ctx.pilot()
    oc = ctx.op_context("pilot")
    if not args.follow:
        text = ops.run_op("logs.content", args.filename, lambda: logs.content(p, args.filename, args.lines), ctx=oc)
        if isinstance(text, dict):  # dry-run
            return text
        sys.stdout.write(text if text.endswith("\n") or not text else text + "\n")
        return 0
    gen = ops.run_op("logs.tail", args.filename, lambda: logs.tail(p, args.filename), ctx=oc)
    if isinstance(gen, dict):
        return gen
    try:
        for line in gen:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        return 130
    return 0


def register(sub, common):
    g = add_group(sub, common, "logs", "pilot logs", ["fcctl logs list", "fcctl logs tail frappe.log --lines 100"], "logs_cmd")
    add_cmd(g, common, "list", "list log files", "fcctl logs list").set_defaults(func=l_list)
    x = add_cmd(g, common, "tail", "show the end of a log file (-f to follow)",
                ["fcctl logs tail frappe.log --lines 100", "fcctl logs tail web.error.log -f"])
    x.add_argument("filename")
    x.add_argument("-f", "--follow", action="store_true"); x.add_argument("--lines", type=int)
    x.set_defaults(func=l_tail)
