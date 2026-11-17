"""Team and server activity feeds on Central."""
from __future__ import annotations

from .request import CentralClient

M = "central.api.activity."

OPS = {"team_activity": "activity.team", "server_activity": "activity.server"}


def team_activity(c: CentralClient, limit: int | None = None, cursor=None, category: str | None = None,
                  since: str | None = None, until: str | None = None):
    p = {k: v for k, v in dict(limit=limit, cursor=cursor, category=category, since=since, until=until).items()
         if v is not None}
    return c.call("GET", M + "team_activity", params=p)


def server_activity(c: CentralClient, resource_id: str):
    return c.call("GET", M + "server_activity", params={"resource_id": resource_id})
