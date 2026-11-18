"""Async server snapshots on Central."""
from __future__ import annotations

from ..guard import guarded
from . import _io
from .central import CentralClient

M = "central.api.snapshots."


async def take(c: CentralClient, resource_id: str, title: str | None = None):
    return await c.call("POST", M + "take_snapshot", json={"resource_id": resource_id, "title": title})


async def list_snapshots(c: CentralClient, resource_id: str | None = None):
    return await c.call("GET", M + "list_snapshots", params={"resource_id": resource_id} if resource_id else {})


@guarded("delete_snapshots", lambda names: ",".join(names))
async def delete(c: CentralClient, names: list[str]):
    return await c.call("POST", M + "delete_snapshots", json={"names": names})


async def wait_available(c: CentralClient, resource_id: str, name: str, timeout=1800, poll=20):
    dl = _io.Deadline(timeout)
    while True:
        for s in ((await list_snapshots(c, resource_id)) or {}).get("snapshots", []):
            if s.get("name") == name and s.get("status") in ("Available", "Ready", "Completed"):
                return s
        if dl.expired():
            break
        await _io.sleep(poll)
    raise TimeoutError(f"snapshot {name} not available")

