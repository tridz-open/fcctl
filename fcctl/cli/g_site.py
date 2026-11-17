"""site: list/get/create/provision/drop/login/maintenance/migrate/clear-cache/config/domain."""
from __future__ import annotations


def _kv(pairs):
    from ..errors import UsageError
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise UsageError(f"bad KEY=VALUE {p!r}")
        k, v = p.split("=", 1)
        try:
            import json
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def s_list(ctx, args):
    from .. import ops, sites
    from .g_task import items
    p = ctx.pilot()
    return items(ops.run_op("site.list", "sites", lambda: sites.list_sites(p), ctx=ctx.op_context("pilot")), "sites")


def s_get(ctx, args):
    from .. import ops, sites
    p = ctx.pilot()
    return ops.run_op("site.get", args.site, lambda: sites.get_site(p, args.site), ctx=ctx.op_context("pilot"))


def s_create(ctx, args):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("site.create", args.site, lambda: sites.create_site(p, args.site, args.app or []),
                     ctx=ctx.op_context("pilot"))
    return follow(ctx, args, res, "site.create", site=args.site)


def _parse_hooks(specs):
    from ..errors import UsageError
    from ..workflows.types import HookRef
    out = []
    for s in specs or []:
        if "@" not in s:
            raise UsageError(f"hook must be name@version, got {s!r}")
        n, v = s.split("@", 1)
        out.append(HookRef(n, int(v) if v.isdigit() else v))
    return out


def s_provision(ctx, args):
    from .. import ops
    from ..errors import UsageError
    from ..workflows import provision_site
    from ..workflows.types import AppSource, BackupSource
    fb = None
    if args.from_backup:
        parts = args.from_backup.split(":")
        if len(parts) != 3:
            raise UsageError("--from-backup must be PROFILE:SITE:TIMESTAMP")
        fb = BackupSource(parts[0], parts[1], parts[2], args.max_age)
    bench = [AppSource(repo=a) if "/" in a else AppSource(name=a) for a in args.bench_app or []]
    hooks = _parse_hooks(args.hook)
    p = ctx.pilot()
    oc = ctx.op_context("pilot")

    from ..workflows.provision import LiveApi
    from .g_task import poll_interval, wants_wait
    to = 0.0 if not wants_wait(args) else (3600.0 if args.timeout is None else args.timeout)
    api = LiveApi(p, ctx.central, timeout=to, poll=poll_interval(ctx))

    def fn():
        run = provision_site(api=api, pilot=p, journal=ctx.journal, site=args.site, server=args.server,
                             central=ctx.central, bench_apps=bench, site_apps=args.app or [], from_backup=fb,
                             post_restore_hooks=hooks, config=_kv(args.config), workflow_id=args.workflow_id,
                             eff=ctx.eff, allow_hook_upgrade=args.allow_hook_upgrade)
        oc.workflow_id = run.workflow_id
        out = run.wait()
        return out if isinstance(out, dict) else {"result": out}
    if args.workflow_id and not args.dry_run:  # resume: the journal already holds the recipe; do not create a new write op
        res = fn()
        return {"workflow_id": args.workflow_id, **res} if isinstance(res, dict) else res
    return ops.run_op("site.create", args.site, fn, ctx=oc)


def s_drop(ctx, args):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("site.drop", args.site, lambda: sites.drop_site(p, args.site, no_backup=args.no_backup),
                     ctx=ctx.op_context("pilot"))
    return follow(ctx, args, res, "site.drop", site=args.site)


def s_login(ctx, args):
    from .. import ops, sites
    p = ctx.pilot()
    r = ops.run_op("site.login_link", args.site, lambda: {"url": sites.login_link(p, args.site, args.hint)},
                   ctx=ctx.op_context("pilot"))
    return r


def s_maintenance(ctx, args):
    from .. import ops, sites
    p = ctx.pilot()
    return ops.run_op("site.set_maintenance", args.site,
                      lambda: sites.set_maintenance(p, args.site, args.state == "on"), ctx=ctx.op_context("pilot"))


