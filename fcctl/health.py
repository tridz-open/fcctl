"""Default site health check (no Administrator session) + opt-in admin-session checks.

Live-verified semantics:
  maintenance on : ping 200 AND root 503 -> healthy_in_maintenance; root 200 -> FAIL (isolation missing)
  maintenance off: ping 200 AND root 200/302 -> healthy
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .request import FcError, NotFound, PilotClient

HEALTHY, HEALTHY_MAINT, UNHEALTHY = "healthy", "healthy_in_maintenance", "unhealthy"
BAD_STATUS = {"broken", "archived", "inactive", "error", "failed"}
PROVISIONING = {"pending", "installing", "provisioning", "creating", "new", "recovering"}


@dataclass
class HealthResult:
    site: str
    state: str = UNHEALTHY
    reasons: list[str] = field(default_factory=list)
    maintenance_mode: bool | None = None
    ping_status: int | None = None
    root_status: int | None = None
    site_status: str | None = None
    task_status: str | None = None

    @property
    def ok(self) -> bool:
        return self.state in (HEALTHY, HEALTHY_MAINT)


class SiteAdminClient(Protocol):
    """Interface for opt-in checks needing an Administrator session; implemented by
    fcctl._sync.siteadmin.SiteAdminClient (and its async twin)."""
    def get_list(self, doctype: str, filters: Any = None, fields: Any = None, limit: int = 20) -> Any: ...
    def get_doc(self, doctype: str, name: str) -> Any: ...
    def insert(self, doctype: str, data: dict) -> Any: ...
    def update(self, doctype: str, name: str, data: dict) -> Any: ...
    def delete(self, doctype: str, name: str) -> Any: ...
    def call(self, method: str, **args: Any) -> Any: ...
    def ping(self) -> Any: ...
    def close(self) -> None: ...


def _get(http: httpx.Client, url: str) -> int | None:
    try:
        return http.get(url, follow_redirects=False).status_code
    except httpx.HTTPError:
        return None


def check_site_health(pilot: PilotClient, site: str, http: httpx.Client | None = None,
                      task_id: str | None = None, timeout: float = 20.0) -> HealthResult:
    res = HealthResult(site)
    why = res.reasons
    try:
        info = pilot.get(f"/sites/{site}")
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
            t = pilot.get(f"/tasks/{task_id}")
            res.task_status = str((t or {}).get("status", "")).lower() or None
            if res.task_status in ("failed", "killed"):
                why.append(f"task {task_id} {res.task_status}")
            elif res.task_status in ("pending", "running", "queued"):
                why.append(f"task {task_id} still {res.task_status}")
        except FcError as e:
            why.append(f"task lookup failed: {e}")

    own = http is None
    http = http or httpx.Client(timeout=timeout, headers={"User-Agent": "fcctl-health"})
    try:
        res.ping_status = _get(http, f"https://{site}/api/method/ping")
        res.root_status = _get(http, f"https://{site}/")
    finally:
        if own:
            http.close()
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


def check_admin_session(admin: SiteAdminClient, site: str) -> HealthResult:
    """Opt-in deep checks (scheduler, error logs, ...) requiring an Administrator session. Stub."""
    raise NotImplementedError("admin-session health checks are not implemented; supply a SiteAdminClient")
