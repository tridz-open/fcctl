import json

import httpx
import pytest
import respx

from conftest import BASE
from fcctl import activity, billing, infra, notifications, objects, servers

M = BASE + "/api/v2/method/"


def route(router, method, fn, data=None):
    return getattr(router, method)(M + fn).mock(return_value=httpx.Response(200, json={"data": data}))


@pytest.fixture
def r():
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_notifications(r, central):
    lst = route(r, "get", "central.notification.api.list_notifications", [])
    notifications.list_notifications(central, unread_only=True)
    q = lst.calls[0].request.url.params
    assert q["unread_only"] == "1" and q["team"] == "TEAM-1"
    b = route(r, "post", "central.notification.api.notification_badge", {"count": 2})
    assert notifications.badge(central) == {"count": 2}
    assert json.loads(b.calls[0].request.content)["team"] == "TEAM-1"
    m = route(r, "post", "central.notification.api.mark_notification_read", {})
    notifications.mark_read(central, "N-1")
    assert json.loads(m.calls[0].request.content)["name"] == "N-1"
    route(r, "post", "central.notification.api.mark_all_notifications_read", {})
    notifications.mark_all_read(central)


def test_activity_infra(r, central):
    t = route(r, "get", "central.api.activity.team_activity", [])
    activity.team_activity(central, limit=5)
    assert t.calls[0].request.url.params["limit"] == "5"
    s = route(r, "get", "central.api.activity.server_activity", [])
    activity.server_activity(central, "srv1")
    assert s.calls[0].request.url.params["resource_id"] == "srv1"
    i = route(r, "get", "central.api.images.eligible_plans", [])
    infra.plans_eligible(central, image_id="img")
    assert i.calls[0].request.url.params["image_id"] == "img"
    route(r, "get", "central.api.images.list_images", [])
    infra.images(central)
    o = route(r, "get", "central.api.images.list_offerings", [])
    infra.offerings(central)
    assert o.calls[0].request.url.params["flow"] == "Server"


def test_servers_additions(r, central):
    h = route(r, "get", "central.api.servers.server_hostnames", ["a"])
    assert servers.hostnames(central, "s1") == ["a"]
    assert h.calls[0].request.url.params["resource_id"] == "s1"
    p = route(r, "post", "central.api.servers.stop_server", {})
    servers.stop(central, "s1")
    assert json.loads(p.calls[0].request.content)["resource_id"] == "s1"
    assert servers.start is servers.start_server


def test_buckets(r, central):
    route(r, "get", "central.services.api.storage.get_object_storage", {"buckets": [{"name": "b"}]})
    assert objects.list_buckets(central) == [{"name": "b"}]
    q = route(r, "post", "central.services.api.storage.set_bucket_quota", {})
    objects.set_quota(central, "b", size_gib=5)
    assert json.loads(q.calls[0].request.content) == {"name": "b", "size_gib": 5, "team": "TEAM-1"}
    u = route(r, "get", "central.services.api.storage.get_bucket_usage", {"used_bytes": 1})
    assert objects.bucket_usage(central, "b")["used_bytes"] == 1
    assert u.calls[0].request.url.params["name"] == "b"


D = "central.billing.api.dashboard."


def mock_billing(r, *, spend=8300.0, running=2000.0, used=2, maxres=3, balance=100.0, standing="Current",
                 action=False, shortfall=0):
    route(r, "get", D + "get_trust_tier", {
        "current": {"tier": "Beginner", "max_spend": spend, "max_resource_count": maxres},
        "spending_limit": spend, "running_monthly": running, "progress": {"resources_used": used}})
    route(r, "get", D + "get_credit_balance", {"balance": balance})
    route(r, "get", D + "get_team_overview", {"standing": standing})
    route(r, "get", D + "get_collection_status", {"collection_mode": "Prepaid", "action_required": action,
                                                  "reason": "x" if action else None, "shortfall": shortfall})


def test_billing_reads(r, central):
    for fn, call in (("get_credit_balance", billing.wallet_balance), ("credit_ledger", billing.ledger),
                     ("list_invoices", billing.invoices_list), ("list_subscriptions", billing.subscriptions)):
        route(r, "get", D + fn, [fn])
        assert call(central) == [fn]
    g = route(r, "post", D + "get_invoice", {"name": "I"})
    billing.invoice_get(central, "I")
    assert json.loads(g.calls[0].request.content)["name"] == "I"


def test_overview(r, central):
    for fn in ("get_team_overview", "get_forecast", "get_cycle_costs", "get_next_payment", "get_collection_status"):
        route(r, "get", D + fn, {"k": fn})
    o = billing.overview(central)
    assert set(o) == {"team", "forecast", "cycle_costs", "next_payment", "collection_status"}


def test_health_ok(r, central):
    mock_billing(r)
    h = billing.billing_health(central)
    assert h["ok"] and h["tier"] == "Beginner" and h["headroom"] == {"spend": 6300.0, "resources": 1}
    assert h["balance"] == 100.0 and h["problems"] == []


def test_health_problems(r, central):
    mock_billing(r, running=8300.0, used=3, balance=1, standing="Overdue", action=True, shortfall=5)
    h = billing.billing_health(central, min_balance=10)
    assert not h["ok"] and h["overdue"] and h["action_required"]
    assert len(h["problems"]) == 6
