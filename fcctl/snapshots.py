"""DEPRECATED shim: implementation lives in fcctl._async.snapshots (generated sync twin: fcctl._sync.snapshots).

Kept so existing imports (`from fcctl import snapshots`) keep working. Prefer fcctl.client / fcctl._sync.
"""
from __future__ import annotations

from ._sync import snapshots as _impl  # noqa: F401
from ._sync.snapshots import (M, delete, keep, list_snapshots, pricing, restore_as_new_server,  # noqa: F401
                              set_automatic, take, wait_available)
