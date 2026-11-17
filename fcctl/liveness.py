"""Liveness UX: task handles, waiters with heartbeat/stall detection, Ctrl-C detach/attach, stderr progress."""
from __future__ import annotations

import sys
from fcctl._sync import _io
from dataclasses import asdict, dataclass, fields
from typing import Any, Callable

from . import tasks as _tasks
from .request import FcError, PilotClient, TaskFailed

try:  # errors module may be provided by another worker
    from .errors import WaitTimeout  # type: ignore
except Exception:  # pragma: no cover - fallback
    class WaitTimeout(FcError):  # type: ignore[no-redef]
        exit_code = 8

TERMINAL = _tasks.TERMINAL


class Stalled(WaitTimeout):
    """Raised only with fail_on_stall=True."""


class Detached(FcError):
    """Ctrl-C while waiting: the remote task continues. Carries the serialized handle."""
    exit_code = 130

    def __init__(self, handle: Any):
        self.handle = handle
        self.handle_dict = handle.to_dict() if hasattr(handle, "to_dict") else {"task_id": str(handle)}
        tid = self.handle_dict.get("task_id") or self.handle_dict.get("action")
        super().__init__(f"detached; task continues: fcctl task attach {tid}", body=self.handle_dict)


class _Handle:
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TaskHandle(_Handle):
    plane: str = "pilot"
    server: str = ""
    task_id: str = ""
    op: str = ""
    site: str | None = None
    started_at: float = 0.0
    last_status: str | None = None


@dataclass
class ActionHandle(_Handle):
    """Central-plane action (e.g. a job tracked by name)."""
    action: str = ""
    plane: str = "central"
    server: str | None = None
    site: str | None = None
    started_at: float = 0.0
    last_status: str | None = None


def _event(kind: str, t: dict, elapsed: float, message: str = "") -> dict:
    return {"event": kind, "status": t.get("status"), "step": t.get("step") or t.get("current_step"),
            "elapsed": round(elapsed, 1), "message": message or t.get("message") or ""}


def wait(handle_or_task: Any, pilot: PilotClient, timeout: float = 3600, poll: float = 15,
         on_progress: Callable[[dict], None] | None = None, heartbeat: float = 30, stall_after: float = 300,
         fail_on_stall: bool = False, sse: bool = False,
         sleep: Callable[[float], None] = _io.sleep, clock: Callable[[], float] = _io.now,
         poller: "TaskPoller | None" = None) -> dict:
    """Wait for a pilot task. Returns the final task dict on success.

    Raises TaskFailed (failed/killed), WaitTimeout (deadline; remote continues), Detached (Ctrl-C).
    """
    handle = handle_or_task if isinstance(handle_or_task, TaskHandle) else TaskHandle(
        server=getattr(pilot, "server", ""), task_id=str(handle_or_task), started_at=clock())
    cb = on_progress or (lambda e: None)
    start = clock()
    deadline = start + timeout
    last_key: Any = object()
    last_change = last_emit = start
    stalled = False
    sse_iter = None
    if sse:
        try:
            sse_iter = _tasks.stream_events(pilot, f"/tasks/{handle.task_id}/events")
        except Exception:
            sse_iter = None
    try:
        while True:
            t = poller.get(handle.task_id) if poller else pilot.get(f"/tasks/{handle.task_id}")
            now = clock()
            status = t.get("status")
            handle.last_status = status
            key = (status, t.get("step") or t.get("current_step"))
            if key != last_key:
                last_key, last_change, last_emit, stalled = key, now, now, False
                cb(_event("progress", t, now - start))
            elif now - last_emit >= heartbeat:
                last_emit = now
                cb(_event("heartbeat", t, now - start))
            if status in TERMINAL:
                if status != "success":
                    raise TaskFailed(f"task {handle.task_id} {status}: {t.get('failure')}", body=t)
                return t
            if now - last_change >= stall_after and not stalled:
                stalled = True
                cb(_event("stalled", t, now - start, f"no change for {int(now - last_change)}s"))
                if fail_on_stall:
                    raise Stalled(f"task {handle.task_id} stalled; remote continues: fcctl task attach {handle.task_id}",
                                  body=handle.to_dict())
            if now >= deadline:
                raise WaitTimeout(f"task {handle.task_id} still {status} after {timeout}s; "
                                  f"remote continues: fcctl task attach {handle.task_id}", body=handle.to_dict())
            sleep(max(0.0, min(poll, deadline - now)))
    except KeyboardInterrupt:
        raise Detached(handle) from None
    finally:
        if sse_iter is not None and hasattr(sse_iter, "close"):
            sse_iter.close()


def attach(pilot: PilotClient, task_id: str, **kw) -> dict:
    """Re-attach to a running task (same semantics as wait)."""
    return wait(TaskHandle(server=getattr(pilot, "server", ""), task_id=task_id,
                           started_at=kw.get("clock", _io.now)()), pilot, **kw)


class TaskPoller:
    """One GET /tasks per interval serving many task ids (cached ttl)."""

    def __init__(self, pilot: PilotClient, ttl: float = 5.0, clock: Callable[[], float] = _io.now):
        self.pilot, self.ttl, self.clock = pilot, ttl, clock
        self._cache: dict[str, dict] = {}
        self._at = -1e18
        self.fetches = 0

    def refresh(self) -> dict[str, dict]:
        r = self.pilot.get("/tasks")
        items = r if isinstance(r, list) else (r.get("tasks") or r.get("items") or [])
        self._cache = {str(t.get("task_id") or t.get("id")): t for t in items if isinstance(t, dict)}
        self._at = self.clock()
        self.fetches += 1
        return self._cache

    def get(self, task_id: str) -> dict:
        if self.clock() - self._at >= self.ttl:
            self.refresh()
        t = self._cache.get(task_id)
        if t is None:  # not in list page: fall back to direct read
            t = self.pilot.get(f"/tasks/{task_id}")
        return t


class ProgressPrinter:
    """on_progress callback writing to stderr only; quiet when not a tty or disabled."""

    def __init__(self, stream=None, enabled: bool | None = None, no_progress: bool = False):
        self.stream = stream if stream is not None else sys.stderr
        tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = (tty if enabled is None else enabled) and not no_progress

    def __call__(self, e: dict) -> None:
        if not self.enabled:
            return
        line = f"[{e['event']}] {e.get('status')} {e.get('step') or ''} {e['elapsed']:.0f}s {e.get('message') or ''}"
        self.stream.write(line.rstrip() + "\n")
        self.stream.flush()


Spinner = ProgressPrinter
