"""Async client: list sites on several servers concurrently. Requires a prior `fcctl auth login`."""
import asyncio

from fcctl import AsyncClient
from fcctl.session_store import SessionStore

SERVERS = ["server-aaaa", "server-bbbb"]


async def main():
    c = AsyncClient(SessionStore(), team="TEAM-xxxxx")
    try:
        results = await asyncio.gather(*(c.sites.list(s) for s in SERVERS))
        for server, sites in zip(SERVERS, results):
            print(server, len(sites))
    finally:
        await c.aclose()


asyncio.run(main())
