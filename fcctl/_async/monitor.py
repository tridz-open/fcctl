"""Async metrics / monitoring."""
from __future__ import annotations

from .central import CentralClient
from .pilot import PilotClient


async def central_metrics(c: CentralClient, resource_id: str, period: str = "1h"):
    return await c.call("GET", "central.api.servers.server_metrics", params={"resource_id": resource_id, "period": period})


async def metrics(p: PilotClient):
    return await p.get("/metrics")


async def system(p: PilotClient):
    return await p.get("/system")


async def health(p: PilotClient):
    return await p.get("/health")


async def monitor_status(p: PilotClient):
    """Monitor log sources."""
    return await p.get("/monitor/status")
