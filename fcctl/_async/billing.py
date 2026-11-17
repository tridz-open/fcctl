"""Billing reads."""
from __future__ import annotations

from .central import CentralClient


async def eligible_plans(c: CentralClient, **kw):
    return await c.call("POST", "central.billing.api.dashboard.catalog.get_eligible_plans", json=kw)


async def trust_tier(c: CentralClient):
    return await c.call("GET", "central.billing.api.dashboard.get_trust_tier")  # bru: GET, team query


# ---- v1 reads (dotted prefix central.billing.api.dashboard.)
D = "central.billing.api.dashboard."

OPS = {"trust_tier": "billing.trust_tier", "eligible_plans": "billing.eligible_plans",
       "wallet_balance": "billing.wallet_balance", "ledger": "billing.ledger",
       "invoices_list": "billing.invoices_list", "invoice_get": "billing.invoice_get",
       "overview": "billing.overview", "subscriptions": "billing.subscriptions",
       "billing_health": "billing.health"}


async def wallet_balance(c: CentralClient):
    return await c.call("GET", D + "get_credit_balance")


async def ledger(c: CentralClient):
    return await c.call("GET", D + "credit_ledger")


async def invoices_list(c: CentralClient):
    return await c.call("GET", D + "list_invoices")


async def invoice_get(c: CentralClient, name: str):
    return await c.call("POST", D + "get_invoice", json={"name": name})


async def subscriptions(c: CentralClient):
    return await c.call("GET", D + "list_subscriptions")


async def overview(c: CentralClient) -> dict:
    """Team overview, forecast, cycle costs, next payment and collection status, merged: {team, forecast, cycle_costs, next_payment, collection_status}."""
    return {"team": await c.call("GET", D + "get_team_overview"),
            "forecast": await c.call("GET", D + "get_forecast"),
            "cycle_costs": await c.call("GET", D + "get_cycle_costs"),
            "next_payment": await c.call("GET", D + "get_next_payment"),
            "collection_status": await c.call("GET", D + "get_collection_status")}


async def billing_health(c: CentralClient, min_balance: float = 0.0) -> dict:
    """Computed from the trust tier, credit balance, team overview and collection status. Returns {tier, headroom: {spend, resources}, balance, standing,
    collection_mode, action_required, overdue, ok, problems}."""
    tt = (await trust_tier(c)) or {}
    cur = tt.get("current") or {}
    prog = tt.get("progress") or {}
    limit = tt.get("spending_limit", cur.get("max_spend"))
    running = tt.get("running_monthly") or 0
    max_res = cur.get("max_resource_count")
    headroom = {"spend": None if limit is None else limit - running,
                "resources": None if max_res is None else max_res - (prog.get("resources_used") or 0)}
    bal = ((await wallet_balance(c)) or {}).get("balance")
    team = (await c.call("GET", D + "get_team_overview")) or {}
    col = (await c.call("GET", D + "get_collection_status")) or {}
    standing = team.get("standing")
    overdue = bool(standing and standing != "Current")
    problems = []
    if headroom["spend"] is not None and headroom["spend"] <= 0:
        problems.append("trust-tier spend limit exhausted")
    if headroom["resources"] is not None and headroom["resources"] <= 0:
        problems.append("trust-tier resource limit reached")
    if bal is not None and bal < min_balance:
        problems.append(f"wallet balance {bal} below {min_balance}")
    if overdue:
        problems.append(f"account standing {standing!r}")
    if col.get("action_required"):
        problems.append(f"collection action required: {col.get('reason')}")
    if (col.get("shortfall") or 0) > 0:
        problems.append(f"wallet shortfall {col['shortfall']}")
    return {"tier": cur.get("tier"), "headroom": headroom, "balance": bal, "standing": standing,
            "collection_mode": col.get("collection_mode"), "action_required": bool(col.get("action_required")),
            "overdue": overdue, "ok": not problems, "problems": problems}
