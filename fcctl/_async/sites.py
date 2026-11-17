"""Site operations through Pilot.

Every function that starts a task returns the 202 task dict; use tasks.run()/wait_task to follow it.
"""
from __future__ import annotations

from .pilot import PilotClient


async def list_sites(p: PilotClient):
    return await p.get("/sites")


async def get_site(p: PilotClient, name: str):
    return await p.get(f"/sites/{name}")


async def wildcard_domains(p: PilotClient):
    return await p.get("/sites/wildcard-domains")


async def create_site(p: PilotClient, name: str, apps: list[str], key: str | None = None):
    """Create a site. Apps are installed in dependency order (see apps.install_order, e.g. ["erpnext", "hrms"]); name ends with a wildcard suffix."""
    return await p.post("/sites", {"name": name, "apps": apps}, key=key)


async def clone_site(p: PilotClient, source: str, name: str, target_bench: str = "default-bench", key=None):
    """Golden-source clone; body keys name, target_bench."""
    return await p.post(f"/sites/{source}/actions/clone", {"name": name, "target_bench": target_bench}, key=key)


async def install_app(p: PilotClient, site: str, app: str | None = None, repo: str | None = None, branch: str | None = None):
    """Install an app: {app} or {repo, branch} (repo needs bench scope)."""
    body = {"app": app} if app else {"repo": repo, **({"branch": branch} if branch else {})}
    return await p.post(f"/sites/{site}/apps", body)


async def uninstall_app(p: PilotClient, site: str, app: str, force: bool = False, remove_from_bench: bool = False):
    return await p.delete(f"/sites/{site}/apps/{app}", force=str(force).lower(),
                    remove_from_bench=str(remove_from_bench).lower())


async def set_maintenance(p: PilotClient, site: str, on: bool, pause_scheduler: bool | None = None):
    """PATCH {maintenance_mode, pause_scheduler} (sync, no task)."""
    return await p.patch(f"/sites/{site}/configuration", {
        "maintenance_mode": 1 if on else 0,
        "pause_scheduler": int(on if pause_scheduler is None else pause_scheduler)})


async def clear_cache(p: PilotClient, site: str):
    return await p.post(f"/sites/{site}/actions/clear-cache")


async def migrate(p: PilotClient, site: str):
    """Migrate (409 task_conflict is common: use tasks.retry_conflict)."""
    return await p.post(f"/sites/{site}/actions/migrate")


async def build_assets(p: PilotClient, site: str):
    return await p.post(f"/sites/{site}/actions/build-assets")


async def login_link(p: PilotClient, site: str, hint: str | None = None) -> str:
    """Site login link -> 201 {url}."""
    r = await p.post(f"/sites/{site}/login", {"hint": hint} if hint else {})
    return r["url"]


async def drop_site(p: PilotClient, site: str, no_backup: bool = False):
    """DESTRUCTIVE; takes a backup first unless no_backup."""
    return await p.request("DELETE", f"/sites/{site}", params={"no_backup": "1"} if no_backup else None)


async def rename_site(p: PilotClient, site: str, new_name: str, keep_old_hostname=False):
    return await p.post(f"/sites/{site}/actions/rename",
                        {"new_name": new_name, "keep_old_hostname": keep_old_hostname})


# ---- v1 additions
OPS = {"config_set": "site.config_set", "config_get": "site.config_get", "site_apps": "site.apps_list",
       "set_maintenance": "site.set_maintenance"}


async def config_get(p: PilotClient, site: str):
    return await p.get(f"/sites/{site}/configuration")


async def config_set(p: PilotClient, site: str, **values):
    """PATCH any of maintenance_mode, pause_scheduler, ..."""
    return await p.patch(f"/sites/{site}/configuration", dict(values))


async def site_apps(p: PilotClient, site: str):
    return await p.get(f"/sites/{site}/apps")
