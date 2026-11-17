"""Async server creation catalog: images, offerings, eligible plans."""
from __future__ import annotations

from .central import CentralClient

M = "central.api.images."

OPS = {"images": "infra.images", "offerings": "infra.offerings", "plans_eligible": "infra.plans_eligible"}


async def images(c: CentralClient, region: str = "bom", offering: str = "pilot", flow: str | None = None,
           offset: int | None = None):
    p = {"region": region, "offering": offering}
    if flow:
        p["flow"] = flow
    if offset is not None:
        p["offset"] = offset
    return await c.call("GET", M + "list_images", params=p)


async def offerings(c: CentralClient, flow: str = "Server"):
    return await c.call("GET", M + "list_offerings", params={"flow": flow})


async def plans_eligible(c: CentralClient, region: str = "bom", offering: str | None = None,
                   image_id: str | None = None, snapshot: str | None = None):
    p = {"region": region}
    for k, v in (("offering", offering), ("image_id", image_id), ("snapshot", snapshot)):
        if v:
            p[k] = v
    return await c.call("GET", M + "eligible_plans", params=p)
