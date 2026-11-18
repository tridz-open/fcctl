"""Profile policy resolution and operation classification."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .profiles import Profile

RESTRICTIVENESS = {"full": 0, "guarded": 1, "read_only": 2, "protected": 3}

# op_id -> kind (read|write|cost|disruptive|destructive); cost = write that changes spend (needs --confirm when guarded), plane (central|pilot), method
def _o(kind: str, plane: str, method: str) -> dict[str, Any]:
    return {"kind": kind, "plane": plane, "method": method}

REGISTRY: dict[str, dict[str, Any]] = {
    # servers (central)
    "server.list": _o("read", "central", "GET"), "server.overview": _o("read", "central", "GET"),
    "server.create": _o("write", "central", "POST"),
    "server.resize": _o("cost", "central", "POST"), "server.set_termination_protection": _o("write", "central", "POST"),
    "server.terminate": _o("destructive", "central", "POST"),
    # snapshots
    "snapshot.list": _o("read", "central", "GET"), "snapshot.take": _o("write", "central", "POST"),
    "snapshot.delete": _o("destructive", "central", "POST"),
    # objects / buckets
    "bucket.get": _o("read", "central", "GET"), "objects.list": _o("read", "central", "GET"),
    "bucket.usage": _o("read", "central", "GET"), "objects.get_url": _o("read", "central", "GET"),
    "bucket.create": _o("write", "central", "POST"), "objects.upload_url": _o("write", "central", "POST"),
    "objects.upload": _o("write", "central", "PUT"), "bucket.set_quota": _o("write", "central", "POST"),
    "bucket.delete": _o("destructive", "central", "POST"), "objects.delete": _o("destructive", "central", "POST"),
    # billing / monitor / alerts
    "billing.eligible_plans": _o("read", "central", "GET"), "billing.trust_tier": _o("read", "central", "GET"),
    "monitor.central_metrics": _o("read", "central", "GET"), "monitor.metrics": _o("read", "pilot", "GET"),
    "monitor.system": _o("read", "pilot", "GET"), "monitor.health": _o("read", "pilot", "GET"),
    # logs
    "logs.list": _o("read", "pilot", "GET"), "logs.content": _o("read", "pilot", "GET"),
    "logs.tail": _o("read", "pilot", "GET"), "logs.task_events": _o("read", "pilot", "GET"),
    # sites (pilot)
    "site.list": _o("read", "pilot", "GET"), "site.get": _o("read", "pilot", "GET"),
    "site.wildcard_domains": _o("read", "pilot", "GET"),
    "site.create": _o("write", "pilot", "POST"), "site.clone": _o("write", "pilot", "POST"),
    "site.install_app": _o("write", "pilot", "POST"), "site.set_maintenance": _o("write", "pilot", "POST"),
    "site.clear_cache": _o("write", "pilot", "POST"), "site.migrate": _o("write", "pilot", "POST"),
    "site.build_assets": _o("write", "pilot", "POST"), "site.login_link": _o("write", "pilot", "POST"),
    "site.rename": _o("write", "pilot", "POST"),
    "site.uninstall_app": _o("destructive", "pilot", "POST"), "site.drop": _o("destructive", "pilot", "DELETE"),
    # apps (pilot)
    "app.list": _o("read", "pilot", "GET"), "app.check_updates": _o("write", "pilot", "POST"),
    "app.add_marketplace": _o("write", "pilot", "POST"), "app.add_repo": _o("write", "pilot", "POST"),
    "app.add_in_order": _o("write", "pilot", "POST"), "app.update": _o("write", "pilot", "POST"),
    "app.switch_branch": _o("destructive", "pilot", "POST"), "app.remove": _o("destructive", "pilot", "DELETE"),
    # backups (pilot)
    "backup.list": _o("read", "pilot", "GET"), "backup.get": _o("read", "pilot", "GET"),
    "backup.download_links": _o("read", "pilot", "GET"), "backup.download_file": _o("read", "pilot", "GET"),
    "backup.get_schedule": _o("read", "pilot", "GET"),
    "backup.create": _o("write", "pilot", "POST"), "backup.set_schedule": _o("write", "pilot", "PUT"),
    "backup.restore": _o("destructive", "pilot", "POST"),
    # tasks
    "task.cancel": _o("destructive", "pilot", "POST"),
    # --- v2 additions
    "activity.server": _o("read", "central", "GET"), "activity.team": _o("read", "central", "GET"),
    "backup.offsite_list": _o("read", "pilot", "GET"),
    "backup.offsite_download": _o("destructive", "pilot", "POST"),  # overwrites local target
    "backup.restore_upload": _o("destructive", "pilot", "POST"),
    "billing.health": _o("read", "central", "GET"), "billing.invoice_get": _o("read", "central", "GET"),
    "billing.invoices_list": _o("read", "central", "GET"), "billing.ledger": _o("read", "central", "GET"),
    "billing.overview": _o("read", "central", "GET"), "billing.subscriptions": _o("read", "central", "GET"),
    "billing.wallet_balance": _o("read", "central", "GET"),
    "domain.dns_records": _o("read", "pilot", "GET"), "domain.list": _o("read", "pilot", "GET"),
    "infra.images": _o("read", "central", "GET"), "infra.offerings": _o("read", "central", "GET"),
    "infra.plans_eligible": _o("read", "central", "GET"),
    "notification.badge": _o("read", "central", "GET"), "notification.list": _o("read", "central", "GET"),
    "notification.read": _o("write", "central", "POST"), "notification.read_all": _o("write", "central", "POST"),
    "server.activity": _o("read", "central", "GET"), "server.hostnames": _o("read", "central", "GET"),
    "server.start": _o("write", "central", "POST"),
    "server.restart": _o("disruptive", "central", "POST"), "server.stop": _o("disruptive", "central", "POST"),
    "site.apps_list": _o("read", "pilot", "GET"), "site.config_get": _o("read", "pilot", "GET"),
    "site.config_set": _o("write", "pilot", "POST"),
    "task.output": _o("read", "pilot", "GET"),
    "workflow.provision_site": _o("write", "pilot", "POST"),
    "fleet.status": _o("read", "central", "GET"),
}

DESTRUCTIVE_RE = re.compile(
    r"terminate|delete|drop|archive|remove|revoke|transfer|restore|stop_server|uninstall|switch-branch", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class NeedsConfirm:
    pass


@dataclass(frozen=True)
class NeedsApproval:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


Decision = Allow | NeedsConfirm | NeedsApproval | Deny


@dataclass(frozen=True)
class EffectivePolicy:
    login: str
    team: str
    policy: str
    allow: frozenset[str] = frozenset()
    warnings: tuple[str, ...] = ()
    profiles: tuple[str, ...] = ()


def resolve_policy(login: str, team: str, profiles: list[Profile]) -> EffectivePolicy:
    """Most restrictive policy wins; allow lists intersect; unknown pair -> read_only."""
    m = [p for p in profiles if p.login == login and p.team == team]
    if not m:
        return EffectivePolicy(login, team, "read_only",
                               warnings=(f"no profile for ({login}, {team}); treated as read_only",))
    policy = max((p.policy for p in m), key=RESTRICTIVENESS.__getitem__)
    prot = [set(p.allow) for p in m if p.policy == "protected"]
    allow = frozenset(set.intersection(*prot)) if prot else frozenset()
    warns = []
    if len(m) > 1:
        names = ", ".join(p.name for p in m)
        warns.append(f"conflicting profiles for ({login}, {team}): {names}; "
                     f"most restrictive policy {policy!r} applies")
    return EffectivePolicy(login, team, policy, allow, tuple(warns), tuple(p.name for p in m))


READ_METHODS = ("GET", "HEAD", "OPTIONS")

# Allowlist of path regexes for known-safe, read-style non-GET raw calls (e.g. POST RPCs that only
# read). Intentionally empty: every registry entry with a `path` regex is already classified by
# classify_raw, and nothing else is known safe. Anything unclassified and non-read needs approval
# under `guarded` and `protected` (`fcctl approval approve <id>`); `--yes`/`--confirm` never bypass it.
SAFE_RAW_NON_GET: tuple[str, ...] = ()


def classify_raw(method: str, path: str) -> dict[str, Any] | None:
    """Match a raw call to a registry entry that declares a `path` regex; else None (unclassified)."""
    method = method.upper()
    for op_id, meta in REGISTRY.items():
        pat = meta.get("path")
        if pat and meta["method"] == method and re.search(pat, path):
            return {"op_id": op_id, **meta}
    return None


RISKY_GET_RE = re.compile(r"drop|delete|restore|archive", re.I)
_METHOD_PREFIX = "/api/method/"


def raw_get_is_risky(plane: str | None, path: str) -> bool:
    """An unclassified read-method raw call that must be treated as a write.

    Frappe whitelisted methods accept GET, so on Central any `/api/method/<x>` (or bare dotted
    method) is a write unless `<x>` is a registered read op. On Pilot, paths naming a destructive
    verb (drop, delete, restore, archive) are write-like.
    """
    if plane == "central":
        name = path[len(_METHOD_PREFIX):] if path.startswith(_METHOD_PREFIX) else path
        meta = REGISTRY.get(name)
        return not (meta and meta["kind"] == "read")
    return bool(RISKY_GET_RE.search(path))


def check(op: str | tuple[str, str], eff: EffectivePolicy, registry: dict[str, dict[str, Any]] | None = None,
          plane: str | None = None) -> Decision:
    """op: registry op id (str) or raw (method, path). `plane` refines unclassified read-method calls."""
    reg = REGISTRY if registry is None else registry
    raw = False
    op_id = None
    if isinstance(op, str):
        meta = reg.get(op)
        if meta is None:
            return Deny(f"unknown operation {op!r}")
        op_id, kind, method, path = op, meta["kind"], meta["method"], op
    else:
        method, path = op[0].upper(), op[1]
        raw = True
        meta = classify_raw(method, path) if registry is None else None
        if meta:
            op_id, kind = meta["op_id"], meta["kind"]
        else:
            kind = None  # unclassified
    pol = eff.policy
    if kind is None and method in READ_METHODS and plane is not None:
        if not raw_get_is_risky(plane, path):
            return Allow()  # plain safe read
        method = "POST"  # risky read-method call: treat as an unclassified write
    if kind is None:  # unclassified raw
        if pol == "read_only" or (pol == "protected" and method in READ_METHODS):
            return Deny(f"unclassified raw operation {method} {path} rejected under {pol}")
        if method == "DELETE" or DESTRUCTIVE_RE.search(path):
            return NeedsApproval()
        if pol in ("guarded", "protected") and method not in READ_METHODS:
            if any(re.search(p, path) for p in SAFE_RAW_NON_GET):
                return Allow()
            return NeedsApproval()  # unknown write: fail closed to the two-person approval flow
        return Allow()
    is_write = kind in ("write", "cost", "disruptive", "destructive")
    if is_write:
        if pol == "read_only":
            return Deny("read_only profile blocks writes")
        if pol == "protected" and op_id not in eff.allow:
            return Deny(f"protected profile blocks writes; {op_id!r} not in allow list")
    if pol in ("read_only", "protected"):
        return Allow()
    if kind == "destructive":
        return NeedsApproval()
    if kind in ("disruptive", "cost") and pol == "guarded":
        return NeedsConfirm()
    if raw and is_write and pol == "guarded" and method in ("POST", "PUT", "PATCH"):
        return NeedsConfirm()
    if raw and pol == "guarded" and (method == "DELETE" or DESTRUCTIVE_RE.search(path)):
        return NeedsApproval()
    return Allow()
