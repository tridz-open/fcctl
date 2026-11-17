"""Public facade: Client bundles a CentralClient with pilot() factories (async flavour = AsyncClient)."""
from __future__ import annotations

from ..session_store import SessionStore
from .central import CentralClient
from .facade import Backups, Servers, Sites, Workflows
from .pilot import PilotClient


class AsyncClient:
    def __init__(self, store: SessionStore, team: str | None = None, **kw):
        self.central = CentralClient(store, team, **kw)
        self.sites, self.servers = Sites(self), Servers(self)
        self.backups, self.workflows = Backups(self), Workflows(self)

    def pilot(self, server: str, **kw) -> PilotClient:
        return PilotClient(self.central, server, **kw)

    async def aclose(self) -> None:
        await self.central.aclose()
