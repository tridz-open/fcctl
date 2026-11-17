"""Shared (non-generated) facade types, so sync and async clients use one class."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackupSource:
    """Source backup for workflows.provision_site(from_backup=...)."""
    profile: str
    server: str
    site: str
    ts: str
    max_age: str | None = None
