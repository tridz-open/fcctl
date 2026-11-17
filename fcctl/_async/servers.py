from __future__ import annotations

import uuid

from ..guard import guarded
from . import _io
from .central import CentralClient

M = "central.api.servers."


async def registry(c: CentralClient):
    return await c.call("GET", M + "registry")


async def list_servers(c: CentralClient) -> list[dict]:
    return ((await registry(c)) or {}).get("servers", [])


async def overview(c: CentralClient, resource_id: str):
    return await c.call("GET", M + "server_overview", params={"resource_id": resource_id})


async def action_status(c: CentralClient, name: str):
    return await c.call("GET", M + "action_status", params={"name": name}, team=False)


async def create_server(c: CentralClient, title: str, plan: str, image_id: str, region: str = "bom",
                  hostname: str | None = None, snapshot: str | None = None, ssh_key_ids=None,
                  has_public_ipv4: bool = False, has_public_ipv6: bool = True,
                  is_firewall_enabled: bool = True, request_key: str | None = None):
    """Create a server; with `snapshot` it clones from a snapshot (same region only).
    request_key makes the create idempotent: persist it and reuse on retry/fallback."""
    body = {"region": region, "title": title, "hostname": hostname or title, "offering": "pilot",
            "image_id": image_id, "ssh_key_ids": ssh_key_ids or [], "has_public_ipv4": has_public_ipv4,
            "has_public_ipv6": has_public_ipv6, "is_firewall_enabled": is_firewall_enabled, "plan": plan,
            "request_key": request_key or str(uuid.uuid4())}
    if snapshot:
        body["snapshot"] = snapshot
    return await c.call("POST", M + "create_server", json=body)


async def clone_from_snapshot(c: CentralClient, snapshot: str, **kw):
    return await create_server(c, snapshot=snapshot, **kw)


def _act(name):
    async def f(c: CentralClient, resource_id: str):
        return await c.call("POST", M + name, json={"resource_id": resource_id})
    f.__name__ = name
    f.__doc__ = f"Server action: {name.replace('_', ' ')}."
    return f


start_server, stop_server, restart_server = _act("start_server"), _act("stop_server"), _act("restart_server")


async def resize_server(c: CentralClient, resource_id: str, plan: str, **extra):
    return await c.call("POST", M + "resize_server", json={"resource_id": resource_id, "plan": plan, **extra})


async def set_termination_protection(c: CentralClient, resource_id: str, enabled: bool):
    return await c.call("POST", M + "set_termination_protection", json={"resource_id": resource_id, "enabled": enabled})


@guarded("terminate_server", lambda resource_id, take_snapshot: resource_id)
async def terminate_server(c: CentralClient, resource_id: str, take_snapshot: bool = False):
    return await c.call("POST", M + "terminate_server", json={"resource_id": resource_id, "take_snapshot": take_snapshot})


async def wait_running(c: CentralClient, resource_id: str, timeout: float = 900, poll: float = 15):
    dl = _io.Deadline(timeout)
    while True:
        for s in await list_servers(c):
            if s.get("resource_id") == resource_id or s.get("name") == resource_id:
                if s.get("status") == "Running":
                    return s
        if dl.expired():
            break
        await _io.sleep(poll)
    raise TimeoutError(f"server {resource_id} not Running after {timeout}s")


# ---- v1 additions. OPS maps function -> operation id (see fcctl/policy.py REGISTRY).
OPS = {"start_server": "server.start", "stop_server": "server.stop", "restart_server": "server.restart",
       "resize_server": "server.resize", "set_termination_protection": "server.set_termination_protection",
       "terminate_server": "server.terminate", "hostnames": "server.hostnames",
       "activity": "server.activity", "create_server": "server.create"}

# short aliases matching the SDK shape servers.start/stop/restart/resize
start, stop, restart, resize = start_server, stop_server, restart_server, resize_server


async def hostnames(c: CentralClient, resource_id: str):
    return await c.call("GET", M + "server_hostnames", params={"resource_id": resource_id})


async def activity(c: CentralClient, resource_id: str):
    return await c.call("GET", "central.api.activity.server_activity", params={"resource_id": resource_id})
