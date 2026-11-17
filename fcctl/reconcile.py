"""reconcile: resolve ambiguous journal entries from REMOTE state only.

A resolver is `callable(entry) -> "succeeded" | "failed" | "not_happened" | None` (None = cannot tell),
or a (state, result) tuple. Resubmission happens only when the resolver proves "not_happened" AND the op's
idempotency class allows safe resend AND a `resubmit` callable is supplied.
"""
from __future__ import annotations

from . import journal as J

AMBIGUOUS = {J.DISPATCHING, J.UNKNOWN}


def reconcile(journal: J.Journal, resolvers: dict, ttl: float = 300, resubmit=None, now=None) -> dict:
    t = (now or journal.now)()
    report = {"succeeded": [], "failed": [], "unresolved": [], "resubmitted": [], "skipped": []}
    for e in journal.list(AMBIGUOUS):
        if e["state"] == J.DISPATCHING and t - e["updated_at"] < ttl:
            report["skipped"].append(e["id"])
            continue
        res = resolvers.get(e["op"])
        if res is None:
            report["unresolved"].append(e["id"])
            _park(journal, e)
            continue
        try:
            out = res(e)
        except Exception:
            out = None
        result = None
        if isinstance(out, tuple):
            out, result = out
        if out == "succeeded":
            _settle(journal, e, J.SUCCEEDED, result)
            report["succeeded"].append(e["id"])
        elif out == "failed":
            _settle(journal, e, J.FAILED, result)
            report["failed"].append(e["id"])
        elif out == "not_happened" and e["idempotency"] in J.SAFE_RESEND and resubmit is not None:
            _park(journal, e)
            journal.transition(e["id"], J.DISPATCHING)
            try:
                r = resubmit(journal.get(e["id"]))
            except Exception as exc:
                journal.transition(e["id"], J.UNKNOWN, error=f"{type(exc).__name__}: {exc}")
                report["unresolved"].append(e["id"])
                continue
            journal.transition(e["id"], J.SUBMITTED, handle=r.get("handle") if isinstance(r, dict) else None)
            report["resubmitted"].append(e["id"])
        else:  # undetermined, or not_happened but resend unsafe: human decides
            report["unresolved"].append(e["id"])
            _park(journal, e)
    return report


def _park(journal, e):
    if e["state"] == J.DISPATCHING:
        journal.transition(e["id"], J.UNKNOWN, error=e.get("error") or "interrupted while dispatching")


def _settle(journal, e, state, result):
    if e["state"] == J.DISPATCHING:
        journal.transition(e["id"], J.UNKNOWN, error="interrupted while dispatching")
    journal.transition(e["id"], state, result=result)


def pid_alive(pid) -> bool:
    import os
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError):
        return True
    return True


def clear_orphan_markers(guard, journal: J.Journal, now=None) -> dict:
    """Clear `.executing` markers whose PID is dead or TTL expired, only once the journal entry is resolved.
    Approvals are never reset or re-armed: status is set executed/failed from the journal, or left alone."""
    t = now if now is not None else guard.now()
    out = {"cleared": [], "kept": []}
    for m in guard.markers():
        orphan = not pid_alive(m.get("pid")) or t > m.get("ts", 0) + guard_ttl(guard)
        if not orphan:
            out["kept"].append(m["approval_id"])
            continue
        try:
            e = journal.get(m["journal_id"])
        except FileNotFoundError:
            out["kept"].append(m["approval_id"])
            continue
        if e["state"] in J.TERMINAL:
            guard.finish_approval(m["approval_id"], "executed" if e["state"] == J.SUCCEEDED else "failed",
                                  e.get("result") if e["state"] == J.SUCCEEDED else e.get("error"))
            guard.remove_marker(m["approval_id"])
            out["cleared"].append(m["approval_id"])
        else:
            out["kept"].append(m["approval_id"])
    return out


def guard_ttl(guard):
    from .guard import TTL
    return 2 * TTL


# ---- default resolvers (remote state only) -------------------------------------------------------

_DONE, _BAD = {"success", "succeeded", "completed", "done"}, {"failed", "killed", "error", "cancelled"}


