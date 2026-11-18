"""snapshot: list/take/delete/wait."""
from __future__ import annotations

from .g_server import poll, run_central


def _rows(res):
    return res.get("snapshots", []) if isinstance(res, dict) else (res or [])


def list_(ctx, args):
    from .._sync import snapshots
    return run_central(ctx, "snapshot.list", args.server_id or "-",
                       lambda: _rows(snapshots.list_snapshots(ctx.central, args.server_id)))


def take(ctx, args):
    from .._sync import snapshots
    res = run_central(ctx, "snapshot.take", args.server_id,
                      lambda: snapshots.take(ctx.central, args.server_id, args.title),
                      args={"resource_id": args.server_id, "title": args.title})
    if args.wait is not False and isinstance(res, dict) and res.get("name") and not res.get("dry_run"):
        return _wait(ctx, args, args.server_id, res["name"])
    return res


def delete(ctx, args):
    from .. import snapshots  # guarded wrapper (has _unsafe)
    names = list(args.names)
    return run_central(ctx, "snapshot.delete", ",".join(names),
                       lambda: snapshots.delete._unsafe(ctx.central, names), args={"names": names})


def _wait(ctx, args, rid, name):
    from .._sync import snapshots

    def fetch():
        for s in _rows(snapshots.list_snapshots(ctx.central, rid)):
            if s.get("name") == name:
                return s
        return {"name": name, "status": "Missing"}
    return poll(ctx, args, fetch, lambda s: s.get("status") in ("Available", "Ready", "Completed"),
                f"snapshot {name}")


def wait(ctx, args):
    return _wait(ctx, args, args.server_id, args.name)


def register(sub, common):
    g = sub.add_parser("snapshot", help="server snapshots", parents=[common])
    gs = g.add_subparsers(dest="snapshot_cmd", required=True)
    x = gs.add_parser("list", parents=[common])
    x.add_argument("server_id", nargs="?")
    x.set_defaults(func=list_)
    x = gs.add_parser("take", parents=[common], help="[experimental] take a snapshot (paid)")
    x.add_argument("server_id")
    x.add_argument("--title")
    x.add_argument("--poll", type=float, default=20.0)
    x.set_defaults(func=take)
    x = gs.add_parser("delete", parents=[common], help="[experimental] delete snapshots (guarded)")
    x.add_argument("names", nargs="+")
    x.set_defaults(func=delete)
    x = gs.add_parser("wait", parents=[common])
    x.add_argument("server_id")
    x.add_argument("name")
    x.add_argument("--poll", type=float, default=20.0)
    x.set_defaults(func=wait)
