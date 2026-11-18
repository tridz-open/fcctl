"""Site backups through Pilot."""
from __future__ import annotations

from typing import Any

from ..errors import UsageError
from . import transfer
from .pilot import seg, PilotClient


async def create(p: PilotClient, site: str):
    return await p.post(f"/sites/{seg(site)}/backups")


async def list_backups(p: PilotClient, site: str, limit: int = 20):
    return await p.get(f"/sites/{seg(site)}/backups", limit=limit)


async def get(p: PilotClient, site: str, timestamp: str):
    return await p.get(f"/sites/{seg(site)}/backups/{seg(timestamp)}")


async def download_links(p: PilotClient, site: str, timestamp: str):
    return await p.get(f"/sites/{seg(site)}/backups/{seg(timestamp)}/download-links")


async def restore(p: PilotClient, site: str, parts=("database", "public", "private", "config"),
            source_site: str | None = None, backup_timestamp: str | None = None,
            upload_id: str | None = None, skip_failing_patches: bool = False):
    body = {"parts": list(parts), "skip_failing_patches": skip_failing_patches}
    if source_site:
        body["source_site"] = source_site
    if backup_timestamp:
        body["backup_timestamp"] = backup_timestamp
    if upload_id:
        body["upload_id"] = upload_id
    return await p.post(f"/sites/{seg(site)}/actions/restore", body)


async def get_schedule(p: PilotClient, site: str):
    return await p.get(f"/sites/{seg(site)}/backup-schedule")


async def set_schedule(p: PilotClient, site: str, cron: str = "0 2 * * *", retention: dict | None = None):
    if retention is None:
        current = await p.get(f"/sites/{seg(site)}/backup-schedule")
        retention = current.get("retention") if isinstance(current, dict) else None
    return await p.put(f"/sites/{seg(site)}/backup-schedule", {"schedule": cron, "retention": retention})


# ---- v1 additions
OPS = {"offsite_list": "backup.offsite_list", "offsite_download": "backup.offsite_download",
       "restore_upload": "backup.restore_upload", "restore": "backup.restore"}


async def offsite_list(p: PilotClient, site: str):
    return await p.get(f"/sites/{seg(site)}/integrations/frappe-cloud/backups")


async def download_file(p: PilotClient, site: str, timestamp: str, file_id: str, dest: str):
    """Stream a local backup file to disk."""
    s = await p.session()
    async with p.http.stream("GET", f"{s['gateway']}/api/v1/sites/{site}/backups/{timestamp}/files/{file_id}/content",
                             headers={"Authorization": f"Bearer {s['token']}"}) as r:
        r.raise_for_status()
        chunks: Any = getattr(r, "aiter_bytes", None) or r.iter_bytes  # async / sync httpx response
        with open(dest, "wb") as f:
            async for chunk in chunks():
                f.write(chunk)
    return dest


async def upload_from_site_backup(src: PilotClient, src_site: str, timestamp: str, dst: PilotClient,
                                  dst_site: str, **kw):
    """Stream a backup src -> dst upload only (harmless: does not touch dst_site). Returns {upload_id, parts}."""
    return await transfer.restore_from_site_backup(src, src_site, timestamp, dst, dst_site, upload_only=True, **kw)


async def restore_from_site_backup(src: PilotClient, src_site: str, timestamp: str, dst: PilotClient,
                                   dst_site: str, *, owned_by_workflow: str | None = None, **kw):
    """Cross-server restore by streaming a backup src -> dst upload, then POST actions/restore.

    This OVERWRITES dst_site, so the async core only does it for a workflow-owned target
    (verified by the caller); otherwise it refuses. Non-owned callers use
    upload_from_site_backup() then the guarded `restore` (the fcctl.backups shim does this).
    """
    if not owned_by_workflow:
        raise UsageError("restore_from_site_backup overwrites the target; pass owned_by_workflow=<workflow_id> "
                         "or use upload_from_site_backup() + the guarded restore()")
    return await transfer.restore_from_site_backup(src, src_site, timestamp, dst, dst_site, **kw)


async def offsite_download(src: PilotClient, src_site: str, timestamp: str, dst: PilotClient, dst_site: str,
                           parts=("database", "public", "private"), skip_failing_patches: bool = False,
                           allow_overwrite: bool = False):
    """download-links then restore on dst (transfer.restore_via_download_links). OVERWRITES dst_site, so it
    refuses unless allow_overwrite=True (workflow-owned target only); otherwise use the guarded `restore`."""
    if not allow_overwrite:
        raise ValueError("offsite_download overwrites the target site; pass allow_overwrite=True "
                         "for a workflow-owned target, or use the guarded restore()")
    return await transfer.restore_via_download_links(src, src_site, timestamp, dst, dst_site, parts=parts,
                                                     skip_failing_patches=skip_failing_patches)


async def restore_upload(src: PilotClient, src_site: str, timestamp: str, dst: PilotClient, dst_site: str, **kw):
    """Convenience for restore_from_site_backup (owned_by_workflow required)."""
    return await restore_from_site_backup(src, src_site, timestamp, dst, dst_site, **kw)
