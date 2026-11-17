"""DEPRECATED (will be removed): Playwright persistent-context fallback.

fcctl never solves CAPTCHAs or Cloudflare challenges; the supported login is the plain HTTP path
(send_code/verify_code, manual code, --code-stdin, or IMAP). This module is only used if a library user
wires it in explicitly; the default is HTTP-only.

Playwright is imported lazily (pip install fcctl[browser]; playwright install chromium).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .request import ApiError, AuthError


class Browser:
    def __init__(self, profile_dir: Path | str, base: str = "https://dashboard.frappe.io",
                 headless: bool = True, pw_factory=None):
        import warnings
        warnings.warn("fcctl.browser.Browser is deprecated and will be removed; fcctl never solves "
                      "challenges. Use the HTTP login (send_code/verify_code, IMAP code).",
                      DeprecationWarning, stacklevel=2)
        self.profile_dir, self.base, self.headless = str(profile_dir), base.rstrip("/"), headless
        self.csrf: str | None = None
        self._pw_factory, self._pw, self._ctx, self._page = pw_factory, None, None, None

    # lifecycle
    def start(self):
        if self._ctx:
            return self._page
        if self._pw_factory:
            self._pw = self._pw_factory()
        else:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(self.profile_dir, headless=self.headless)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._page.on("request", self._capture_csrf)
        return self._page

    def close(self):
        if self._ctx:
            self._ctx.close()
        if self._pw:
            self._pw.stop()
        self._ctx = self._pw = self._page = None

    def _capture_csrf(self, req) -> None:
        tok = req.headers.get("x-frappe-csrf-token")
        if tok:
            self.csrf = tok

    def cookies(self) -> dict[str, str]:
        self.start()
        return {c["name"]: c["value"] for c in self._ctx.cookies(self.base)}

    def is_logged_in(self) -> bool:
        page = self.start()
        page.goto(self.base + "/dashboard", wait_until="domcontentloaded")
        return "/login" not in page.url and bool(self.cookies().get("sid", "Guest") != "Guest")

    def ensure_logged_in(self, alerter=None, wait_seconds: int = 600) -> bool:
        """If the profile is on the login page, alert a human and wait for them to log in (headed)."""
        if self.is_logged_in():
            return True
        shot = None
        try:
            shot = str(Path(self.profile_dir).parent / "login-needed.png")
            self._page.screenshot(path=shot)
        except Exception:
            pass
        if alerter:
            alerter.send("Frappe Cloud login needed",
                         f"Browser profile {self.profile_dir} is logged out. Log in inside the opened browser window, or run `fcctl auth login`.", shot)
        if self.headless:
            raise AuthError("browser profile logged out (headless); human login required")
        self._page.wait_for_url(lambda u: "/login" not in u, timeout=wait_seconds * 1000)
        return self.is_logged_in()

    def call_via_page(self, method: str, path: str, body: Any = None) -> Any:
        """Run the same fetch inside the logged-in page (same-origin, cookies + CSRF + Cloudflare clearance)."""
        page = self.start()
        if not page.url.startswith(self.base):
            page.goto(self.base + "/dashboard", wait_until="domcontentloaded")
        js = """async ({method, path, body, csrf}) => {
          const h = {'Accept':'application/json'}; if (csrf) h['X-Frappe-CSRF-Token']=csrf;
          if (body !== null && body !== undefined) h['Content-Type']='application/json';
          const r = await fetch(path, {method, headers:h, credentials:'include',
                    body: (body===null||body===undefined) ? undefined : JSON.stringify(body)});
          return {status:r.status, text: await r.text(), ct: r.headers.get('content-type')||''};
        }"""
        csrf = self.csrf or page.evaluate("() => (window.csrf_token || (window.frappe && frappe.csrf_token) || null)")
        res = page.evaluate(js, {"method": method, "path": path, "body": body, "csrf": csrf})
        try:
            data = json.loads(res["text"])
        except ValueError:
            raise ApiError(f"non-JSON response {res['status']} via page", res["status"], res["text"][:300])
        if res["status"] >= 400:
            et = data.get("exc_type", "") if isinstance(data, dict) else ""
            if res["status"] == 401 or et in ("AuthenticationError",):
                raise AuthError(et, res["status"], data)
            raise ApiError(f"{res['status']} {et}", res["status"], data)
        return data["data"] if isinstance(data, dict) and "data" in data else (data.get("message", data) if isinstance(data, dict) else data)

    def interactive_login(self, email: str | None = None) -> bool:
        """Open a headed window on the login page; a human enters the email code."""
        self.headless = False
        self.close()
        page = self.start()
        page.goto(self.base + "/login")
        page.wait_for_url(lambda u: "/login" not in u, timeout=900_000)
        return self.is_logged_in()
