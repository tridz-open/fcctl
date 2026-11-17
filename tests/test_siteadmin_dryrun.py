import asyncio

import httpx
import respx

from fcctl._async.siteadmin import SiteAdminClient
from fcctl.request import DryRunRecorder


@respx.mock
def test_post_under_recorder_is_recorded_not_sent():
    route = respx.route().mock(return_value=httpx.Response(500))

    async def go():
        async with httpx.AsyncClient() as h:
            c = SiteAdminClient("a.x", h)
            DryRunRecorder.start()
            try:
                r = await c._req("POST", "/api/method/x", json={"a": 1})
            finally:
                rec = DryRunRecorder.stop()
            return r, rec

    r, rec = asyncio.run(go())
    assert r.status_code == 200 and r.json() == {}
    assert rec == [{"method": "POST", "path": "/api/method/x", "params": None, "body": {"a": 1}}]
    assert not route.called
