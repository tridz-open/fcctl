"""backup: list / create / download / offsite / restore (guarded unless workflow-owned)."""
from __future__ import annotations

from .cmd import add_cmd, add_group


def b_list(ctx, args):
    from .. import backups, ops
    from .g_task import items
    p = ctx.pilot()
    return items(ops.run_op("backup.list", args.site, lambda: backups.list_backups(p, args.site, args.limit),
                            ctx=ctx.op_context("pilot")), "backups")


def b_create(ctx, args):
    from .. import backups, ops
    from .g_task import follow
    p = ctx.pilot()
    res = ops.run_op("backup.create", args.site, lambda: backups.create(p, args.site), ctx=ctx.op_context("pilot"))
    return follow(ctx, args, res, "backup.create", site=args.site)


def b_download(ctx, args):
    from .. import backups, ops
    p, oc = ctx.pilot(), ctx.op_context("pilot")
    if args.file_id:
        if not args.dest:
            from ..errors import UsageError
            raise UsageError("--dest is required with --file-id")
        return ops.run_op("backup.download_file", args.site,
                          lambda: {"saved": backups.download_file(p, args.site, args.timestamp, args.file_id, args.dest)},
                          ctx=oc)
    return ops.run_op("backup.download_links", args.site,
                      lambda: backups.download_links(p, args.site, args.timestamp), ctx=oc)


def b_offsite(ctx, args):
    from .. import backups, ops
    from .g_task import items
    p = ctx.pilot()
    return items(ops.run_op("backup.list", args.site, lambda: backups.offsite_list(p, args.site),
                            ctx=ctx.op_context("pilot")), "backups")


def b_restore(ctx, args):
    from .. import backups, ops, policy
    from ..workflows import is_workflow_owned
    from .g_task import follow
    p = ctx.pilot()
    parts = tuple(args.parts) if args.parts else ("database", "public", "private", "config")
    oc = ctx.op_context("pilot", args={"site": args.site, "parts": list(parts), "backup_timestamp": args.timestamp,
                                       "skip_failing_patches": bool(args.skip_failing_patches)})

    def fn():
        return backups.restore._unsafe(p, args.site, parts=parts, backup_timestamp=args.timestamp,
                                       skip_failing_patches=args.skip_failing_patches)

    owned = bool(args.workflow_id) and is_workflow_owned(ctx.journal, args.workflow_id, args.site, ctx.journal.now(),
                                                         oc.eff.policy)
    if owned:
        oc.workflow_id = args.workflow_id
        res = ops.execute_decision(policy.Allow(), "backup.restore", args.site, fn, oc)
    else:
        # guarded path: the guard itself files/consumes approval; keep the pilot-level guard in the loop
        res = ops.run_op("backup.restore", args.site,
                         lambda: backups.restore(p, args.site, parts=parts, backup_timestamp=args.timestamp,
                                                 skip_failing_patches=args.skip_failing_patches), ctx=oc)
    return follow(ctx, args, res, "backup.restore", site=args.site)


def register(sub, common):
    g = add_group(sub, common, "backup", "site backups",
                  ["fcctl backup list demo.example.com", "fcctl backup create demo.example.com"], "backup_cmd")
    x = add_cmd(g, common, "list", "list backups of a site", "fcctl backup list demo.example.com --limit 5")
    x.add_argument("site"); x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=b_list)
    x = add_cmd(g, common, "create", "take a backup of a site now", "fcctl backup create demo.example.com")
    x.add_argument("site"); x.set_defaults(func=b_create)
    x = add_cmd(g, common, "download", "download links, or a file with --file-id/--dest",
                ["fcctl backup download demo.example.com 20260101_000000",
                 "fcctl backup download demo.example.com 20260101_000000 --file-id FILE_ID --dest ./backup.sql.gz"])
    x.add_argument("site"); x.add_argument("timestamp"); x.add_argument("--file-id"); x.add_argument("--dest")
    x.set_defaults(func=b_download)
    x = add_cmd(g, common, "offsite", "list offsite backups", "fcctl backup offsite demo.example.com")
    x.add_argument("site")
    x.set_defaults(func=b_offsite)
    x = add_cmd(g, common, "restore", "[experimental] full restore of a backup over a site (guarded; not live-tested)",
                "fcctl --dry-run backup restore demo.example.com 20260101_000000")
    x.add_argument("site"); x.add_argument("timestamp"); x.add_argument("--parts", nargs="*")
    x.add_argument("--skip-failing-patches", action="store_true")
    x.add_argument("--workflow-id", help="exempt from approval only if this workflow created the site")
    x.set_defaults(func=b_restore)
