"""DEPRECATED shim: implementation lives in fcctl._async.servers (generated sync twin: fcctl._sync.servers).

Kept so existing imports (`from fcctl import servers`) keep working. Prefer fcctl.client / fcctl._sync.
"""
from __future__ import annotations

from ._sync import servers as _impl  # noqa: F401
from ._sync.servers import (M, OPS, action_status, activity, clone_from_snapshot, create_server,  # noqa: F401
                            hostnames, list_servers, overview, registry, resize, resize_server, restart,
                            restart_server, set_termination_protection, start, start_server, stop, stop_server,
                            terminate_server, wait_running)
