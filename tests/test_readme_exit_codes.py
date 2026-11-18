"""The README exit-code table must match fcctl.errors."""
import inspect
import re
from pathlib import Path

from fcctl import errors, guard

README = (Path(__file__).resolve().parent.parent / "README.md").read_text()


def _table():
    sec = README.split("### Exit codes", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    rows = re.findall(r"^\| (\d+) \| (.+?) \|$", sec, re.M)
    return {int(c): m for c, m in rows}


def test_table_codes_equal_code_codes():
    codes = {0, 130}
    for _, cls in inspect.getmembers(errors, inspect.isclass):
        if issubclass(cls, errors.FcError):
            codes.add(cls.exit_code)
    codes |= {7, 4}  # guard errors map through exit_code_for
    assert set(_table()) == codes


def test_exit_code_for_values_are_documented():
    table = _table()
    samples = [KeyboardInterrupt(), errors.AuthError(), errors.PolicyDenied(), errors.NotFound(),
               errors.TaskConflict(), errors.ApprovalPending(), errors.WaitTimeout(), errors.TaskFailed(),
               errors.CloudflareChallenge(), errors.RateLimited(), errors.ValidationError(),
               errors.OutcomeUnknown(), errors.UsageError(), errors.ApiError(), guard.GuardError("x"),
               RuntimeError("boom")]
    for e in samples:
        assert errors.exit_code_for(e) in table


def test_key_meanings():
    t = _table()
    assert "approval" in t[7].lower() and "cloudflare" in t[10].lower() and "interrupt" in t[130].lower()
    assert "unknown" in t[13].lower() and "timeout" in t[8].lower()
