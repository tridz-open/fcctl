"""Email-code login, keepalive, CSRF refresh, code providers."""
from __future__ import annotations

import email as _email
import imaplib
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from fcctl._sync import _io
from typing import Callable, cast


from .request import AuthError, CentralClient

CodeProvider = Callable[[], str]
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def manual_code_provider(prompt: str = "6-digit Frappe Cloud code: ") -> CodeProvider:
    return lambda: input(prompt).strip()


class ImapCodePoller:
    """Poll a dedicated ops mailbox for the newest 6-digit code (mail newer than `since`)."""

    def __init__(self, host: str, user: str, password: str, folder: str = "INBOX",
                 sender: str = "frappe", timeout: float = 120, interval: float = 5,
                 imap_cls=imaplib.IMAP4_SSL, sleep=_io.sleep, clock=time.time):
        self.host, self.user, self.password, self.folder = host, user, password, folder
        self.sender, self.timeout, self.interval = sender, timeout, interval
        self.imap_cls, self.sleep, self.clock = imap_cls, sleep, clock
        self.started: float | None = None
        self.baseline: set[bytes] = set()

    @staticmethod
    def extract(text: str) -> str | None:
        m = CODE_RE.findall(text)
        return m[-1] if m else None

    SKEW = 30  # seconds of slack before the recorded start

    def _uids(self, m, *crit) -> list[bytes]:
        _, ids = m.uid("search", None, *crit)
        return ids[0].split() if ids and ids[0] else []

    def start(self) -> None:
        """Record the login start time and snapshot existing sender mail. Call just before send_code."""
        self.started = self.clock()
        m = self.imap_cls(self.host)
        try:
            m.login(self.user, self.password)
            m.select(self.folder)
            # Gmail IMAP FROM search is word-based ("frappe" misses "frappecloud.com"): search by date and
            # filter the sender in Python instead.
            since = datetime.fromtimestamp((self.started or 0.0) - self.SKEW, timezone.utc).strftime("%d-%b-%Y")
            self.baseline = set(self._uids(m, "SINCE", since))
        finally:
            try:
                m.logout()
            except Exception:
                pass

    def _fresh(self, msg) -> bool:
        try:
            dt = parsedate_to_datetime(msg.get("Date", ""))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() >= (self.started or 0.0) - self.SKEW
        except (TypeError, ValueError):
            return True  # no usable Date: rely on the UID baseline

    def _latest(self) -> str | None:
        if self.started is None:
            self.start()
        m = self.imap_cls(self.host)
        try:
            m.login(self.user, self.password)
            m.select(self.folder)
            since = datetime.fromtimestamp((self.started or 0.0) - self.SKEW, timezone.utc).strftime("%d-%b-%Y")
            for mid in reversed(self._uids(m, "SINCE", since)):
                if mid in self.baseline:
                    continue
                _, data = m.uid("fetch", mid, "(RFC822)")
                msg = _email.message_from_bytes(data[0][1])
                if self.sender.lower() not in str(msg.get("From", "")).lower() or not self._fresh(msg):
                    continue
                # Subject first ("123456 is your Frappe Cloud sign-in code"); HTML bodies carry 6-digit colours
                # like #737373. Fall back to plain text, then HTML with #hex colours stripped.
                code = self.extract(str(msg.get("Subject", "")))
                for ctype in ("text/plain", "text/html"):
                    if code:
                        break
                    for p in msg.walk():
                        if p.get_content_type() == ctype:
                            body = cast(bytes, p.get_payload(decode=True)).decode(errors="ignore")
                            code = self.extract(re.sub(r"#[0-9a-fA-F]{6}\b", "", body)) or code
                if code:
                    m.uid("store", mid, "+FLAGS", "\\Seen")
                    return code
        finally:
            try:
                m.logout()
            except Exception:
                pass
        return None

    def __call__(self) -> str:
        end = _io.now() + self.timeout
        while _io.now() < end:
            code = self._latest()
            if code:
                return code
            self.sleep(self.interval)
        raise TimeoutError("no sign-in code arrived in mailbox")


def login_with_code(central: CentralClient, email: str, code_provider: CodeProvider) -> dict:
    """send_code -> code_provider() -> verify_code; persists sid (Set-Cookie) + csrf_token."""
    prepare = getattr(code_provider, "start", None)
    if callable(prepare):
        prepare()  # record start time + snapshot pre-existing mail before the code is sent
    central.call("POST", "central.api.auth.send_code", json={"email": email}, team=False)
    code = code_provider()
    r = central.http.send(central.build("POST", "central.api.auth.verify_code",
                                        json={"email": email, "code": code}, team=False))
    from .request import raise_for_central
    data = raise_for_central(r) or {}
    sid = next((m.group(1) for c in r.headers.get_list("set-cookie")
                if (m := re.match(r"sid=([^;]+)", c)) and m.group(1) != "Guest"), None)
    if not sid:
        raise AuthError("verify_code returned no sid cookie")
    central.store.set_central(sid=sid, csrf=(data.get("csrf_token") if isinstance(data, dict) else None),
                              email=email)
    return data


def my_profile(central: CentralClient) -> dict:
    """GET central.api.identity.my_profile: slides the session, captures rotated sid."""
    return central.call("GET", "central.api.identity.my_profile", team=False)


def keepalive(central: CentralClient) -> dict:
    """One cheap Central GET to slide the sid. Returns {ok, email, sid_age_seconds, checked_at}.

    Raises AuthError when the session is lost (caller runs the reauth chain / pauses the queue).
    """
    import time
    prof = my_profile(central)
    if isinstance(prof, dict) and (prof.get("user") == "Guest" or prof.get("email") == "Guest"):
        raise AuthError("session is Guest: sign in again")
    now = time.time()
    upd = central.store.data.get("sid_updated")
    email = prof.get("email") if isinstance(prof, dict) else None
    return {"ok": True, "email": email or central.store.data.get("email"),
            "sid_age_seconds": (now - upd) if upd else None, "checked_at": now}


def login_via_imap(central: CentralClient, email: str, host: str, user: str, password: str,
                   folder: str = "INBOX", sender: str = "frappe", timeout: float = 120,
                   interval: float = 5, **poller_kw) -> dict:
    """Fully automatic OTP re-login using a bot mailbox (library entry point, no CLI needed)."""
    poller = ImapCodePoller(host, user, password, folder=folder, sender=sender, timeout=timeout,
                            interval=interval, **poller_kw)
    return login_with_code(central, email, poller)


def refresh_csrf(central: CentralClient) -> str | None:
    """Re-learn the CSRF token: response header if present, else scrape the dashboard boot page."""
    r = central.http.get(central.base + "/api/v2/method/central.api.identity.my_profile",
                         headers={"Cookie": f"sid={central.store.sid or ''}"})
    central._capture_cookie(r)
    tok = r.headers.get("x-frappe-csrf-token")
    if not tok:
        page = central.http.get(central.base + "/dashboard", headers={"Cookie": f"sid={central.store.sid or ''}"})
        m = re.search(r"csrf_token\W+([0-9a-f]{20,})", page.text)
        tok = m.group(1) if m else None
    if tok:
        central.store.set_central(csrf=tok)
    return tok
