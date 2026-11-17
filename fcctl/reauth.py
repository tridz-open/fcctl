"""Single-flight re-authentication.

Order: (1) reload cookies from the Playwright profile (DEPRECATED browser support; optional) -> (2) refresh CSRF ->
(3) re-mint Pilot bench link (pilot scope) -> (4) browser login -> (5) alert + raise ReauthFailed.
"""
from __future__ import annotations

import threading
from typing import Any

from .auth import refresh_csrf
from .request import FcError, PilotClient, ReauthFailed


class Reauther:
    def __init__(self, store, central, browser=None, alerter=None, pilot: PilotClient | None = None):
        self.store, self.central, self.browser, self.alerter, self.pilot = store, central, browser, alerter, pilot
        self._lock = threading.Lock()
        self._gen = 0
        self.trail: list[str] = []  # step names attempted, in order (tests + audit)
        central.reauth = self

    def _central_ok(self) -> bool:
        try:
            self.central._once("GET", "central.api.identity.my_profile", None, None, False)
            return True
        except FcError:
            return False

    # steps -----------------------------------------------------------
    def _cookies(self) -> bool:
        if not self.browser:
            return False
        c = self.browser.cookies().get("sid")
        if not c:
            return False
        self.store.set_central(sid=c)
        return self._central_ok()

    def _csrf(self) -> bool:
        return bool(refresh_csrf(self.central)) and self._central_ok()

    def _bench_link(self, server: str | None) -> bool:
        server = server or (self.pilot.server if self.pilot else None)
        if not server:
            return False
        PilotClient(self.central, server, http=self.pilot.http if self.pilot else None).mint()
        return True

    def _browser_login(self) -> bool:
        if not self.browser:
            return False
        self.browser.ensure_logged_in(alerter=self.alerter)
        c = self.browser.cookies().get("sid")
        if c:
            self.store.set_central(sid=c)
        if self.browser.csrf:
            self.store.set_central(csrf=self.browser.csrf)
        return self._central_ok()

    # entry -------------------------------------------------------------
    def recover(self, scope: str = "central", error: Exception | None = None, server: str | None = None) -> str:
        gen = self._gen
        with self._lock:
            if self._gen != gen:  # another thread already re-authed while we waited
                return "concurrent"
            central_ok = scope == "pilot" and self._central_ok()
            steps: list[tuple[str, Any]] = []
            if not central_ok:
                steps += [("cookies", self._cookies), ("csrf", self._csrf)]
            if scope == "pilot":
                steps.append(("bench_link", lambda: self._bench_link(server)))
            steps.append(("browser_login", self._browser_login))
            if scope == "pilot":
                steps.append(("bench_link_after_login", lambda: self._bench_link(server)))
            for name, fn in steps:
                self.trail.append(name)
                try:
                    if fn():
                        self._gen += 1
                        return name
                except Exception:
                    continue
            self.trail.append("alert")
            if self.alerter:
                self.alerter.send("Frappe Cloud re-auth FAILED",
                                  f"scope={scope} error={error!r}\nHuman login required in the profile "
                                  f"{self.store.profile_dir}. Trail: {self.trail}")
            raise ReauthFailed(f"all re-auth steps failed ({scope})")
