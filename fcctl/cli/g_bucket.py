"""bucket: list/create/usage/quota/delete."""
from __future__ import annotations

from ..region import default_region
from .g_server import run_central


def list_(ctx, args):
    from .. import objects
    return run_central(ctx, "bucket.get", "-", lambda: objects.list_buckets(ctx.central))


def create(ctx, args):
    from .. import objects
    return run_central(ctx, "bucket.create", args.name,
                       lambda: objects.create_bucket(ctx.central, args.name, args.region),
                       args={"bucket_name": args.name, "region": args.region})


def usage(ctx, args):
    from .. import objects
    return run_central(ctx, "bucket.usage", args.name, lambda: objects.bucket_usage(ctx.central, args.name))


def quota(ctx, args):
    from .. import objects
    from ..errors import UsageError
    if args.size_gib is None and args.max_objects is None:
        raise UsageError("nothing to set", what="no quota given", why="--size-gib and/or --max-objects required",
                         fix="pass --size-gib N and/or --max-objects N (0 = unlimited)")
    return run_central(ctx, "bucket.set_quota", args.name,
                       lambda: objects.set_quota(ctx.central, args.name, args.size_gib, args.max_objects),
                       args={"name": args.name})


def delete(ctx, args):
    from .. import objects
    return run_central(ctx, "bucket.delete", args.name,
                       lambda: objects.delete_bucket._unsafe(ctx.central, args.name), args={"name": args.name})


def put(ctx, args):
    from .. import objects
    return run_central(ctx, "objects.upload", f"{args.name}/{args.key}",
                       lambda: objects.put_object(ctx.central, args.name, args.key, args.file, args.content_type),
                       args={"name": args.name, "key": args.key})


def get(ctx, args):
    from .. import objects
    return run_central(ctx, "objects.get_url", f"{args.name}/{args.key}",
                       lambda: objects.get_object(ctx.central, args.name, args.key, args.dest),
                       args={"name": args.name, "key": args.key})


def presign(ctx, args):
    from .. import objects
    def go():
        if args.method == "put":
            url = objects.presign_put(ctx.central, args.name, args.key, args.content_type)
        else:
            url = objects.presign_get(ctx.central, args.name, args.key)
        return {"method": args.method.upper(), "url": url}
    return run_central(ctx, "objects.get_url", f"{args.name}/{args.key}", go, args={"name": args.name, "key": args.key})


def register(sub, common):
    g = sub.add_parser("bucket", help="object storage buckets", parents=[common])
    gs = g.add_subparsers(dest="bucket_cmd", required=True)
    gs.add_parser("list", parents=[common]).set_defaults(func=list_)
    x = gs.add_parser("create", parents=[common], help="[experimental] create (secret shown once)")
    x.add_argument("name")
    x.add_argument("--region", default=default_region(), help="default: $FCCTL_REGION or bom")
    x.set_defaults(func=create)
    x = gs.add_parser("usage", parents=[common])
    x.add_argument("name")
    x.set_defaults(func=usage)
    x = gs.add_parser("quota", parents=[common], help="[experimental] set quota (0 = unlimited)")
    x.add_argument("name")
    x.add_argument("--size-gib", type=int)
    x.add_argument("--max-objects", type=int)
    x.set_defaults(func=quota)
    x = gs.add_parser("delete", parents=[common], help="[experimental] delete (guarded)")
    x.add_argument("name")
    x.set_defaults(func=delete)
    x = gs.add_parser("put", parents=[common], help="[experimental] upload a local file to NAME/KEY")
    x.add_argument("name"); x.add_argument("key"); x.add_argument("file")
    x.add_argument("--content-type", default="application/octet-stream")
    x.set_defaults(func=put)
    x = gs.add_parser("get", parents=[common], help="download NAME/KEY to DEST")
    x.add_argument("name"); x.add_argument("key"); x.add_argument("dest")
    x.set_defaults(func=get)
    x = gs.add_parser("presign", parents=[common], help="print a presigned URL")
    x.add_argument("name"); x.add_argument("key")
    x.add_argument("--method", choices=["get", "put"], default="get")
    x.add_argument("--content-type", default="application/octet-stream")
    x.set_defaults(func=presign)
