"""Shim: implementation lives in fcctl._async.snapshots (generated sync twin: fcctl._sync.snapshots)."""
from __future__ import annotations

from ._sync import snapshots as _impl  # noqa: F401
from ._sync.snapshots import M, delete, list_snapshots, take, wait_available  # noqa: F401
