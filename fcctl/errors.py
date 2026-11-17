"""Typed errors with what/why/fix, stable exit codes, redaction and 3-line formatting."""
from __future__ import annotations

import re
from typing import Any


class FcError(Exception):
    exit_code = 1

    def __init__(self, message: str = "", status: int | None = None, body: Any = None, *,
                 what: str | None = None, why: str | None = None, fix: str | None = None,
                 request_id: str | None = None, cf_ray: str | None = None, code: str | None = None,
                 op: str | None = None, profile: str | None = None):
        super().__init__(message)
        self.status, self.body = status, body
        self.what, self.why, self.fix = what, why, fix
        self.request_id, self.cf_ray, self.code = request_id, cf_ray, code
        self.op, self.profile = op, profile


class AuthError(FcError): exit_code = 3
class CsrfError(FcError): exit_code = 3
class ReauthFailed(AuthError): ...
class CloudflareChallenge(FcError): exit_code = 10
class NotFound(FcError): exit_code = 5
class ApiError(FcError): exit_code = 1
class TaskConflict(FcError): exit_code = 6
class TaskFailed(FcError): exit_code = 9
class PolicyDenied(FcError): exit_code = 4
class ApprovalPending(FcError): exit_code = 7
class WaitTimeout(FcError): exit_code = 8
class RateLimited(FcError): exit_code = 11
class ValidationError(FcError): exit_code = 12
class OutcomeUnknown(FcError): exit_code = 13
class Interrupted(FcError): exit_code = 130
class ScopeMismatch(FcError): exit_code = 4
class NameConflict(FcError): exit_code = 6
class QuotaExceeded(FcError): exit_code = 11
class FeatureDisabled(FcError): exit_code = 4
class ApiDrift(FcError): exit_code = 1
class UsageError(FcError): exit_code = 2
class DiskShrinkRefused(FcError): exit_code = 12  # same family as ValidationError


_REDACTIONS = [
    (re.compile(r"(?i)\bsid=[^;\s&\"',]+"), "sid=***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)(x-frappe-csrf-token|csrf[_-]?token)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"),
     r"\1\2***"),
]


def redact(text: Any) -> str:
    s = str(text)
    for rx, rep in _REDACTIONS:
        s = rx.sub(rep, s)
    return s


def format_error(e: BaseException) -> str:
    what = getattr(e, "what", None) or str(e) or type(e).__name__
    why = getattr(e, "why", None) or type(e).__name__
    fix = getattr(e, "fix", None) or "Retry with --debug, or run `fcctl doctor`."
    lines = [f"error: {what}", f"why: {why}", f"fix: {fix}"]
    ids = []
    if getattr(e, "request_id", None):
        ids.append(f"request_id={e.request_id}")
    if getattr(e, "cf_ray", None):
        ids.append(f"cf-ray={e.cf_ray}")
    if ids:
        lines.append(" ".join(ids))
    return redact("\n".join(l.replace("\n", " ") for l in lines))


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, KeyboardInterrupt):
        return 130
    if isinstance(exc, FcError):
        return exc.exit_code
    try:
        from . import guard
    except Exception:  # pragma: no cover
        guard = None
    if guard is not None and isinstance(exc, guard.GuardError):
        return 7 if isinstance(exc, guard.NotPending) or type(exc) is guard.GuardError else 4
    return 1
