"""DEPRECATED shim: implementation lives in fcctl._async.logs (sync twin: fcctl._sync.logs)."""
from __future__ import annotations

from ._sync.logs import content, list_logs, tail, task_events  # noqa: F401
