"""site: list/get/create/provision/drop/login/maintenance/migrate/clear-cache/config/domain."""
from __future__ import annotations

from .cmd import add_cmd, add_group


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
    if args.dry_run:
        # Never construct a ProvisionRun (it writes the journal). Show the first deterministic write only.
        from .. import sites

        def fn():  # noqa: F811
            return sites.create_site(p, args.site, args.app or [])
        plan = ops.run_op("site.create", args.site, fn, ctx=oc)
        if isinstance(plan, dict) and plan.get("dry_run"):
            plan["note"] = ("provision is a multi-step workflow; only the first deterministic write is shown. "
                            "Later steps (bench apps, restore, hooks, config) depend on responses from earlier ones.")
        return plan
    return ops.run_op("site.create", args.site, fn, ctx=oc)


def s_drop(ctx, args):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("site.drop", args.site, lambda: sites.drop_site(p, args.site, no_backup=args.no_backup),
                     ctx=ctx.op_context("pilot", args={"site": args.site, "no_backup": bool(args.no_backup)}))
    return follow(ctx, args, res, "site.drop", site=args.site)


def s_rename(ctx, args):
    from .. import ops, sites
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("site.rename", args.site,
                     lambda: sites.rename_site(p, args.site, args.new_name, args.keep_old_hostname),
                     ctx=ctx.op_context("pilot"))
    return follow(ctx, args, res, "site.rename", site=args.site)


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
    g = add_group(sub, common, "site", "sites on a pilot server",
                  ["fcctl site list", "fcctl site get demo.example.com"], "site_cmd")
    add_cmd(g, common, "list", "list sites on the server", ["fcctl site list", "fcctl -o table site list"]).set_defaults(func=s_list)
    x = add_cmd(g, common, "get", "show one site", "fcctl site get demo.example.com")
    x.add_argument("site"); x.set_defaults(func=s_get)
    x = add_cmd(g, common, "create", "create a bare site (use `provision` for a full workflow)",
                "fcctl --dry-run site create demo.example.com --app erpnext")
    x.add_argument("site")
    x.add_argument("--app", action="append"); x.set_defaults(func=s_create)
    x = add_cmd(g, common, "provision", "resumable provisioning workflow",
                ["fcctl --dry-run site provision demo.example.com --app erpnext --config developer_mode=1",
                 "fcctl site provision demo.example.com --workflow-id WORKFLOW_ID"])
    x.add_argument("site"); x.add_argument("--app", action="append", help="site app (repeatable)")
    x.add_argument("--bench-app", action="append", help="bench app name or repo URL (repeatable)")
    x.add_argument("--from-backup", metavar="PROFILE:SITE:TIMESTAMP"); x.add_argument("--max-age")
    x.add_argument("--hook", action="append", metavar="NAME@VER"); x.add_argument("--config", action="append", metavar="K=V")
    x.add_argument("--workflow-id", help="resume this workflow"); x.add_argument("--allow-hook-upgrade", action="store_true")
    x.set_defaults(func=s_provision)
    x = add_cmd(g, common, "drop", "drop a site (guarded)", "fcctl --dry-run site drop demo.example.com --no-backup")
    x.add_argument("site")
    x.add_argument("--no-backup", action="store_true"); x.set_defaults(func=s_drop)
    x = add_cmd(g, common, "rename", "rename a site (guarded)",
                "fcctl --dry-run site rename demo.example.com new.example.com --keep-old-hostname")
    x.add_argument("site")
    x.add_argument("new_name", metavar="NEW"); x.add_argument("--keep-old-hostname", action="store_true")
    x.set_defaults(func=s_rename)
    x = add_cmd(g, common, "login", "get a one-time login link for a site", "fcctl site login demo.example.com --hint admin")
    x.add_argument("site"); x.add_argument("--hint")
    x.set_defaults(func=s_login)
    x = add_cmd(g, common, "maintenance", "turn maintenance mode on or off", "fcctl site maintenance demo.example.com on")
    x.add_argument("site"); x.add_argument("state", choices=["on", "off"])
    x.set_defaults(func=s_maintenance)
    x = add_cmd(g, common, "migrate", "run `bench migrate` on a site", "fcctl site migrate demo.example.com")
    x.add_argument("site"); x.set_defaults(func=_action("site.migrate", "migrate"))
    x = add_cmd(g, common, "clear-cache", "clear a site's cache", "fcctl site clear-cache demo.example.com")
    x.add_argument("site")
    x.set_defaults(func=_action("site.clear_cache", "clear_cache"))
    c = add_group(g, common, "config", "read and change site_config.json",
                  ["fcctl site config get demo.example.com", "fcctl site config set demo.example.com developer_mode=1"],
                  "config_cmd")
    x = add_cmd(c, common, "get", "show a site's config", "fcctl site config get demo.example.com")
    x.add_argument("site"); x.set_defaults(func=s_config_get)
    x = add_cmd(c, common, "set", "set config keys (values parsed as JSON when possible)",
                "fcctl site config set demo.example.com developer_mode=1 'limits={\"users\": 5}'")
    x.add_argument("site"); x.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    x.set_defaults(func=s_config_set)
    d = add_group(g, common, "domain", "custom domains of a site",
                  ["fcctl site domain list demo.example.com", "fcctl site domain records demo.example.com shop.example.org"],
                  "domain_cmd")
    x = add_cmd(d, common, "list", "list a site's domains", "fcctl site domain list demo.example.com")
    x.add_argument("site"); x.set_defaults(func=s_domain_list)
    x = add_cmd(d, common, "records", "show the DNS records to set for a domain",
                "fcctl site domain records demo.example.com shop.example.org")
    x.add_argument("site"); x.add_argument("domain")
    x.set_defaults(func=s_domain_records)
