"""Shared transport helpers."""
from __future__ import annotations

import httpx

from .._version import USER_AGENT as UA  # noqa: F401

CENTRAL_BASE = "https://dashboard.frappe.io"


def make_http(timeout: float, ua: str, http2: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, headers={"User-Agent": ua}, http2=http2)
