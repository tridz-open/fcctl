"""fleet: run OP --select EXPR --parallel N / status RUN / resume RUN (journaled, bounded)."""
from __future__ import annotations

from .cmd import add_cmd, add_group

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..journal import render_entry

OPS = {"backup.create": ("backup.create", "backups", "create"),
       "site.migrate": ("site.migrate", "sites", "migrate"),
       "site.clear_cache": ("site.clear_cache", "sites", "clear_cache")}


def _targets(ctx, select: str) -> list[dict]:
    """EXPR: comma list of `server` (all its sites) or `server:site`."""
    from ..errors import UsageError
    from .g_task import items
    out = []
    for part in [s.strip() for s in select.split(",") if s.strip()]:
        srv, _, site = part.partition(":")
        if site:
            out.append({"server": srv, "site": site})
        else:
            names = [s.get("name") for s in items(ctx.pilot(srv).get("/sites"), "sites")]
            out += [{"server": srv, "site": n} for n in names if n]
    if not out:
        raise UsageError("--select matched nothing", fix="use server-a,server-b:site.example.com")
    return out


MAX_PARALLEL = 32


def _parallel(n) -> int:
    from ..errors import UsageError
    n = int(n)
    if n < 1 or n > MAX_PARALLEL:
        raise UsageError(f"--parallel must be 1..{MAX_PARALLEL}, got {n}", fix=f"use --parallel 1..{MAX_PARALLEL}")
    return n


def _save(ctx, lock, rid, mutate):
    with lock:
        e = ctx.journal.get(rid)
        mutate(e.setdefault("fleet", {}))
        e["updated_at"] = ctx.journal.now()
        ctx.journal._write(e)


def _execute(ctx, args, rid: str, resume: bool) -> object:
    from .. import ops
    from ..errors import FcError, exit_code_for
    from ..liveness import TaskHandle, wait
    from .g_task import poll_interval
    e = ctx.journal.get(rid)
    fl = e["fleet"]
    op_id, mod_name, fn_name = OPS[fl["op"]]
    mod = __import__(f"fcctl.{mod_name}", fromlist=["x"])
    fn = getattr(mod, fn_name)
    # build clients up front (ctx caches are not thread safe)
    servers = sorted({i["server"] for i in fl["items"]})
    pilots = {s: ctx.pilot(s) for s in servers}
    ocs = {s: ctx.op_context("pilot", server=s, clients={"central": ctx.central, "pilot": pilots[s]}) for s in servers}
    save_lock, guard = threading.Lock(), threading.Lock()
    server_locks: dict[str, threading.Lock] = {}  # per-server submit lock; audit chain is flock-safe

    def submit_lock(srv: str) -> threading.Lock:
        with guard:
            return server_locks.setdefault(srv, threading.Lock())
    timeout = 3600 if args.timeout is None else args.timeout
    errors: list[BaseException] = []

    def work(idx: int):
        it = fl["items"][idx]
        if it["state"] == "done":
            return
        s, site = it["server"], it["site"]
        tid = it.get("task_id") if it["state"] == "submitted" else None
        try:
            if not tid:
                with submit_lock(s):
                    res = ops.run_op(op_id, site, lambda: fn(pilots[s], site), ctx=ocs[s])
                tid = res.get("task_id") if isinstance(res, dict) else None
                _save(ctx, save_lock, rid, lambda f: f["items"][idx].update(state="submitted", task_id=tid, error=None))
            if tid:
                wait(TaskHandle(server=s, task_id=tid, op=op_id, site=site, started_at=time.monotonic()), pilots[s],
                     timeout=timeout, poll=poll_interval(ctx))
            _save(ctx, save_lock, rid, lambda f: f["items"][idx].update(state="done", error=None))
        except BaseException as exc:  # noqa: BLE001
            code = exit_code_for(exc)
            from ..errors import TaskFailed
            state = "submitted" if tid and not isinstance(exc, TaskFailed) else "failed"
            _save(ctx, save_lock, rid, lambda f: f["items"][idx].update(
                state=state, task_id=tid if state == "submitted" else None,
                error=f"{type(exc).__name__}: {exc}", exit_code=code))  # noqa: F821
            if isinstance(exc, (FcError, Exception)):
                errors.append(exc)
            else:
                raise

    with ThreadPoolExecutor(max_workers=_parallel(getattr(args, "parallel", 4) or 4)) as pool:
        list(pool.map(work, range(len(fl["items"]))))
    e = ctx.journal.get(rid)
    ok = all(i["state"] == "done" for i in e["fleet"]["items"])
    from .. import journal as J
    ctx.journal.transition(rid, J.SUCCEEDED if ok else J.NEEDS_ATTENTION)
    ctx.render(_summary(ctx.journal.get(rid)))
    if ok:
        return 0
    # surface the most specific failure code (policy 4, approval 7, timeout 8, task failed 9, ...)
    codes = [i.get("exit_code") for i in e["fleet"]["items"] if i["state"] != "done" and i.get("exit_code")]
    return codes[0] if codes else 1


