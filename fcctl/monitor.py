"""DEPRECATED shim: implementation lives in fcctl._async.monitor (sync twin: fcctl._sync.monitor)."""
from __future__ import annotations

from ._sync.monitor import central_metrics, health, metrics, system  # noqa: F401
