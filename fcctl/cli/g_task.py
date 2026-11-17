"""task: list / get / wait / attach / output / cancel. Also hosts shared helpers for pilot-task groups."""
from __future__ import annotations

import time


def items(r, *keys):
    """Unwrap a list payload that may be a bare list or {key: [...]}."""
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        for k in keys + ("items", "data"):
            if isinstance(r.get(k), list):
                return r[k]
    return []


def poll_interval(ctx) -> float:
    try:
        return float(ctx.env.get("FCCTL_POLL", 15))
    except (TypeError, ValueError):
        return 15.0


def wants_wait(args) -> bool:
    return args.wait is not False


def wait_kw(ctx, args) -> dict:
    return {"timeout": 3600 if args.timeout is None else args.timeout, "poll": poll_interval(ctx),
            "on_progress": ctx.progress}


def follow(ctx, args, res, op: str, site: str | None = None, server: str | None = None):
    """After a task-starting call: wait for the task (default) or return the submit response."""
    from .. import liveness
    if isinstance(res, dict) and res.get("dry_run"):
        return res
    tid = res.get("task_id") if isinstance(res, dict) else None
    if not tid or not wants_wait(args):
        return res
    pilot = ctx.pilot(server)
    h = liveness.TaskHandle(server=getattr(pilot, "server", "") or (server or ""), task_id=tid, op=op,
                            site=site, started_at=time.monotonic())
    return liveness.wait(h, pilot, **wait_kw(ctx, args))


def t_list(ctx, args):
    from .. import tasks  # noqa: F401
    r = items(ctx.pilot().get("/tasks"), "tasks")
    if args.status:
        r = [t for t in r if t.get("status") == args.status]
    return r


def t_get(ctx, args):
    return ctx.pilot().get(f"/tasks/{args.task_id}")


def t_wait(ctx, args):
    from .. import liveness
    return liveness.attach(ctx.pilot(), args.task_id, **wait_kw(ctx, args))


def t_output(ctx, args):
    from .. import taskops
    return taskops.output(ctx.pilot(), args.task_id)


def t_cancel(ctx, args):
    from .. import ops, taskops
    p = ctx.pilot()
    return ops.run_op("task.cancel", args.task_id, lambda: taskops.cancel_task(p, args.task_id),
                      ctx=ctx.op_context("pilot"))


def register(sub, common):
    g = sub.add_parser("task", parents=[common], help="pilot tasks").add_subparsers(dest="task_cmd", required=True)
    x = g.add_parser("list", parents=[common]); x.add_argument("--status"); x.set_defaults(func=t_list)
    for name, fn, h in (("get", t_get, "show a task"), ("wait", t_wait, "wait for a task (exit 8 on timeout, 9 on failure)"),
                        ("attach", t_wait, "re-attach to a running task (Ctrl-C detaches, exit 130)"),
                        ("output", t_output, "task output"), ("cancel", t_cancel, "cancel a task (guarded)")):
        x = g.add_parser(name, parents=[common], help=h); x.add_argument("task_id"); x.set_defaults(func=fn)