def _summary(e: dict) -> dict:
    fl = e.get("fleet", {})
    its = fl.get("items", [])
    return {"run": e["id"], "op": fl.get("op"), "state": e["state"], "total": len(its),
            "done": sum(i["state"] == "done" for i in its),
            "failed": sum(i["state"] == "failed" for i in its),
            "pending": sum(i["state"] in ("pending", "submitted") for i in its), "items": render_entry(its)}


def f_run(ctx, args):
    from .. import journal as J
    from ..errors import UsageError
    if args.op not in OPS:
        raise UsageError(f"fleet op must be one of {', '.join(OPS)}")
    _parallel(args.parallel)
    targets = _targets(ctx, args.select)
    its = [{"server": t["server"], "site": t["site"], "state": "pending", "task_id": None, "error": None} for t in targets]
    if args.dry_run:
        return {"dry_run": True, "op": args.op, "parallel": args.parallel, "items": its}
    rid = "fleet-" + uuid.uuid4().hex[:10]
    ctx.journal.create("fleet_run", {"op": args.op, "select": args.select}, jid=rid)
    for s in (J.DISPATCHING, J.SUBMITTED, J.RUNNING):
        ctx.journal.transition(rid, s, **({"handle": rid} if s == J.SUBMITTED else {}))
    e = ctx.journal.get(rid)
    e["fleet"] = {"op": args.op, "select": args.select, "parallel": args.parallel, "items": its}
    ctx.journal._write(e)
    ctx.info(f"fleet run {rid}")
    return _execute(ctx, args, rid, False)


def _load(ctx, rid):
    from ..errors import NotFound
    try:
        e = ctx.journal.get(rid)
    except (FileNotFoundError, OSError):
        raise NotFound(f"no such fleet run {rid}") from None
    if "fleet" not in e:
        raise NotFound(f"{rid} is not a fleet run")
    return e


def f_status(ctx, args):
    return _summary(_load(ctx, args.run))


def f_resume(ctx, args):
    from .. import journal as J
    e = _load(ctx, args.run)
    if e["state"] == J.SUCCEEDED:
        return _summary(e)
    if e["state"] == J.NEEDS_ATTENTION:
        ctx.journal.transition(args.run, J.RUNNING)
    for i in e["fleet"]["items"]:
        if i["state"] == "failed":
            i["state"] = "pending"
    e = ctx.journal.get(args.run)
    for i in e["fleet"]["items"]:
        if i["state"] == "failed":
            i["state"] = "pending"
    ctx.journal._write(e)
    args.parallel = args.parallel or e["fleet"].get("parallel", 4)
    _parallel(args.parallel)
    return _execute(ctx, args, args.run, True)


def register(sub, common):
    g = add_group(sub, common, "fleet", "bounded multi-server runs",
                  ["fcctl --dry-run fleet run backup.create --select server-a,server-b:demo.example.com",
                   "fcctl fleet status RUN_ID"], "fleet_cmd")
    x = add_cmd(g, common, "run", "run one operation across several servers or sites with bounded parallelism",
                ["fcctl --dry-run fleet run backup.create --select server-a,server-b:demo.example.com --parallel 2"])
    x.add_argument("op", help=" | ".join(OPS))
    x.add_argument("--select", required=True, metavar="EXPR", help="server-a,server-b:site.example.com")
    x.add_argument("--parallel", type=int, default=4); x.set_defaults(func=f_run)
    x = add_cmd(g, common, "status", "show the progress of a fleet run", "fcctl fleet status RUN_ID")
    x.add_argument("run"); x.set_defaults(func=f_status)
    x = add_cmd(g, common, "resume", "resume an interrupted fleet run", "fcctl fleet resume RUN_ID --parallel 2")
    x.add_argument("run")
    x.add_argument("--parallel", type=int, default=0); x.set_defaults(func=f_resume)
