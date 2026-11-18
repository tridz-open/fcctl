"""server: list/get/create/start/stop/restart/resize/protect/terminate/wait/plans/images/metrics.

Shared helpers (`run_central`, `poll`) live here and are reused by the other central groups."""
from __future__ import annotations

import uuid

from .._sync import _io

from .. import policy as pol
from ..region import default_region

DEFAULT_TIMEOUT = 1800.0  # global default for server/snapshot waits (override: --timeout)


def run_central(ctx, op_id: str, target, fn, args: dict | None = None, force_guard: bool = False):
    """Policy -> guard -> journal -> audit via fcctl.ops. force_guard sends an otherwise-allowed
    write (resize, protect off) through the approval guard for non-read-only profiles."""
    from .. import ops
    octx = ctx.op_context("central", args=dict(args or {}))
    decision = pol.check(op_id, octx.eff)
    if force_guard and isinstance(decision, (pol.Allow, pol.NeedsConfirm)):
        decision = pol.NeedsApproval()
    if force_guard:
        return ops.execute_decision(decision, op_id, target, fn, octx)
    return ops.run_op(op_id, target, fn, ctx=octx)


def poll(ctx, args, fetch, done, label: str, default_timeout: float = DEFAULT_TIMEOUT):
    """Poll fetch() until done(value); progress to stderr; Ctrl-C detaches (remote work continues)."""
    from ..errors import WaitTimeout
    from ..liveness import ActionHandle, Detached
    from .g_task import poll_interval
    timeout = args.timeout if args.timeout is not None else default_timeout  # deadline-bound; never unbounded
    interval = getattr(args, "poll", None)
    interval = poll_interval(ctx) if interval is None else interval
    start = _io.now()
    prog = ctx.progress
    try:
        while True:
            val = fetch()
            elapsed = _io.now() - start
            status = val.get("status") if isinstance(val, dict) else None
            prog({"event": "poll", "status": status, "elapsed": elapsed, "message": label})
            if done(val):
                return val
            if elapsed >= timeout:
                raise WaitTimeout(f"{label}: not ready after {timeout:g}s (still running remotely)")
            _io.sleep(max(0.0, min(interval, timeout - elapsed)))
    except KeyboardInterrupt:
        raise Detached(ActionHandle(action=label, started_at=start)) from None


def _find(ctx, rid):
    from .._sync import servers
    from ..errors import NotFound
    for s in servers.list_servers(ctx.central):
        if rid in (s.get("resource_id"), s.get("name")):
            return s
    raise NotFound(f"server {rid} not found")


def _wait_status(ctx, args, rid, status="Running"):
    return poll(ctx, args, lambda: _find(ctx, rid), lambda s: s.get("status") == status,
                f"server {rid} -> {status}")


def _maybe_wait(ctx, args, res, rid, status):
    if args.wait is not False and not (isinstance(res, dict) and res.get("dry_run")):
        return _wait_status(ctx, args, rid, status)
    return res


def list_(ctx, args):
    from .._sync import servers
    return run_central(ctx, "server.list", "-", lambda: servers.list_servers(ctx.central))


def get(ctx, args):
    from .._sync import servers
    return run_central(ctx, "server.overview", args.id, lambda: servers.overview(ctx.central, args.id))


def create(ctx, args):
    from .._sync import servers
    key = args.request_key or str(uuid.uuid4())
    kw = dict(title=args.title, plan=args.plan, image_id=args.image, region=args.region,
              hostname=args.hostname, snapshot=args.snapshot, request_key=key)
    res = run_central(ctx, "server.create", args.title, lambda: servers.create_server(ctx.central, **kw),
                      args={"title": args.title, "plan": args.plan, "image_id": args.image, "request_key": key})
    rid = res.get("resource_id") if isinstance(res, dict) else None
    return _maybe_wait(ctx, args, res, rid, "Running") if rid else res


def _power(op, fn_name, status, force=False):
    def h(ctx, args):
        from .._sync import servers
        fn = getattr(servers, fn_name)
        res = run_central(ctx, op, args.id, lambda: fn(ctx.central, args.id),
                          args={"resource_id": args.id}, force_guard=force)
        return _maybe_wait(ctx, args, res, args.id, status)
    return h


