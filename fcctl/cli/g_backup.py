"""backup: list / create / download / offsite / restore (guarded unless workflow-owned)."""
from __future__ import annotations


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
    oc = ctx.op_context("pilot")
    parts = tuple(args.parts) if args.parts else ("database", "public", "private", "config")

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
    g = sub.add_parser("backup", parents=[common], help="site backups").add_subparsers(dest="backup_cmd", required=True)
    x = g.add_parser("list", parents=[common]); x.add_argument("site"); x.add_argument("--limit", type=int, default=20)
    x.set_defaults(func=b_list)
    x = g.add_parser("create", parents=[common]); x.add_argument("site"); x.set_defaults(func=b_create)
    x = g.add_parser("download", parents=[common], help="download links, or a file with --file-id/--dest")
    x.add_argument("site"); x.add_argument("timestamp"); x.add_argument("--file-id"); x.add_argument("--dest")
    x.set_defaults(func=b_download)
    x = g.add_parser("offsite", parents=[common], help="list offsite backups"); x.add_argument("site")
    x.set_defaults(func=b_offsite)
    x = g.add_parser("restore", parents=[common], help="restore a backup over a site (guarded)")
    x.add_argument("site"); x.add_argument("timestamp"); x.add_argument("--parts", nargs="*")
    x.add_argument("--skip-failing-patches", action="store_true")
    x.add_argument("--workflow-id", help="exempt from approval only if this workflow created the site")
    x.set_defaults(func=b_restore)
