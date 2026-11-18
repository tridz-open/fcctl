from __future__ import annotations

from typing import Any

from .request import CentralClient

M = "central.notification.api."

OPS = {"list_notifications": "notification.list", "badge": "notification.badge",
       "mark_read": "notification.read", "mark_all_read": "notification.read_all"}


def list_notifications(c: CentralClient, unread_only: bool = False, start: int = 0, limit: int = 20,
                       category: str | None = None):
    """GET; unread_only is sent as 0/1."""
    p: dict[str, Any] = {"start": start, "limit": limit, "unread_only": int(bool(unread_only))}
    if category:
        p["category"] = category
    return c.call("GET", M + "list_notifications", params=p)


def badge(c: CentralClient):
    return c.call("POST", M + "notification_badge", json={})


def mark_read(c: CentralClient, name: str):
    return c.call("POST", M + "mark_notification_read", json={"name": name})


def mark_all_read(c: CentralClient):
    return c.call("POST", M + "mark_all_notifications_read", json={})