def _action(op_id, fn_name):
    def h(ctx, args):
        from .. import ops, sites
        from .g_task import follow
        p = ctx.pilot()
        res = ops.run_op(op_id, args.site, lambda: getattr(sites, fn_name)(p, args.site), ctx=ctx.op_context("pilot"))
        return follow(ctx, args, res, op_id, site=args.site)
    return h


def s_config_get(ctx, args):
    from .. import ops, sites
    p = ctx.pilot()
    return ops.run_op("site.get", args.site, lambda: sites.config_get(p, args.site), ctx=ctx.op_context("pilot"))


def s_config_set(ctx, args):
    from .. import ops, policy, sites
    p = ctx.pilot()
    values = _kv(args.pairs)
    op_id = "site.config_set" if "site.config_set" in policy.REGISTRY else "site.set_maintenance"
    return ops.run_op(op_id, args.site, lambda: sites.config_set(p, args.site, **values), ctx=ctx.op_context("pilot"))


def s_domain_list(ctx, args):
    from .. import domains
    from .g_task import items
    return items(domains.list_domains(ctx.pilot(), args.site), "domains")


def s_domain_records(ctx, args):
    from .. import domains
    return domains.dns_records(ctx.pilot(), args.site, args.domain)


def register(sub, common):
    g = sub.add_parser("site", parents=[common], help="sites on a pilot server").add_subparsers(
        dest="site_cmd", required=True)
    g.add_parser("list", parents=[common]).set_defaults(func=s_list)
    x = g.add_parser("get", parents=[common]); x.add_argument("site"); x.set_defaults(func=s_get)
    x = g.add_parser("create", parents=[common]); x.add_argument("site")
    x.add_argument("--app", action="append"); x.set_defaults(func=s_create)
    x = g.add_parser("provision", parents=[common], help="resumable provisioning workflow")
    x.add_argument("site"); x.add_argument("--app", action="append", help="site app (repeatable)")
    x.add_argument("--bench-app", action="append", help="bench app name or repo URL (repeatable)")
    x.add_argument("--from-backup", metavar="PROFILE:SITE:TIMESTAMP"); x.add_argument("--max-age")
    x.add_argument("--hook", action="append", metavar="NAME@VER"); x.add_argument("--config", action="append", metavar="K=V")
    x.add_argument("--workflow-id", help="resume this workflow"); x.add_argument("--allow-hook-upgrade", action="store_true")
    x.set_defaults(func=s_provision)
    x = g.add_parser("drop", parents=[common], help="drop a site (guarded)"); x.add_argument("site")
    x.add_argument("--no-backup", action="store_true"); x.set_defaults(func=s_drop)
    x = g.add_parser("login", parents=[common]); x.add_argument("site"); x.add_argument("--hint")
    x.set_defaults(func=s_login)
    x = g.add_parser("maintenance", parents=[common]); x.add_argument("site"); x.add_argument("state", choices=["on", "off"])
    x.set_defaults(func=s_maintenance)
    x = g.add_parser("migrate", parents=[common]); x.add_argument("site"); x.set_defaults(func=_action("site.migrate", "migrate"))
    x = g.add_parser("clear-cache", parents=[common]); x.add_argument("site")
    x.set_defaults(func=_action("site.clear_cache", "clear_cache"))
    c = g.add_parser("config", parents=[common]).add_subparsers(dest="config_cmd", required=True)
    x = c.add_parser("get", parents=[common]); x.add_argument("site"); x.set_defaults(func=s_config_get)
    x = c.add_parser("set", parents=[common]); x.add_argument("site"); x.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    x.set_defaults(func=s_config_set)
    d = g.add_parser("domain", parents=[common]).add_subparsers(dest="domain_cmd", required=True)
    x = d.add_parser("list", parents=[common]); x.add_argument("site"); x.set_defaults(func=s_domain_list)
    x = d.add_parser("records", parents=[common]); x.add_argument("site"); x.add_argument("domain")
    x.set_defaults(func=s_domain_records)
