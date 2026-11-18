"""app: list / add / remove (bench) and install / uninstall (--site)."""
from __future__ import annotations


def a_list(ctx, args):
    from .. import apps, ops, sites
    from .g_task import items
    p, oc = ctx.pilot(), ctx.op_context("pilot")
    if args.site:
        return items(ops.run_op("site.get", args.site, lambda: sites.site_apps(p, args.site), ctx=oc), "apps")
    return items(ops.run_op("app.list", "apps", lambda: apps.list_apps(p), ctx=oc), "apps")


def a_add(ctx, args):
    from .. import apps, ops
    from .g_task import follow
    p, oc = ctx.pilot(), ctx.op_context("pilot")
    if args.site and args.name and not args.repo:
        # Per-site install with {app} (as the dashboard does). Pilot runs get-and-install-app when the app
        # isn't on the bench yet, which skips the bench get-app validator (e.g. india_compliance).
        out = [_install(ctx, args, s, args.name, None, None) for s in args.site]
        return out[0] if len(out) == 1 else out
    if args.repo:
        res = ops.run_op("app.add_repo", args.repo, lambda: apps.add_repo(p, args.repo, args.branch or "main",
                                                                          args.sites or None), ctx=oc)
    else:
        res = ops.run_op("app.add_marketplace", args.name, lambda: apps.add_marketplace(p, args.name, args.sites or None),
                         ctx=oc)
    return follow(ctx, args, res, "app.add")


def a_remove(ctx, args):
    from .. import apps, ops
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("app.remove", args.name, lambda: apps.remove(p, args.name),
                     ctx=ctx.op_context("pilot", args={"name": args.name}))
    return follow(ctx, args, res, "app.remove")


def _install(ctx, args, site, name, repo, branch):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    tgt = f"{site}:{name or repo}"
    res = ops.run_op("site.install_app", tgt,
                     lambda: sites.install_app(p, site, name, repo, branch),
                     ctx=ctx.op_context("pilot"))
    return follow(ctx, args, res, "site.install_app", site=site)


def a_install(ctx, args):
    return _install(ctx, args, args.site, args.name, args.repo, args.branch)


def a_uninstall(ctx, args):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("site.uninstall_app", f"{args.site}:{args.name}",
                     lambda: sites.uninstall_app(p, args.site, args.name, force=args.force,
                                                 remove_from_bench=args.remove_from_bench),
                     ctx=ctx.op_context("pilot", args={"site": args.site, "app": args.name,
                                                       "force": bool(args.force),
                                                       "remove_from_bench": bool(args.remove_from_bench)}))
    return follow(ctx, args, res, "site.uninstall_app", site=args.site)


def a_check_updates(ctx, args):
    from .. import apps, ops
    from .g_task import follow, wants_wait
    p = ctx.pilot()
    res = ops.run_op("app.check_updates", "apps", lambda: apps.check_updates(p), ctx=ctx.op_context("pilot"))
    done = follow(ctx, args, res, "app.check_updates")
    tid = res.get("task_id") if isinstance(res, dict) else None
    if not tid or not wants_wait(args) or (isinstance(res, dict) and res.get("dry_run")):
        return done
    return apps.check_updates_result(p, tid)


def register(sub, common):
    g = sub.add_parser("app", parents=[common], help="bench / site apps").add_subparsers(dest="app_cmd", required=True)
    x = g.add_parser("list", parents=[common]); x.add_argument("--site"); x.set_defaults(func=a_list)
    x = g.add_parser("add", parents=[common], help="add an app (prefer --site: installs on a site; bench-only adds can fail validation)")
    x.add_argument("name", nargs="?"); x.add_argument("--repo"); x.add_argument("--branch")
    x.add_argument("--sites", nargs="*")
    x.add_argument("--site", action="append", help="fetch and install NAME on this site (repeatable) instead of only adding it to the bench")
    x.set_defaults(func=a_add)
    x = g.add_parser("remove", parents=[common], help="remove an app from the bench (guarded)")
    x.add_argument("name"); x.set_defaults(func=a_remove)
    x = g.add_parser("check-updates", parents=[common], help="git-fetch every bench app and show which have updates")
    x.set_defaults(func=a_check_updates)
    x = g.add_parser("install", parents=[common], help="install an app on a site")
    x.add_argument("name", nargs="?"); x.add_argument("--site", required=True)
    x.add_argument("--repo"); x.add_argument("--branch")
    x.set_defaults(func=a_install)
    x = g.add_parser("uninstall", parents=[common], help="uninstall an app from a site (guarded)")
    x.add_argument("name"); x.add_argument("--site", required=True); x.add_argument("--force", action="store_true")
    x.add_argument("--remove-from-bench", action="store_true"); x.set_defaults(func=a_uninstall)