def _arg(e, *names):
    a = e.get("args") or {}
    for n in names:
        if a.get(n) not in (None, ""):
            return a[n]
    return None


def _is_404(exc) -> bool:
    from .errors import NotFound
    return isinstance(exc, NotFound) or getattr(exc, "status", None) == 404


def _find_task(pilot, kinds, target, entry):
    """Newest Pilot task of one of `kinds` for `target` created at/after the journal entry (when stamped)."""
    r = pilot.get("/tasks")
    items = r.get("tasks", []) if isinstance(r, dict) else (r or [])
    hits = []
    for t in items:
        ty = str(t.get("type") or t.get("name") or t.get("kind") or "")
        if not any(k in ty for k in kinds):
            continue
        tt = t.get("site") or (t.get("args") or {}).get("site") or (t.get("args") or {}).get("name")
        if target is not None and tt is not None and tt != target:
            continue
        hits.append(t)
    return hits[-1] if hits else None


def default_resolvers(central_factory, pilot_factory) -> dict:
    """Build {op: resolver}. central_factory() -> CentralClient; pilot_factory(entry) -> PilotClient | None.
    Each resolver returns succeeded | failed | not_happened | None (cannot tell)."""
    def task_resolver(kinds, arg_names=("site", "name", "source")):
        def r(e):
            p = pilot_factory(e)
            if p is None:
                return None
            t = _find_task(p, kinds, _arg(e, *arg_names), e)
            if t is None:
                return "not_happened"
            st = str(t.get("status", "")).lower()
            res = {"task_id": t.get("task_id") or t.get("id")}
            if st in _DONE:
                return "succeeded", res
            if st in _BAD:
                return "failed", res
            return None  # still running: leave it
        return r

    def create_server(e):
        from . import servers
        key, title = _arg(e, "request_key") or e.get("idem_key"), _arg(e, "title", "name")
        lst = servers.list_servers(central_factory())
        for s in lst:
            if key and s.get("request_key") == key:
                return "succeeded", {"resource_id": s.get("resource_id")}
        for s in lst:  # registry does not expose request_key on every deployment
            if title and not any("request_key" in x for x in lst) and s.get("title") == title:
                return "succeeded", {"resource_id": s.get("resource_id")}
        return "not_happened"

    def drop_site(e):
        p = pilot_factory(e)
        site = _arg(e, "site", "name")
        if p is None or not site:
            return None
        try:
            p.get(f"/sites/{site}")
        except Exception as exc:
            return "succeeded" if _is_404(exc) else None
        return "not_happened"

    def delete_server(e):
        from . import servers
        rid = _arg(e, "resource_id", "server", "name")
        if not rid:
            return None
        return "not_happened" if any(s.get("resource_id") == rid for s in servers.list_servers(central_factory())) \
            else "succeeded"

    def bucket_create(e):
        from . import objects
        name = _arg(e, "bucket_name", "name")
        if not name:
            return None
        for b in objects.list_buckets(central_factory()):
            if name in (b.get("name"), b.get("bucket_name")):
                return "succeeded", {"name": name}
        return "not_happened"

    def bucket_delete(e):
        from . import objects
        name = _arg(e, "bucket_name", "name")
        if not name:
            return None
        return "not_happened" if any(name in (b.get("name"), b.get("bucket_name"))
                                     for b in objects.list_buckets(central_factory())) else "succeeded"

    m = {
        "create_server": create_server, "server.create": create_server,
        "drop_site": drop_site, "site.drop": drop_site,
        "delete_server": delete_server, "server.terminate": delete_server,
        "create_bucket": bucket_create, "bucket.create": bucket_create,
        "delete_bucket": bucket_delete, "bucket.delete": bucket_delete,
    }
    for names, kinds in ((("create_site", "site.create"), ("site_create", "create_site")),
                         (("create_backup", "backup.create"), ("backup",)),
                         (("restore_backup", "backup.restore"), ("restore",)),
                         (("migrate", "site.migrate"), ("migrate",))):
        for n in names:
            m[n] = task_resolver(kinds)
    return m
