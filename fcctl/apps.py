"""Bench apps through Pilot.


DEPRECATED: thin shim over fcctl._sync.apps (generated from fcctl._async.apps); new code should use the async core / facade.
"""
from __future__ import annotations

from .guard import guarded
from ._sync import apps as _impl

DEFAULT_DEPS = _impl.DEFAULT_DEPS
install_order = _impl.install_order
OPS = _impl.OPS
list_apps = _impl.list_apps
add_marketplace = _impl.add_marketplace
add_repo = _impl.add_repo
check_updates = _impl.check_updates
update_apps = _impl.update_apps


add_in_order = _impl.add_in_order


switch_branch = guarded("switch_branch", lambda name, branch: f"{name}->{branch}", kind="pilot")(_impl.switch_branch)
remove_app = guarded("remove_app", lambda name: name, kind="pilot")(_impl.remove_app)


def remove(p, name: str):
    """Guarded; same as remove_app."""
    return remove_app(p, name)
