"""Default Frappe Cloud region for server and bucket operations.

Resolution order: explicit argument, then the `FCCTL_REGION` environment variable, then `DEFAULT_REGION`.
"""
from __future__ import annotations

import os
from typing import Mapping

DEFAULT_REGION = "bom"


def default_region(env: Mapping[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    return (env.get("FCCTL_REGION") or "").strip() or DEFAULT_REGION
