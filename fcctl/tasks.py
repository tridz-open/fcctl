"""Pilot task polling and 409 task_conflict retry."""
from __future__ import annotations

from fcctl._sync import _io
from typing import Any, Callable, Iterator

from .request import PilotClient, TaskConflict, TaskFailed

TERMINAL = ("success", "failed", "killed")


def retry_conflict(fn: Callable[[], Any], attempts: int = 8, base: float = 5.0, cap: float = 60.0,
                   sleep: Callable[[float], None] = _io.sleep) -> Any:
    """Call fn; on 409 TaskConflict back off exponentially and retry."""
    for i in range(attempts):
        try:
            return fn()
        except TaskConflict:
            if i == attempts - 1:
                raise
            sleep(min(cap, base * 2 ** i))


def task_id_of(resp: Any) -> str | None:
    if isinstance(resp, dict):
        return resp.get("task_id") or (resp.get("task") or {}).get("task_id")
    return None


def wait_task(pilot: PilotClient, task_id: str, timeout: float = 3600, poll: float = 15,
              sleep: Callable[[float], None] = _io.sleep, clock: Callable[[], float] = _io.now) -> dict:
    """GET /tasks/<id> until success|failed|killed. Raises TaskFailed on failed/killed, TimeoutError."""
    deadline = clock() + timeout
    while True:
        t = pilot.get(f"/tasks/{task_id}")
        if t.get("status") in TERMINAL:
            if t["status"] != "success":
                raise TaskFailed(f"task {task_id} {t['status']}: {t.get('failure')}", body=t)
            return t
        if clock() >= deadline:
            raise TimeoutError(f"task {task_id} still {t.get('status')} after {timeout}s")
        sleep(poll)


def run(pilot: PilotClient, start: Callable[[], Any], wait: bool = True, **kw) -> Any:
    """Start a task-returning call (with 409 retry) and optionally wait for it."""
    resp = retry_conflict(start)
    tid = task_id_of(resp)
    return wait_task(pilot, tid, **kw) if (wait and tid) else resp


def stream_events(pilot: PilotClient, path: str) -> Iterator[str]:
    """Yield SSE `data:` payload lines from e.g. /tasks/<id>/events or /logs/<f>/events."""
    s = pilot.session()
    with pilot.http.stream("GET", f"{s['gateway']}/api/v1{path}", timeout=None,
                           headers={"Authorization": f"Bearer {s['token']}", "Accept": "text/event-stream"}) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                yield line[5:].strip()