start = _power("server.start", "start_server", "Running")
restart = _power("server.restart", "restart_server", "Running")
stop = _power("server.stop", "stop_server", "Stopped", force=True)


def resize(ctx, args):
    from .._sync import servers
    res = run_central(ctx, "server.resize", args.id, lambda: servers.resize_server(ctx.central, args.id, args.plan),
                      args={"resource_id": args.id, "plan": args.plan})
    return _maybe_wait(ctx, args, res, args.id, "Running")


def protect(ctx, args):
    from .._sync import servers
    enabled = args.state == "on"
    return run_central(ctx, "server.set_termination_protection", args.id,
                       lambda: servers.set_termination_protection(ctx.central, args.id, enabled),
                       args={"resource_id": args.id, "enabled": enabled}, force_guard=not enabled)


def terminate(ctx, args):
    from .. import servers  # guarded wrapper (has _unsafe)
    return run_central(
        ctx, "server.terminate", args.id,
        lambda: servers.terminate_server._unsafe(ctx.central, args.id, args.take_snapshot),
        args={"resource_id": args.id, "take_snapshot": args.take_snapshot})


def wait(ctx, args):
    return _wait_status(ctx, args, args.id, args.status)


def plans(ctx, args):
    from .. import infra
    return run_central(ctx, "infra.plans_eligible", args.region,
                       lambda: infra.plans_eligible(ctx.central, region=args.region, offering=args.offering,
                                                    image_id=args.image, snapshot=args.snapshot))


def images(ctx, args):
    from .. import infra
    return run_central(ctx, "infra.images", args.region,
                       lambda: infra.images(ctx.central, region=args.region, offering=args.offering))


def metrics(ctx, args):
    from .._sync import monitor
    if args.live:
        return monitor.metrics(ctx.pilot(args.id))
    return run_central(ctx, "monitor.central_metrics", args.id,
                       lambda: monitor.central_metrics(ctx.central, args.id, args.period))


def register(sub, common):
    g = sub.add_parser("server", help="Frappe Cloud servers", parents=[common])
    gs = g.add_subparsers(dest="server_cmd", required=True)

    def add(name, func, help_, id_=True, **kw):
        x = gs.add_parser(name, parents=[common], help=help_)
        if id_:
            x.add_argument("id", help="server resource id")
        x.set_defaults(func=func)
        return x

    def pollable(x):
        x.add_argument("--poll", type=float, default=15.0, help=argparse_suppress())

    add("list", list_, "list servers", id_=False)
    add("get", get, "server overview")
    c = add("create", create, "[experimental] create a server", id_=False)
    c.add_argument("--title", required=True)
    c.add_argument("--plan", required=True)
    c.add_argument("--image", required=True, help="image id")
    c.add_argument("--region", default=default_region(), help="default: $FCCTL_REGION or bom")
    c.add_argument("--hostname")
    c.add_argument("--snapshot", help="clone from snapshot (same region)")
    c.add_argument("--request-key", help="idempotency key (reuse on retry)")
    pollable(c)
    for n, f, h in (("start", start, "[experimental] start"), ("stop", stop, "[experimental] stop (guarded)"), ("restart", restart, "[experimental] restart")):
        pollable(add(n, f, h))
    r = add("resize", resize, "[experimental] resize (cost-changing; needs --confirm under guarded)")
    r.add_argument("--plan", required=True)
    pollable(r)
    p = add("protect", protect, "[experimental] termination protection on|off (off is guarded)")
    p.add_argument("state", choices=["on", "off"])
    t = add("terminate", terminate, "[experimental] terminate (guarded)")
    t.add_argument("--take-snapshot", action="store_true")
    w = add("wait", wait, "wait until a server reaches a status")
    w.add_argument("--status", default="Running")
    pollable(w)
    pl = add("plans", plans, "eligible plans", id_=False)
    pl.add_argument("--region", default=default_region(), help="default: $FCCTL_REGION or bom")
    pl.add_argument("--offering")
    pl.add_argument("--image")
    pl.add_argument("--snapshot")
    im = add("images", images, "server images", id_=False)
    im.add_argument("--region", default=default_region(), help="default: $FCCTL_REGION or bom")
    im.add_argument("--offering", default="pilot")
    m = add("metrics", metrics, "server metrics")
    m.add_argument("--period", default="1h")
    m.add_argument("--live", action="store_true", help="live metrics from Pilot")


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
