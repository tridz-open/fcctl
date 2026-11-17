"""Site operations through Pilot.


DEPRECATED: thin shim over fcctl._sync.sites (generated from fcctl._async.sites); new code should use the async core / facade.
"""
from __future__ import annotations

from .guard import guarded
from ._sync import sites as _impl

OPS = _impl.OPS
list_sites = _impl.list_sites
get_site = _impl.get_site
wildcard_domains = _impl.wildcard_domains
create_site = _impl.create_site
clone_site = _impl.clone_site
install_app = _impl.install_app
set_maintenance = _impl.set_maintenance
clear_cache = _impl.clear_cache
migrate = _impl.migrate
build_assets = _impl.build_assets
login_link = _impl.login_link
rename_site = _impl.rename_site
config_get = _impl.config_get
config_set = _impl.config_set
site_apps = _impl.site_apps

uninstall_app = guarded("uninstall_app", lambda site, app, force, remove_from_bench: f"{site}:{app}",
                        kind="pilot")(_impl.uninstall_app)
drop_site = guarded("drop_site", lambda site, no_backup: site, kind="pilot")(_impl.drop_site)
