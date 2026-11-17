"""Bench apps through Pilot."""
from __future__ import annotations

from typing import Any

from ..errors import ApiError
from . import tasks
from .pilot import PilotClient

# Apps that need another app on the bench first, or Pilot's import validation fails.
DEFAULT_DEPS: dict[str, list[str]] = {"hrms": ["erpnext"]}


def install_order(apps: list[str], deps: dict[str, list[str]] | None = None) -> list[str]:
    """Return `apps` in dependency order (dependencies first, stable otherwise).

    Dependencies named in `deps` (default DEFAULT_DEPS) are added if missing. Raises ValueError on a cycle.
    """
    deps = DEFAULT_DEPS if deps is None else deps
    out: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in out:
            return
        if name in visiting:
            raise ValueError(f"dependency cycle at {name}")
        visiting.add(name)
        for d in deps.get(name, []):
            visit(d)
        visiting.discard(name)
        out.append(name)

    for a in apps:
        visit(a)
    return out


async def list_apps(p: PilotClient):
    return await p.get("/apps")


async def add_marketplace(p: PilotClient, name: str, sites: list[str] | None = None):
    return await p.post("/apps", {"name": name, **({"sites": sites} if sites else {})})


async def add_repo(p: PilotClient, repo: str, branch: str, sites: list[str] | None = None,
                   name: str | None = None):
    """Clone a repo into the bench. The app name comes from Pilot's repository resolution
    (what the dashboard does) unless `name` is given, which skips the resolution."""
    if not name:
        res = await p.post("/git/repository-resolutions", {"repo": repo, "branch": branch})
        name = res.get("name") if isinstance(res, dict) else None
        if not name:
            msg = (res.get("message") or res.get("error") or res) if isinstance(res, dict) else res
            raise ApiError(f"could not resolve an app name for {repo}@{branch}: {msg}", body=res)
    return await p.post("/apps", {"name": name, "repo": repo, "branch": branch, "sites": sites or []})


def _task_id(resp: Any) -> str | None:
    if isinstance(resp, dict):
        return resp.get("task_id") or (resp.get("task") or {}).get("task_id")
    return None


async def add_in_order(p: PilotClient, specs: list[dict], **wait_kw):
    """Add apps sequentially (each spec {name} or {repo, branch}); waits for each task.
    wait_kw: timeout/poll (sleeping goes through _io)."""
    out = []
    for s in specs:
        start = (lambda s=s: add_repo(p, s["repo"], s.get("branch", "main"), name=s.get("name")) if "repo" in s
                 else add_marketplace(p, s["name"]))
        resp = await tasks.retry_conflict(start)
        tid = _task_id(resp)
        out.append(await tasks.wait_task(p, tid, **wait_kw) if tid else resp)
    return out


async def check_updates(p: PilotClient):
    """Start a fetch-all-app-updates task (bench-wide git fetch); returns {task_id}."""
    return await p.post("/apps/fetch")


async def check_updates_result(p: PilotClient, task_id: str):
    """Parse the last non-empty line of the task output as JSON ({app: {...}})."""
    import json
    out = await p.get(f"/tasks/{task_id}/output/content")
    if isinstance(out, dict):
        out = out.get("content", "")
    lines = [ln for ln in str(out or "").splitlines() if ln.strip()]
    if not lines:
        return {}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {}


async def update_apps(p: PilotClient, apps: list[str] | None = None, disable_safeguards: bool = False):
    """Chain backup->update->migrate; returns {operation, task_id}."""
    return await p.post("/updates", {**({"apps": apps} if apps else {}), "disable_safeguards": disable_safeguards})


async def switch_branch(p: PilotClient, name: str, branch: str):
    return await p.post(f"/apps/{name}/actions/switch-branch", {"branch": branch})


async def remove_app(p: PilotClient, name: str):
    """Remove an app -> remove-app task."""
    return await p.delete(f"/apps/{name}")


OPS = {"remove_app": "app.remove", "list_apps": "app.list", "remove": "app.remove"}


async def remove(p: PilotClient, name: str):
    """Guarded; same as remove_app."""
    return await remove_app(p, name)
