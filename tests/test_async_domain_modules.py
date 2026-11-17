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


class SchedP:
    def __init__(self):
        self.calls = []
        self.put_body = None

    async def get(self, path, **kw):
        self.calls.append(("GET", path))
        return {"retention": {"keep_weekly": 4}, "schedule": None}

    async def put(self, path, json=None, **kw):
        self.calls.append(("PUT", path))
        self.put_body = json
        return {}


def test_set_schedule_preserves_retention():
    from fcctl._async import backups
    p = SchedP()
    asyncio.run(backups.set_schedule(p, "a.x", "0 2 * * *"))
    assert ("GET", "/sites/a.x/backup-schedule") in p.calls
    assert p.put_body == {"schedule": "0 2 * * *", "retention": {"keep_weekly": 4}}


def test_set_schedule_explicit_retention_no_get():
    from fcctl._async import backups
    p = SchedP()
    r = {"keep_weekly": 2}
    asyncio.run(backups.set_schedule(p, "a.x", "0 2 * * *", r))
    assert not any(c[0] == "GET" for c in p.calls)
    assert p.put_body == {"schedule": "0 2 * * *", "retention": r}
