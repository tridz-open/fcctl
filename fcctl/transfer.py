"""Deprecated shim: use fcctl._async.transfer / fcctl._sync.transfer.

Re-exports the generated sync implementation. Sleeping goes through fcctl._sync._io.sleep
(patch that in tests instead of the old module-level ``_sleep``).
"""
from ._sync.transfer import (PART_SUFFIX, TransferError, _auth, _call, _load, _read_chunk,  # noqa: F401
                             classify, restore_from_site_backup, restore_via_download_links)
