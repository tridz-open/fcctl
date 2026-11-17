import pytest

from fcctl import liveness
from fcctl.liveness import ActionHandle, Detached, ProgressPrinter, TaskHandle, TaskPoller, wait
from fcctl.request import FcError, TaskFailed

GW = "https://gw.example"


class FakePilot:
    server = "srv1"

    def __init__(self, seq, listing=None):
        self.seq, self.listing, self.calls = list(seq), listing, []

    def get(self, path, **kw):
        self.calls.append(path)
        if path == "/tasks":
            return self.listing
        return self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(seq, **kw):
    c = Clock()
    ev = []
    p = FakePilot(seq)
    try:
        r = wait("t1", p, sleep=c.sleep, clock=c, on_progress=ev.append, **kw)
    except Exception as e:
        return e, ev, p
    return r, ev, p


def test_handles_roundtrip():
    h = TaskHandle("pilot", "s", "t", "migrate", "x.com", 1.0, "running")
    assert TaskHandle.from_dict(h.to_dict()) == h
    a = ActionHandle(action="create_site")
    assert ActionHandle.from_dict(a.to_dict()) == a


def test_success_and_progress_on_change_only():
    r, ev, _ = run([{"status": "running", "step": "a"}, {"status": "running", "step": "a"},
                    {"status": "running", "step": "b"}, {"status": "success"}], heartbeat=1000)
    assert r["status"] == "success"
    assert [e["event"] for e in ev] == ["progress", "progress", "progress"]
    assert ev[1]["step"] == "b"


def test_heartbeat_when_unchanged():
    seq = [{"status": "running"}] * 5 + [{"status": "success"}]
    r, ev, _ = run(seq, poll=15, heartbeat=30)
    assert [e["event"] for e in ev].count("heartbeat") >= 1


def test_stall_event_no_raise():
    seq = [{"status": "running"}] * 30 + [{"status": "success"}]
    r, ev, _ = run(seq, poll=15, stall_after=60, timeout=10000)
    assert r["status"] == "success"
    assert sum(e["event"] == "stalled" for e in ev) == 1


def test_fail_on_stall():
    r, ev, _ = run([{"status": "running"}], poll=15, stall_after=60, fail_on_stall=True)
    assert isinstance(r, liveness.Stalled) and isinstance(r, FcError)


def test_timeout():
    r, ev, _ = run([{"status": "running"}], poll=15, timeout=100, stall_after=10**6)
    assert isinstance(r, liveness.WaitTimeout)
    assert getattr(r, "exit_code", 8) == 8
    assert "fcctl task attach t1" in str(r)


@pytest.mark.parametrize("st", ["failed", "killed"])
def test_failed(st):
    r, _, _ = run([{"status": "running"}, {"status": st, "failure": {"code": "x"}}])
    assert isinstance(r, TaskFailed)


def test_ctrl_c_detaches():
    c = Clock()

    def boom(s):
        raise KeyboardInterrupt

    with pytest.raises(Detached) as ei:
        wait("t9", FakePilot([{"status": "running"}]), sleep=boom, clock=c)
    assert ei.value.exit_code == 130
    assert ei.value.handle_dict["task_id"] == "t9"
    assert "detached; task continues: fcctl task attach t9" in str(ei.value)


def test_attach():
    c = Clock()
    r = liveness.attach(FakePilot([{"status": "success"}]), "t1", sleep=c.sleep, clock=c)
    assert r["status"] == "success"


def test_poller_one_list_per_interval():
    c = Clock()
    p = FakePilot([], listing={"tasks": [{"task_id": "a", "status": "running"}, {"task_id": "b", "status": "success"}]})
    tp = TaskPoller(p, ttl=5, clock=c)
    assert tp.get("a")["status"] == "running" and tp.get("b")["status"] == "success"
    assert tp.fetches == 1
    c.t = 6
    tp.get("a")
    assert tp.fetches == 2


def test_wait_with_poller():
    c = Clock()
    p = FakePilot([], listing=[{"task_id": "t1", "status": "success"}])
    assert wait("t1", p, poller=TaskPoller(p, clock=c), sleep=c.sleep, clock=c)["status"] == "success"


def test_printer_quiet_non_tty_and_stderr_only(capsys):
    import io
    buf = io.StringIO()
    ProgressPrinter(stream=buf)({"event": "progress", "status": "x", "elapsed": 1})
    assert buf.getvalue() == ""
    ProgressPrinter(stream=buf, enabled=True)({"event": "progress", "status": "x", "elapsed": 1})
    assert "progress" in buf.getvalue()
    ProgressPrinter(stream=buf, enabled=True, no_progress=True)({"event": "progress", "status": "y", "elapsed": 1})
    assert "y" not in buf.getvalue()
    assert capsys.readouterr().out == ""
