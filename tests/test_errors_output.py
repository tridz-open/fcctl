import dataclasses
import io
import json

import httpx
import pytest

from fcctl import errors as E
from fcctl import guard
from fcctl.output import default_format, render
from fcctl.request import FcError, raise_for_central, raise_for_pilot, TaskConflict, AuthError


def test_backcompat_and_codes():
    e = FcError("m", 500, {"a": 1})
    assert (e.status, e.body, e.exit_code, str(e)) == (500, {"a": 1}, 1, "m")
    assert E.exit_code_for(AuthError()) == 3 and E.exit_code_for(E.PolicyDenied()) == 4
    assert E.exit_code_for(KeyboardInterrupt()) == 130 and E.exit_code_for(ValueError()) == 1
    for cls, c in [(E.NotFound, 5), (E.TaskConflict, 6), (E.ApprovalPending, 7), (E.WaitTimeout, 8),
                   (E.TaskFailed, 9), (E.CloudflareChallenge, 10), (E.RateLimited, 11),
                   (E.ValidationError, 12), (E.OutcomeUnknown, 13), (E.Interrupted, 130), (E.UsageError, 2)]:
        assert cls().exit_code == c


def test_guard_codes():
    assert E.exit_code_for(guard.SelfApproval()) == 4
    assert E.exit_code_for(guard.GuardError()) == 7


@pytest.mark.parametrize("status,cls", [(429, E.RateLimited), (422, E.ValidationError),
                                        (417, E.ValidationError), (500, E.ApiError)])
def test_raise_mapping(status, cls):
    h = {"x-request-id": "rq1", "cf-ray": "ray1", "content-type": "application/json"}
    r = httpx.Response(status, json={"exc_type": "X"}, headers=h)
    for fn in (raise_for_central, raise_for_pilot):
        with pytest.raises(cls) as ei:
            fn(r)
        assert ei.value.request_id == "rq1" and ei.value.cf_ray == "ray1"


def test_409_conflict_central():
    with pytest.raises(TaskConflict):
        raise_for_central(httpx.Response(409, json={"exc_type": "X"}))


def test_redact_and_format():
    t = "Cookie: sid=abc123; Authorization: Bearer eyJ.a-b_c x-frappe-csrf-token: tok9 csrf_token=zz"
    out = E.redact(t)
    for s in ("abc123", "eyJ.a-b_c", "tok9", "zz"):
        assert s not in out
    e = E.NotFound("gone sid=secret", what="Site missing", why="no such site", fix="check name",
                   request_id="r1", cf_ray="c1")
    lines = E.format_error(e).splitlines()
    assert len(lines) == 4 and lines[3] == "request_id=r1 cf-ray=c1" and "secret" not in "".join(lines)
    assert len(E.format_error(E.FcError("x")).splitlines()) == 3


@dataclasses.dataclass
class D:
    a: int
    b: str


def test_render_formats():
    rows = [{"name": "s1", "n": 1}, {"name": "site-two", "n": None}]
    s = io.StringIO(); render(rows, "table", s)
    L = s.getvalue().splitlines()
    assert L[0].startswith("NAME") and "site-two" in L[2]
    s = io.StringIO(); render(rows, "table", s, columns=["n"]); assert "NAME" not in s.getvalue()
    s = io.StringIO(); render(rows, "json", s); assert json.loads(s.getvalue()) == rows
    s = io.StringIO(); render(rows, "ndjson", s)
    assert [json.loads(l) for l in s.getvalue().splitlines()] == rows
    s = io.StringIO(); render(D(1, "x"), "json", s); assert json.loads(s.getvalue()) == {"a": 1, "b": "x"}
    s = io.StringIO(); render({"k": [1, {"z": True}], "e": []}, "yaml", s)
    assert "k:" in s.getvalue()
    s = io.StringIO(); render(rows, "auto", s); assert json.loads(s.getvalue()) == rows
    assert default_format(io.StringIO()) == "json"
    with pytest.raises(ValueError):
        render(rows, "xml", io.StringIO())


def test_builtin_yaml(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "yaml", None)
    s = io.StringIO(); render({"a": [1, {"b": "x", "c": None}], "d": {}}, "yaml", s)
    assert s.getvalue() == 'a:\n  - 1\n  - b: "x"\n    c: null\nd: {}\n'
