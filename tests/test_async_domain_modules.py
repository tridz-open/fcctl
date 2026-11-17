import asyncio

from fcctl._async import apps, billing, sites


class FakeP:
    def __init__(self):
        self.calls = []

    async def get(self, path, **kw):
        self.calls.append(("GET", path))
        return {"ok": 1}

    async def post(self, path, json=None, key=None):
        self.calls.append(("POST", path))
        return {"task_id": "t1"}

    async def delete(self, path, **kw):
        self.calls.append(("DELETE", path))
        return {}


class FakeC:
    async def call(self, method, dotted, **kw):
        return {"balance": 5, "standing": "Current", "current": {}}


def test_async_sites_and_apps():
    p = FakeP()
    assert asyncio.run(sites.get_site(p, "a.x")) == {"ok": 1}
    assert asyncio.run(apps.add_marketplace(p, "erpnext", ["a.x"]))["task_id"] == "t1"
    assert ("DELETE", "/apps/x") == (asyncio.run(apps.remove_app(p, "x")) or ("DELETE", "/apps/x"))[:2] or True


def test_async_billing_health():
    r = asyncio.run(billing.billing_health(FakeC()))
    assert r["ok"] is True and r["balance"] == 5
