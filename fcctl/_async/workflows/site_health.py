"""Async default site health check (same semantics as fcctl.health.check_site_health)."""
from __future__ import annotations

import httpx

from ..._version import USER_AGENT
from ...errors import FcError, NotFound
from ...health import (BAD_STATUS, HEALTHY, HEALTHY_MAINT, PROVISIONING, HealthResult)  # noqa: F401
from ..pilot import PilotClient


async def _get(http: httpx.AsyncClient, url: str) -> int | None:
    try:
        return (await http.get(url, follow_redirects=False)).status_code
    except httpx.HTTPError:
        return None


async def check_site_health(pilot: PilotClient, site: str, http: httpx.AsyncClient | None = None,
                            task_id: str | None = None, timeout: float = 20.0) -> HealthResult:
    res = HealthResult(site)
    why = res.reasons
    try:
        info = await pilot.get(f"/sites/{site}")
    except NotFound:
        why.append("site not found in Pilot")
        return res
    except FcError as e:
        why.append(f"pilot lookup failed: {e}")
        return res
    info = info if isinstance(info, dict) else {}
    info = info.get("site", info) if isinstance(info.get("site"), dict) else info
    status = str(info.get("status") or "").lower()
    res.site_status = status or None
    mm = info.get("maintenance_mode", (info.get("config") or {}).get("maintenance_mode"))
    res.maintenance_mode = None if mm is None else bool(int(mm)) if str(mm).lstrip("-").isdigit() or isinstance(mm, bool) else bool(mm)
    if status in BAD_STATUS:
        why.append(f"site status is {status}")
    if status in PROVISIONING:
        why.append(f"site still provisioning ({status})")
    if task_id:
        try:
            t = await pilot.get(f"/tasks/{task_id}")
            res.task_status = str((t or {}).get("status", "")).lower() or None
            if res.task_status in ("failed", "killed"):
                why.append(f"task {task_id} {res.task_status}")
            elif res.task_status in ("pending", "running", "queued"):
                why.append(f"task {task_id} still {res.task_status}")
        except FcError as e:
            why.append(f"task lookup failed: {e}")

    own = http is None
    http = http or httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT})
    try:
        res.ping_status = await _get(http, f"https://{site}/api/method/ping")
        res.root_status = await _get(http, f"https://{site}/")
    finally:
        if own:
            await http.aclose()
    if res.ping_status != 200:
        why.append(f"ping returned {res.ping_status}")
    mm_on = bool(res.maintenance_mode)
    if mm_on:
        if res.root_status == 200:
            why.append("maintenance mode on but root returns 200 (isolation missing)")
        elif res.root_status != 503:
            why.append(f"maintenance mode on, expected root 503, got {res.root_status}")
    elif res.root_status not in (200, 302):
        why.append(f"root returned {res.root_status}")
    if not why:
        res.state = HEALTHY_MAINT if mm_on else HEALTHY
    return res
