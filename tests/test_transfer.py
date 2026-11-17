import json
import httpx
import pytest
import respx

from fcctl.backups import restore_from_site_backup as wrapped
from fcctl.request import PilotClient
from fcctl.transfer import restore_from_site_backup, TransferError
from conftest import LINK

SRC_GW, DST_GW = "https://src.example", "https://dst.example"
TS = "20261116_215813"
DB = b"D" * 25
PUB = b"P" * 10
PRIV = b"V" * 7
FILES = {"a-database.sql.gz": DB, "a-files.tar": PUB, "a-private-files.tar": PRIV, "a-config.json": b"{}"}
META = {"files": [{"filename": k, "size": len(v)} for k, v in FILES.items()]}


@pytest.fixture(autouse=True)
def nosleep(monkeypatch):
    monkeypatch.setattr("fcctl._sync._io.sleep", lambda s: None)


@pytest.fixture
def clients(central, store):
    src, dst = PilotClient(central, "server-src"), PilotClient(central, "server-dst")
    for c, tok, gw in ((src, "ts", SRC_GW), (dst, "td", DST_GW)):
        c._cache()[c.server] = {"token": tok, "gateway": gw, "issued_at": c.clock()}
    return src, dst


def mock_src(router):
    router.get(f"{SRC_GW}/api/v1/sites/s/backups/{TS}").mock(return_value=httpx.Response(200, json=META))

    def content(request):
        data = FILES[request.url.path.split("/files/")[1].split("/")[0]]
        a, b = request.headers["range"].removeprefix("bytes=").split("-")
        return httpx.Response(206, content=data[int(a):int(b) + 1])
    router.get(url__regex=rf"{SRC_GW}/api/v1/sites/s/backups/{TS}/files/.+/content").mock(side_effect=content)


def mock_dst(router, put=None):
    up = router.post(f"{DST_GW}/api/v1/sites/d/uploads").mock(
        return_value=httpx.Response(201, json={"upload_id": "u1", "chunk_size": 1}))
    puts = router.put(url__regex=rf"{DST_GW}/api/v1/sites/d/uploads/u1/files/\w+").mock(
        side_effect=put or (lambda r: httpx.Response(200, json={"received": 1})))
    rs = router.post(f"{DST_GW}/api/v1/sites/d/actions/restore").mock(
        return_value=httpx.Response(202, json={"task": "t1"}))
    return up, puts, rs


@respx.mock
def test_order_chunks_restore_body(clients):
    src, dst = clients
    mock_src(respx)
    up, puts, rs = mock_dst(respx)
    seen = []
    task = restore_from_site_backup(src, "s", TS, dst, "d", chunk_size=10,
                                    progress=lambda b, t, f: seen.append((b, t, f)))
    assert task == {"task": "t1"}
    assert json.loads(up.calls[0].request.content)["files"]["database"] == {"filename": "a-database.sql.gz", "size": 25}
    got = [(c.request.url.path.rsplit("/", 1)[1], c.request.url.params["offset"], len(c.request.content))
           for c in puts.calls]
    assert got == [("database", "0", 10), ("database", "10", 10), ("database", "20", 5),
                   ("public", "0", 10), ("private", "0", 7)]
    assert puts.calls[0].request.headers["content-type"] == "application/octet-stream"
    body = json.loads(rs.calls[0].request.content)
    assert body["parts"] == ["database", "public", "private"] and body["upload_id"] == "u1"
    assert seen[-1] == (42, 42, "a-private-files.tar")


@respx.mock
def test_resume_skips_done(clients):
    src, dst = clients
    mock_src(respx)
    up, puts, rs = mock_dst(respx)
    state = {"upload_id": "u1", "dst_site": "d", "timestamp": TS, "done": {"database": 20, "public": 10}}
    restore_from_site_backup(src, "s", TS, dst, "d", chunk_size=10, state=state)
    assert not up.called
    assert [(c.request.url.path.rsplit("/", 1)[1], c.request.url.params["offset"]) for c in puts.calls] == [
        ("database", "20"), ("private", "0")]


@respx.mock
def test_retry_5xx(clients):
    src, dst = clients
    mock_src(respx)
    n = {"c": 0}

    def put(r):
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, json={"received": 1})
    mock_dst(respx, put)
    restore_from_site_backup(src, "s", TS, dst, "d", chunk_size=100)
    assert n["c"] == 5  # 2 failures + 3 parts


@respx.mock
def test_retry_exhausted(clients):
    src, dst = clients
    mock_src(respx)
    mock_dst(respx, lambda r: httpx.Response(500))
    with pytest.raises(Exception):
        restore_from_site_backup(src, "s", TS, dst, "d", chunk_size=100, retries=2)


@respx.mock
def test_401_remints(clients):
    src, dst = clients
    mock_src(respx)
    link = respx.get(LINK).mock(return_value=httpx.Response(200, json={"data": {"url": DST_GW + "/?sid=fresh"}}))
    auth = []

    def put(r):
        auth.append(r.headers["authorization"])
        return httpx.Response(401, json={"error": "unauthorized"}) if len(auth) == 1 else httpx.Response(200, json={})
    mock_dst(respx, put)
    restore_from_site_backup(src, "s", TS, dst, "d", chunk_size=100)
    assert link.called and auth[0] == "Bearer td" and auth[1] == "Bearer fresh"


@respx.mock
def test_missing_part_and_wrapper(clients):
    src, dst = clients
    respx.get(f"{SRC_GW}/api/v1/sites/s/backups/{TS}").mock(
        return_value=httpx.Response(200, json={"files": [{"filename": "a-files.tar", "size": 1}]}))
    with pytest.raises(TransferError):
        wrapped(src, "s", TS, dst, "d")


@respx.mock
def test_wrapper_unowned_files_approval_not_restore(clients, tmp_path):
    """Without workflow ownership the backup is only uploaded; the overwriting restore is guarded."""
    from fcctl import guard
    from fcctl.guard import Guard, PendingApproval
    src, dst = clients
    mock_src(respx)
    up, puts, rs = mock_dst(respx)
    guard.set_default_guard(Guard(tmp_path, key="k" * 32, secret="s3cret", alerter=lambda *a, **k: None,
                                  user="bot"))
    try:
        res = wrapped(src, "s", TS, dst, "d", chunk_size=100)
    finally:
        guard.set_default_guard(None)
    assert isinstance(res, PendingApproval) and res.op == "restore_backup" and res.target == "d"
    assert up.called and puts.called and not rs.called


@respx.mock
def test_wrapper_workflow_owned_restores(clients):
    src, dst = clients
    mock_src(respx)
    up, puts, rs = mock_dst(respx)
    wrapped(src, "s", TS, dst, "d", chunk_size=100, owned_by_workflow="wf-1")
    assert rs.called
