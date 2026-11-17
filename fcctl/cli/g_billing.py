"""billing: tier/balance/invoices/usage/health (health exits 1 when not ok)."""
from __future__ import annotations

from .g_server import run_central


def tier(ctx, args):
    from .._sync import billing
    return run_central(ctx, "billing.trust_tier", "-", lambda: billing.trust_tier(ctx.central))


def balance(ctx, args):
    from .._sync import billing
    return run_central(ctx, "billing.wallet_balance", "-", lambda: billing.wallet_balance(ctx.central))


def invoices(ctx, args):
    from .._sync import billing
    if args.name:
        return run_central(ctx, "billing.invoice_get", args.name, lambda: billing.invoice_get(ctx.central, args.name))
    return run_central(ctx, "billing.invoices_list", "-", lambda: billing.invoices_list(ctx.central))


def usage(ctx, args):
    from .._sync import billing
    return run_central(ctx, "billing.overview", "-", lambda: billing.overview(ctx.central))


def health(ctx, args):
    from .._sync import billing
    res = run_central(ctx, "billing.health", "-", lambda: billing.billing_health(ctx.central, args.min_balance))
    if isinstance(res, dict) and res.get("ok") is False:
        ctx.exit_code = 1
    return res


def register(sub, common):
    g = sub.add_parser("billing", help="billing and trust tier", parents=[common])
    gs = g.add_subparsers(dest="billing_cmd", required=True)
    gs.add_parser("tier", aliases=["trust-tier"], parents=[common]).set_defaults(func=tier)
    gs.add_parser("balance", parents=[common]).set_defaults(func=balance)
    x = gs.add_parser("invoices", parents=[common])
    x.add_argument("name", nargs="?", help="invoice name (omit to list)")
    x.set_defaults(func=invoices)
    gs.add_parser("usage", parents=[common], help="forecast, cycle costs, next payment").set_defaults(func=usage)
    x = gs.add_parser("health", parents=[common], help="exit 1 when billing is not healthy")
    x.add_argument("--min-balance", type=float, default=0.0)
    x.set_defaults(func=health)
