"""The ONLY module allowed to sleep or read the clock. Sync twin: fcctl/_sync/_io.py (handwritten)."""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import httpx


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def now() -> float:
    return time.monotonic()


class Deadline:
    def __init__(self, timeout: float, clock=now):
        self.clock = clock
        self.at = clock() + timeout

    def expired(self) -> bool:
        return self.clock() >= self.at


class Semaphore:
    def __init__(self, n: int):
        self._s = asyncio.Semaphore(n)

    async def __aenter__(self):
        await self._s.acquire()
        return self

    async def __aexit__(self, *exc):
        self._s.release()


class Lock:
    def __init__(self):
        self._l = asyncio.Lock()

    async def __aenter__(self):
        await self._l.acquire()
        return self

    async def __aexit__(self, *exc):
        self._l.release()


async def aiter_lines(resp: httpx.Response) -> AsyncIterator[str]:
    async for line in resp.aiter_lines():
        yield line
