"""DEPRECATED shim: see fcctl._async.workflows.hooks / fcctl._sync.workflows.hooks."""
from __future__ import annotations

from .._sync.workflows.hooks import *  # noqa: F401,F403
from .._sync.workflows.hooks import BUILTINS, Hook, HookResolver, builtin_resolver  # noqa: F401
