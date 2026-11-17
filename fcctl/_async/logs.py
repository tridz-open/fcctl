"""Async Pilot logs."""
from __future__ import annotations

from typing import AsyncIterator

from .pilot import PilotClient
from .tasks import stream_events


async def list_logs(p: PilotClient):
    return await p.get("/logs")


async def content(p: PilotClient, filename: str, lines: int | None = None):
    if lines:
        resp = await p.get(f"/logs/{filename}", lines=lines)
        return "\n".join(resp["lines"])
    r = await p.request("GET", f"/logs/{filename}/content", raw=True)
    r.raise_for_status()
    return r.text


async def tail(p: PilotClient, filename: str) -> AsyncIterator[str]:
    """SSE, yields lines; never ends on its own."""
    import json
    async for d in stream_events(p, f"/logs/{filename}/events"):
        try:
            j = json.loads(d)
            yield j.get("line", d) if isinstance(j, dict) and "error" not in j else str(j)
        except ValueError:
            yield d


def task_events(p: PilotClient, task_id: str) -> AsyncIterator[str]:
    return stream_events(p, f"/tasks/{task_id}/events")
