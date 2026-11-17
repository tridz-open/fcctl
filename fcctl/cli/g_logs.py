"""logs: list / tail [--follow]."""
from __future__ import annotations

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
    g = sub.add_parser("logs", parents=[common], help="pilot logs").add_subparsers(dest="logs_cmd", required=True)
    g.add_parser("list", parents=[common]).set_defaults(func=l_list)
    x = g.add_parser("tail", parents=[common]); x.add_argument("filename")
    x.add_argument("-f", "--follow", action="store_true"); x.add_argument("--lines", type=int)
    x.set_defaults(func=l_tail)
