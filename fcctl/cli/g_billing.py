"""billing: tier/balance/invoices/usage/health (health exits 1 when not ok)."""
from __future__ import annotations

from .cmd import add_cmd, add_group

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
    gs = add_group(sub, common, "billing", "billing and trust tier",
                   ["fcctl billing balance", "fcctl billing usage"], "billing_cmd")
    add_cmd(gs, common, "tier", "show the trust tier (alias: trust-tier)", "fcctl billing tier",
            aliases=["trust-tier"]).set_defaults(func=tier)
    add_cmd(gs, common, "balance", "show the account balance", "fcctl billing balance").set_defaults(func=balance)
    x = add_cmd(gs, common, "invoices", "list invoices, or show one", ["fcctl billing invoices", "fcctl billing invoices INVOICE_NAME"])
    x.add_argument("name", nargs="?", help="invoice name (omit to list)")
    x.set_defaults(func=invoices)
    add_cmd(gs, common, "usage", "forecast, cycle costs, next payment", "fcctl billing usage").set_defaults(func=usage)
    x = add_cmd(gs, common, "health", "[experimental] exit 1 when billing is not healthy (field names not live-verified)",
                "fcctl billing health --min-balance 50")
    x.add_argument("--min-balance", type=float, default=0.0)
    x.set_defaults(func=health)
