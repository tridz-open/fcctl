"""`fcctl api` raw calls."""
from __future__ import annotations

import re

from . import policy as pol
from .ops import OpContext, execute_decision
from .request import FcError


RAW_OP = "api.raw"  # guard-registry op id that replays an approved unclassified raw call


class ScopeMismatch(FcError):
    exit_code = 4


ID_RE = re.compile(r"/(?:servers?|sites?)/([^/?#]+)")


def classify(method: str, path_or_id: str) -> dict | None:
    """Registry meta for an op id or a raw (method, path); None when unclassified."""
    meta = pol.REGISTRY.get(path_or_id)
    if meta is not None:
        return {"op_id": path_or_id, **meta}
    return pol.classify_raw(method, path_or_id)


def replay_raw(client, method, path, body=None):
    """Executed by Guard.approve for an approved `api.raw` request (same method/path/body)."""
    if hasattr(client, "request"):
        return client.request(method, path, json=body)
    return client.call(method, path, json=body)


def call_raw(ctx: OpContext, plane: str, method: str, path: str, body=None, confirm=None,
             guarded: bool = False, client=None):
    method = method.upper()
    meta = classify(method, path)
    if ctx.team_membership is not None:
        for sid in ID_RE.findall(path):
            ctx.team_membership(sid)  # raises ScopeMismatch-like error on foreign ids
    is_read = method == "GET" or (meta is not None and meta["kind"] == "read")
    label = meta["op_id"] if meta else RAW_OP
    if is_read:
        decision: pol.Decision = pol.Allow()
    else:
        decision = pol.check((method, path), ctx.eff)
        if guarded and not isinstance(decision, pol.Deny):
            decision = pol.NeedsApproval()
    from dataclasses import replace
    c = replace(ctx, kind=plane, args={"method": method, "path": path, "body": body},
                confirm=confirm if confirm is not None else ctx.confirm)
    client = client if client is not None else getattr(ctx, "clients", {}).get(plane)

    def fn():
        if hasattr(client, "request"):
            return client.request(method, path, json=body)
        return client.call(method, path, json=body)

    return execute_decision(decision, label, f"{method} {path}", fn, c, is_read=is_read,
                            journal_op=label)
