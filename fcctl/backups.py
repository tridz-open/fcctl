"""Site backups through Pilot.


DEPRECATED: thin shim over fcctl._sync.backups (generated from fcctl._async.backups); new code should use the async core / facade.
Only the guard wrappers live here (guard stays sync).
"""
from __future__ import annotations

from .guard import guarded
from ._sync import backups as _impl

OPS = _impl.OPS
create = _impl.create
list_backups = _impl.list_backups
get = _impl.get
download_links = _impl.download_links
get_schedule = _impl.get_schedule
set_schedule = _impl.set_schedule
offsite_list = _impl.offsite_list
download_file = _impl.download_file
offsite_download = _impl.offsite_download

restore = guarded("restore_backup", lambda site, **k: site, kind="pilot")(_impl.restore)


def restore_from_site_backup(src, src_site: str, timestamp: str, dst, dst_site: str,
                             *, owned_by_workflow: str | None = None, **kw):
    """Cross-server restore by streaming a backup src -> dst upload (see fcctl.transfer).

    Restore OVERWRITES dst_site. It is approval-exempt only when the target site was
    created by the same workflow run: pass ``owned_by_workflow=<workflow_id>``. Otherwise the backup is
    streamed into an upload on dst (harmless) and the overwriting restore is filed through the guarded
    ``restore`` (returns a PendingApproval).
    """
    if owned_by_workflow:
        return _impl.restore_from_site_backup(src, src_site, timestamp, dst, dst_site,
                                              owned_by_workflow=owned_by_workflow, **kw)
    up = _impl.upload_from_site_backup(src, src_site, timestamp, dst, dst_site, **kw)
    return restore(dst, dst_site, parts=tuple(up["parts"]), upload_id=up["upload_id"],
                   skip_failing_patches=kw.get("skip_failing_patches", False))


def restore_upload(src, src_site: str, timestamp: str, dst, dst_site: str, **kw):
    """Convenience for restore_from_site_backup (streams src backup into a dst upload, then actions/restore)."""
    return restore_from_site_backup(src, src_site, timestamp, dst, dst_site, **kw)
