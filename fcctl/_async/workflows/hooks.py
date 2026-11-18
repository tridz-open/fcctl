"""Hook resolver + built-in Pilot-native hooks. Hooks are sync `(ctx, **args) -> None`,
must be idempotent (re-run after a crash). The journal stores names/versions/args only, never code."""
from __future__ import annotations

from ..pilot import seg

from typing import Any, Callable

import inspect

from .. import sites
from ...errors import UsageError
from .types import HookRef, StepContext

Hook = Callable[..., Any]  # sync or async callable (ctx, **args); idempotent


def task_id_of(resp: Any) -> str | None:
    if isinstance(resp, dict):
        return resp.get("task_id") or (resp.get("task") or {}).get("task_id")
    return None


async def settle(x: Any) -> Any:
    """Await x if it is awaitable (hooks / wait_task callables may be plain sync functions)."""
    if inspect.isawaitable(x):
        x = await x
    return x


class HookResolver:
    def __init__(self, hooks: dict[str, Hook] | None = None, fallback: "HookResolver | None" = None):
        self._hooks = dict(hooks or {})
        self.fallback = fallback

    def register(self, name: str, version: int | str, fn: Hook) -> "HookResolver":
        self._hooks[f"{name}@{version}"] = fn
        return self

    def resolve(self, ref: HookRef | str) -> Hook:
        key = ref if isinstance(ref, str) else ref.key
        if key in self._hooks:
            return self._hooks[key]
        if self.fallback is not None:
            return self.fallback.resolve(key)
        raise UsageError(f"no hook registered for {key}")

    def has(self, ref: HookRef | str) -> bool:
        try:
            self.resolve(ref)
            return True
        except UsageError:
            return False


async def _wait(ctx: StepContext, resp) -> None:
    tid = task_id_of(resp)
    if tid and ctx.wait_task:
        await settle(ctx.wait_task(tid))


async def _patch_config(ctx: StepContext, cfg: dict) -> None:
    # local adapter: PATCH /sites/<s>/configuration (same route sites.set_maintenance uses)
    await settle(ctx.pilot.patch(f"/sites/{seg(ctx.site)}/configuration", cfg))


async def set_config(ctx, config: dict | None = None, **kw):
    await _patch_config(ctx, dict(config if config is not None else kw))


async def set_host_name(ctx, host_name: str | None = None):
    """Protected on Frappe Cloud Pilot (422 protected_configuration); kept registered so old templates fail fast."""
    raise UsageError("Pilot manages host_name; remove core.set_host_name from the template",
                     fix="drop the core.set_host_name hook from the template")


async def clear_cache(ctx):
    await _wait(ctx, await sites.clear_cache(ctx.pilot, ctx.site))


async def migrate(ctx):
    await _wait(ctx, await sites.migrate(ctx.pilot, ctx.site))


async def maintenance_on(ctx):
    await _patch_config(ctx, {"maintenance_mode": 1})


async def maintenance_off(ctx):
    await _patch_config(ctx, {"maintenance_mode": 0})


async def scheduler_pause(ctx):
    await _patch_config(ctx, {"pause_scheduler": 1})


async def scheduler_resume(ctx):
    await _patch_config(ctx, {"pause_scheduler": 0})


async def login_link(ctx, hint: str | None = None):
    await sites.login_link(ctx.pilot, ctx.site, hint)
    if ctx.journal is not None and ctx.workflow_id:
        e = ctx.journal.get(ctx.workflow_id)
        e.setdefault("wf", {})["login_link_issued"] = True  # URL itself is a credential: never journaled
        ctx.journal._write(e)


async def set_fresh_encryption_key(ctx, confirm_no_encrypted_values: bool = False):
    """Protected on Frappe Cloud Pilot (422 protected_configuration); kept registered so old templates fail fast."""
    raise UsageError("Pilot manages encryption_key; remove core.set_fresh_encryption_key from the template",
                     fix="drop the core.set_fresh_encryption_key hook from the template")


BUILTINS: dict[str, Hook] = {
    "core.set_config@1": set_config, "core.set_host_name@1": set_host_name,
    "core.clear_cache@1": clear_cache, "core.migrate@1": migrate,
    "core.maintenance_on@1": maintenance_on, "core.maintenance_off@1": maintenance_off,
    "core.scheduler_pause@1": scheduler_pause, "core.scheduler_resume@1": scheduler_resume,
    "core.login_link@1": login_link, "core.set_fresh_encryption_key@1": set_fresh_encryption_key,
}


def builtin_resolver() -> HookResolver:
    return HookResolver(BUILTINS)
