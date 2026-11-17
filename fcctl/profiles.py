"""Profile model loaded from config.toml. A profile is bound to its team."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

POLICIES = ("full", "guarded", "read_only", "protected")


@dataclass(frozen=True)
class Profile:
    name: str
    login: str
    team: str
    policy: str = "guarded"
    allow: tuple[str, ...] = field(default_factory=tuple)
    session_ref: str | None = None
    owner: str | None = None
    owner_id: str | None = None
    session_store: str | None = None

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise ValueError(f"profile {self.name!r}: invalid policy {self.policy!r}")


def parse_profiles(data: dict) -> list[Profile]:
    out = []
    for name, p in (data.get("profiles") or {}).items():
        login = p.get("email") or p.get("login")
        if not login or not p.get("team"):
            raise ValueError(f"profile {name!r}: email/login and team are required")
        out.append(Profile(name, login, p["team"], p.get("policy", "guarded"), tuple(p.get("allow", ())),
                           p.get("session_ref"), p.get("owner"), p.get("owner_id"), p.get("session_store")))
    return out


def load_profiles(path: str | Path) -> list[Profile]:
    with open(path, "rb") as f:
        return parse_profiles(tomllib.load(f))
