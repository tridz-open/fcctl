"""Pilot task output and cancel (absent from tasks.py)."""
from __future__ import annotations

from .guard import guarded
from .request import PilotClient

OPS = {"output": "task.output", "cancel_task": "task.cancel"}


def output(p: PilotClient, task_id: str):
    return p.get(f"/tasks/{task_id}/output/content")


@guarded("cancel_task", lambda task_id: task_id, kind="pilot")
def cancel_task(p: PilotClient, task_id: str):
    return p.delete(f"/tasks/{task_id}")
