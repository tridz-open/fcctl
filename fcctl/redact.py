"""Single redaction helper for diagnostic sinks (errors, --debug output, audit, dry-run, logs, rendered journal).

Normal data output (`ctx.render`) is deliberately NOT redacted; the journal file is stored un-redacted.

`redact(x)` masks secrets in text and, structurally, in dicts/lists/tuples. It is idempotent.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

REDACTED = "[REDACTED]"
MASK = "***"
ENV_SECRETS = ("FCCTL_KEY", "FCCTL_APPROVER_KEY", "FCCTL_APPROVER_PRIVATE_KEY", "FCCTL_APPROVAL_SECRET")

# dict keys whose whole value is masked
SECRET_KEY_RE = re.compile(
    r"^key$"                                                   # a bare `key`
    r"|^(?:secret|password|passwd|api_?key|authorization|cookie|csrf|totp|otp|sid)"      # leading word
    r"|(?:^|[_.-])(?:secret|token|password|passwd|api_?key|private_key|encryption_key|"
    r"authorization|cookie|csrf|totp|otp|sid)s?$",              # trailing word (token_count does not match)
    re.I)

_KW = r"(?:[\w.-]*(?:token|password|passwd|secret|api_?key|totp|encryption_key|private_key)[\w.-]*|otp|sid)"
_HDR = r"(?:proxy-authorization|authorization|set-cookie|cookie|x-frappe-csrf-token)"

_PATTERNS = [
    # header-ish "Name: value" (to end of line, or to the closing quote when quoted)
    (re.compile(rf"""(?i)((?<![\w-])["']?{_HDR}["']?\s*[:=]\s*)(["']?)([^"'\r\n]+)"""),
     lambda m: m.group(1) + m.group(2) + MASK),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), lambda m: "Bearer " + MASK),
    (re.compile(r"gAAAA[A-Za-z0-9_=-]{16,}"), lambda m: MASK),
    # key=value / key: value / "key": "value" / key=value in query strings
    (re.compile(rf"""(?i)((?<![\w])["']?{_KW}["']?\s*[:=]\s*)(["']?)([^\s"',;&}}\])]+)"""),
     lambda m: m.group(1) + m.group(2) + MASK),
]


def _env_values() -> list[str]:
    vals = []
    for name in ENV_SECRETS:
        v = os.environ.get(name)
        if v and len(v) >= 4:
            vals.append(v)
    return sorted(vals, key=len, reverse=True)


def redact_text(text: Any) -> str:
    s = text if isinstance(text, str) else str(text)
    for v in _env_values():
        s = s.replace(v, MASK)
    for rx, fn in _PATTERNS:
        s = rx.sub(fn, s)
    return s


def redact(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: (REDACTED if SECRET_KEY_RE.search(str(k)) else redact(x)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [redact(x) for x in v]
    if isinstance(v, str):
        return redact_text(v)
    if isinstance(v, bytes):
        return redact_text(v.decode("utf-8", "replace"))
    return v


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_text(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001
            pass
        return True


def install_debug_logging() -> None:
    """--debug: httpx/httpcore request/response debug text goes to stderr, redacted."""
    h = logging.StreamHandler()
    h.setFormatter(RedactingFormatter("%(name)s: %(message)s"))
    h.addFilter(RedactingFilter())
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.handlers[:] = [h]
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
