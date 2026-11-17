import json

import httpx
import pytest
import respx

from conftest import BASE, PROFILE
from fcctl import auth, objects
from fcctl.request import AuthError

M = BASE + "/api/v2/method/central.services.api.storage."


@pytest.fixture
def r():
    with respx.mock(assert_all_called=False) as router:
        yield router


def test_keepalive_ok_with_age(r, central):
    central.store.set_central(email="a@b.c")
    r.get(PROFILE).respond(200, json={"message": {"email": "a@b.c"}})
    out = auth.keepalive(central)
    assert out["ok"] is True and out["email"] == "a@b.c" and out["sid_age_seconds"] >= 0


def test_keepalive_auth_loss_raises(r, central):
    r.get(PROFILE).respond(401, json={"exc_type": "AuthenticationError"})
    with pytest.raises(AuthError):
        auth.keepalive(central)


def test_keepalive_guest_raises(r, central):
    r.get(PROFILE).respond(200, json={"message": {"user": "Guest"}})
    with pytest.raises(AuthError):
        auth.keepalive(central)


def test_presign_get_put(r, central):
    r.get(M + "get_object_url").respond(200, json={"message": {"url": "https://s3/x?sig=1"}})
    r.post(M + "get_upload_url").respond(200, json={"message": {"url": "https://s3/up?sig=2"}})
    assert objects.presign_get(central, "bk", "a/b.sql") == "https://s3/x?sig=1"
    assert objects.presign_put(central, "bk", "a/b.sql") == "https://s3/up?sig=2"


def test_put_and_get_object(r, central, tmp_path):
    src = tmp_path / "f.bin"
    src.write_bytes(b"hello")
    r.post(M + "get_upload_url").respond(200, json={"message": {"url": "https://s3/up"}})
    up = r.put("https://s3/up").respond(200)
    res = objects.put_object(central, "bk", "k", str(src))
    assert res == {"bucket": "bk", "key": "k", "bytes": 5} and up.called
    r.get(M + "get_object_url").respond(200, json={"message": {"url": "https://s3/dl"}})
    r.get("https://s3/dl").respond(200, content=b"world")
    dest = tmp_path / "out.bin"
    assert objects.get_object(central, "bk", "k", str(dest))["bytes"] == 5
    assert dest.read_bytes() == b"world"


class FakeImap:
    searches = 0  # first search is the pre-send baseline snapshot (empty); the code mail arrives after

    def __init__(self, host): pass
    def login(self, u, p): pass
    def select(self, f): pass
    def uid(self, cmd, *a):
        if cmd == "search":
            FakeImap.searches += 1
            return "OK", [b"1" if FakeImap.searches > 1 else b""]
        if cmd == "fetch":
            return "OK", [(b"1", b"From: notifications@frappecloud.com\r\nSubject: Your code\r\nContent-Type: text/plain\r\n\r\ncode 654321 ok")]
        return "OK", []
    def logout(self): pass


def test_login_via_imap_library(r, central):
    r.post(BASE + "/api/v2/method/central.api.auth.send_code").respond(200, json={"message": {}})
    v = r.post(BASE + "/api/v2/method/central.api.auth.verify_code").respond(
        200, json={"message": {"csrf_token": "c1"}}, headers={"set-cookie": "sid=newsid; Path=/"})
    out = auth.login_via_imap(central, "a@b.c", "imap.x", "u", "p", imap_cls=FakeImap, sleep=lambda s: None)
    assert out["csrf_token"] == "c1" and central.store.sid == "newsid"
    assert json.loads(v.calls[0].request.content)["code"] == "654321"


def test_cli_help_lists_new_commands(capsys):
    from fcctl.cli import main
    for argv, word in ((["bucket", "--help"], "presign"), (["--help"], "keepalive")):
        assert main(argv) == 0
        assert word in capsys.readouterr().out


def _sync_central(central):
    from fcctl._sync.central import CentralClient
    return CentralClient(central.store, base=BASE, http=central.http)


def test_logout_remote_calls_server_and_hides_sid(r, central):
    central = _sync_central(central)
    central.store.set_central(sid="SECRETSID")
    route = r.get(BASE + "/api/method/logout").respond(200, json={})
    assert central.logout_remote() is True
    assert route.calls.last.request.headers["cookie"] == "sid=SECRETSID"


def test_logout_remote_network_failure_is_best_effort(r, central):
    central = _sync_central(central)
    central.store.set_central(sid="SECRETSID")
    r.get(BASE + "/api/method/logout").mock(side_effect=httpx.ConnectError("down"))
    assert central.logout_remote() is False
