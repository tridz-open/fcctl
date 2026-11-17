import json
import threading

from fcctl.audit import AuditLog, WebhookSink


def test_chain_and_verify(tmp_path):
    a = AuditLog(tmp_path / "a.jsonl")
    for i in range(3):
        a.append(op="x", target=str(i), outcome="ok", team="T")
    assert a.verify() == (True, None)
    rows = a.list()
    assert [r["seq"] for r in rows] == [1, 2, 3] and rows[1]["prev"] == rows[0]["hash"]


def test_one_byte_edit_detected(tmp_path):
    p = tmp_path / "a.jsonl"
    a = AuditLog(p)
    for i in range(3):
        a.append(op="x", target=f"srv{i}", outcome="ok")
    b = bytearray(p.read_bytes())
    i = b.index(b"srv1")
    b[i + 3] = ord("9")
    p.write_bytes(bytes(b))
    assert a.verify() == (False, 2)


def test_redaction(tmp_path):
    a = AuditLog(tmp_path / "a.jsonl")
    r = a.append(op="x", target="u?token=abc123", outcome="ok", extra_payload={"password": "p", "n": 1})
    assert "abc123" not in json.dumps(r)
    assert r["extra"]["extra_payload"]["password"] == "[REDACTED]"
    assert a.verify()[0]


def test_filters_export_sink_and_concurrency(tmp_path):
    got = []
    a = AuditLog(tmp_path / "a.jsonl", sinks=[WebhookSink("http://h", lambda u, r: got.append(r))])
    ts = [threading.Thread(target=lambda i=i: a.append(op="o%d" % (i % 2), outcome="ok")) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert a.verify() == (True, None) and len(got) == 8
    assert len(a.list(op="o0")) == 4
    assert len(a.export().strip().splitlines()) == 8


def test_bad_sink_does_not_break(tmp_path):
    def boom(u, r): raise RuntimeError
    a = AuditLog(tmp_path / "a.jsonl", sinks=[WebhookSink("u", boom)])
    a.append(op="x")
    assert a.verify()[0]
