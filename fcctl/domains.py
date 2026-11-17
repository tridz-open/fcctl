"""Site domains through Pilot (read only)."""
from __future__ import annotations

from .request import PilotClient

OPS = {"list_domains": "domain.list", "dns_records": "domain.dns_records"}


def list_domains(p: PilotClient, site: str):
    return p.get(f"/sites/{site}/domains")


def dns_records(p: PilotClient, site: str, domain: str):
    return p.get(f"/sites/{site}/domains/{domain}/dns-records")
