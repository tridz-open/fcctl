"""Canary tests: a planted secret must never reach any output sink."""
import importlib
import json
import logging
import sys

import httpx
import pytest
from hypothesis import given, settings, strategies as st

from fcctl import errors as E
from fcctl.audit import AuditLog
from fcctl.journal import Journal
from fcctl.redact import RedactingFormatter, install_debug_logging, redact, redact_text
from fcctl.request import DryRunRecorder

cli_main = importlib.import_module("fcctl.cli.main")
CANARY = "CANARYsecret0123456789xyz"
FERNET = "gAAAAA" + "B" * 40


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "keyring", type(sys)("keyring"))
    monkeypatch.setenv("FCCTL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FCCTL_KEY", CANARY)
    monkeypatch.setenv("FCCTL_APPROVER_KEY", CANARY + "A")
    monkeypatch.setenv("FCCTL_APPROVAL_SECRET", CANARY + "B")


@pytest.mark.parametrize("env", ["FCCTL_KEY", "FCCTL_APPROVER_KEY", "FCCTL_APPROVAL_SECRET"])
def test_env_secret_values_masked(env, monkeypatch):
    monkeypatch.setenv(env, "zzUniqueEnvValue99")
    assert "zzUniqueEnvValue99" not in redact_text("boom zzUniqueEnvValue99 end")


@pytest.mark.parametrize("key", ["sid", "token", "password", "secret", "api_key", "otp", "totp",
                                 "access_token", "csrf_token", "login_token", "Authorization", "Cookie"])
def test_key_names_in_all_shapes(key):
    assert CANARY not in json.dumps(redact({key: CANARY, "n": {key: [CANARY]}}))
    assert CANARY not in redact_text(f"{key}={CANARY}&x=1")
    assert CANARY not in redact_text(f"?a=1&{key}={CANARY}")
    assert CANARY not in redact_text(json.dumps({key: CANARY}))
    assert CANARY not in redact_text(repr({key: CANARY}))


@pytest.mark.parametrize("text", [
    f"Authorization: Bearer {CANARY}", f"Authorization: Basic {CANARY}", f"Cookie: sid={CANARY}; a=b",
    f"Set-Cookie: sid={CANARY}; HttpOnly", f"X-Frappe-CSRF-Token: {CANARY}",
    f"Bearer {CANARY}", FERNET, f"{{'headers': {{'Cookie': '{CANARY}'}}}}",
])
def test_text_forms(text):
    out = redact_text(text)
    assert CANARY not in out and FERNET not in out


def test_error_rendering_and_str(capsys):
    e = E.ApiError(f"fail sid={CANARY} {FERNET}", 500, {"token": CANARY, "msg": f"Bearer {CANARY}"},
                   what=f"w {CANARY}", why=f"y password={CANARY}", fix=f"f {CANARY}")
    assert CANARY not in str(e) and CANARY not in json.dumps(e.body)
    assert CANARY not in E.format_error(Exception(f"raw {CANARY}"))
    assert CANARY not in E.format_error(e)


def test_debug_traceback_output(capsys):
    try:
        raise RuntimeError(f"Cookie: sid={CANARY} key {FERNET}")
    except RuntimeError as exc:
        rc = cli_main._fail(exc, True)
    cap = capsys.readouterr()
    assert rc != 0 and CANARY not in cap.out + cap.err and FERNET not in cap.err


def test_httpx_debug_logging(capsys):
    install_debug_logging()
    logging.getLogger("httpx").debug("HTTP Request: GET https://x/?token=%s Authorization: Bearer %s", CANARY, CANARY)
    cap = capsys.readouterr()
    assert CANARY not in cap.out + cap.err
    fmt = RedactingFormatter("%(message)s")
    rec = logging.LogRecord("n", 10, "", 0, f"Cookie: sid={CANARY}", None, None)
    assert CANARY not in fmt.format(rec)


def test_audit_log(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditLog(p)
    a.append(op="x", target=f"https://h/?sid={CANARY}", actor=CANARY, extra_payload={"api_key": CANARY},
             note=f"Authorization: Bearer {CANARY}")
    assert CANARY not in p.read_text()
    assert a.verify()[0]


def test_journal(tmp_path):
    j = Journal(tmp_path / "j")
    e = j.create("op", {"password": CANARY, "cmd": f"x --token={CANARY}"})
    j.transition(e["id"], "dispatching")
    j.transition(e["id"], "failed", error=f"boom {CANARY} Cookie: sid={CANARY}", result={"secret": CANARY})
    for f in (tmp_path / "j").iterdir():
        assert CANARY not in f.read_text()
    try:
        j.execute("op2", lambda _e: (_ for _ in ()).throw(RuntimeError(f"bad {CANARY}")))
    except RuntimeError:
        pass
    assert all(CANARY not in f.read_text() for f in (tmp_path / "j").iterdir())


def test_dry_run_recording():
    DryRunRecorder.start()
    DryRunRecorder.record("POST", "/x", {"sid": CANARY}, {"password": CANARY, "headers": {"Cookie": CANARY},
                                                          "note": f"token={CANARY}"})
    assert CANARY not in json.dumps(DryRunRecorder.stop())


def test_site_admin_style_httpx_exception_text():
    r = httpx.Response(401, text=f"sid={CANARY}", request=httpx.Request("GET", "https://h/"))
    assert CANARY not in str(E.ApiError(f"{r.status_code} {r.text}"))


_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=60)


@settings(max_examples=150, deadline=None)
@given(pre=_text, post=_text, key=st.sampled_from(["sid", "token", "password", "secret", "api_key", "otp"]),
       sep=st.sampled_from(["=", ": ", '": "', "="]))
def test_property_idempotent_and_no_leak(pre, post, key, sep):
    x = f"{pre} {key}{sep}{CANARY} {post}"
    once = redact_text(x)
    assert CANARY not in once
    assert redact_text(once) == once
    d = {"a": [x, {key: CANARY}], key: CANARY}
    r1 = redact(d)
    assert redact(r1) == r1 and CANARY not in json.dumps(r1)
