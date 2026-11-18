"""Typed errors with what/why/fix, stable exit codes, redaction and 3-line formatting."""
from __future__ import annotations

import re
from typing import Any


GENERIC_FIX = "Retry with --debug, or run `fcctl doctor`."
KEY_CMD = 'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'


class FcError(Exception):
    exit_code = 1
    default_why = "Frappe Cloud returned an error that fcctl has no more specific explanation for."
    default_fix = "Re-run with --debug to see the raw response, then check `fcctl doctor`."

    def __init__(self, message: str = "", status: int | None = None, body: Any = None, *,
                 what: str | None = None, why: str | None = None, fix: str | None = None,
                 request_id: str | None = None, cf_ray: str | None = None, code: str | None = None,
                 op: str | None = None, profile: str | None = None):
        super().__init__(message)
        self.status, self.body = status, body
        self.what = what
        self.why = why or self.default_why
        self.fix = fix or self.default_fix
        self.request_id, self.cf_ray, self.code = request_id, cf_ray, code
        self.op, self.profile = op, profile


class AuthError(FcError):
    exit_code = 3
    default_why = 'The Frappe Cloud session is missing, expired or was rejected.'
    default_fix = 'Sign in again with `fcctl auth login`, then retry.'


class CsrfError(FcError):
    exit_code = 3
    default_why = 'The CSRF token paired with the session is missing or stale.'
    default_fix = 'Refresh the session with `fcctl auth login`, then retry.'


class ReauthFailed(AuthError):
    default_why = 'The session expired and every automatic re-login step failed.'
    default_fix = 'Run `fcctl auth login` interactively to create a fresh session.'


class CloudflareChallenge(FcError):
    exit_code = 10
    default_why = 'Cloudflare served a bot challenge instead of the Frappe Cloud API response.'
    default_fix = 'Wait a few minutes and retry; if it persists, sign in from a browser on this network first.'


class NotFound(FcError):
    exit_code = 5
    default_why = 'No resource with that name or id exists for this team, or the profile cannot see it.'
    default_fix = 'Check the name with the matching `list` command and confirm you are using the right --profile.'


class ApiError(FcError):
    exit_code = 1
    default_why = 'The Frappe Cloud API rejected the request or failed server-side.'
    default_fix = 'Re-run with --debug to see the response body and request id; retry later if it is a 5xx.'


class TaskConflict(FcError):
    exit_code = 6
    default_why = 'Another task is already running on the same resource, so this one cannot start.'
    default_fix = 'Wait for the running task (`fcctl task list`) to finish, then retry.'


class TaskFailed(FcError):
    exit_code = 9
    default_why = 'The Frappe Cloud job ran and finished with a failure status.'
    default_fix = 'Read the job output with `fcctl task output <id>`, fix the cause, then retry.'


class PolicyDenied(FcError):
    exit_code = 4
    default_why = "The active profile's policy does not allow this operation."
    default_fix = "Use a profile whose policy allows it (`fcctl profile list`), or extend that profile's allow list."


class ApprovalPending(FcError):
    exit_code = 7
    default_why = "This operation needs a second person's approval before it can run."
    default_fix = 'Have an approver run `fcctl approval approve <id>` (not the requester), then re-run the command.'


class WaitTimeout(FcError):
    exit_code = 8
    default_why = 'The --wait/--timeout limit elapsed before the task reached a final state; it may still be running.'
    default_fix = 'Check progress with `fcctl task get <id>`, or re-run with a larger --timeout.'


class RateLimited(FcError):
    exit_code = 11
    default_why = 'Frappe Cloud is throttling requests from this account.'
    default_fix = 'Wait a minute and retry, or lower --parallel.'


class ValidationError(FcError):
    exit_code = 12
    default_why = 'Frappe Cloud rejected one of the supplied values as invalid.'
    default_fix = 'Fix the argument named in the message and re-run; `--help` shows allowed values.'


class OutcomeUnknown(FcError):
    exit_code = 13
    default_why = 'The request was sent but the connection broke before any result came back, so it may or may not have applied.'
    default_fix = 'Do not blindly retry: inspect the resource with `fcctl task list` or the matching `show` command first.'


class Interrupted(FcError):
    exit_code = 130
    default_why = 'The command was interrupted (Ctrl-C or SIGTERM) before it finished.'
    default_fix = 'Check whether a remote task is still running with `fcctl task list`, then re-run if needed.'


class ScopeMismatch(FcError):
    exit_code = 4
    default_why = 'The target is outside the team or sites this profile is scoped to.'
    default_fix = 'Switch to the profile that owns the target (`fcctl profile use <name>`).'


class NameConflict(FcError):
    exit_code = 6
    default_why = 'A resource with that name already exists.'
    default_fix = 'Pick a different name, or operate on the existing resource instead.'


class QuotaExceeded(FcError):
    exit_code = 11
    default_why = 'The account has hit a plan or resource quota.'
    default_fix = 'Free up resources or raise the plan in the Frappe Cloud dashboard, then retry.'


class FeatureDisabled(FcError):
    exit_code = 4
    default_why = 'This feature is not enabled for the team or plan.'
    default_fix = 'Enable it in the Frappe Cloud dashboard or contact Frappe support.'


class ApiDrift(FcError):
    exit_code = 1
    default_why = 'The API response shape no longer matches what this fcctl version expects.'
    default_fix = 'Upgrade fcctl (`pip install -U fcctl`); if it persists, report it with the --debug output.'


class UsageError(FcError):
    exit_code = 2
    default_why = 'The command line or call arguments are incomplete or inconsistent.'
    default_fix = 'Run the command with --help and correct the arguments.'


class DiskShrinkRefused(FcError):
    exit_code = 12
    default_why = 'Disks cannot be shrunk; the requested size is smaller than the current one.'
    default_fix = 'Request a size at or above the current disk size.'


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
    why = getattr(e, "why", None) or getattr(type(e), "default_why", None)
    fix = getattr(e, "fix", None) or getattr(type(e), "default_fix", None)
    if not why:  # truly unknown exception
        why = f"unexpected {type(e).__name__}: not a failure fcctl anticipated"
    if not fix:
        fix = GENERIC_FIX
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
