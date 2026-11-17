"""Admin alerts: email (SMTP env) + Telegram (bot token/chat id env), optional screenshot.

Env: FCCTL_SMTP_HOST, FCCTL_SMTP_PORT(587), FCCTL_SMTP_USER, FCCTL_SMTP_PASS, FCCTL_ALERT_FROM,
FCCTL_ALERT_TO (comma list), FCCTL_TG_TOKEN, FCCTL_TG_CHAT.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

import httpx


class Alerter:
    def __init__(self, env=None):
        self.env = env if env is not None else os.environ
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str, screenshot: str | Path | None = None) -> dict:
        res = {}
        for name, fn in (("email", self._email), ("telegram", self._telegram)):
            try:
                res[name] = fn(subject, body, screenshot)
            except Exception as e:  # alerting must never raise
                res[name] = f"error: {e}"
        self.sent.append((subject, body))
        return res

    def _email(self, subject, body, shot):
        e = self.env
        if not (e.get("FCCTL_SMTP_HOST") and e.get("FCCTL_ALERT_TO")):
            return "skipped"
        m = EmailMessage()
        m["Subject"], m["From"], m["To"] = f"[fcctl] {subject}", e.get("FCCTL_ALERT_FROM", e.get("FCCTL_SMTP_USER", "fcctl@localhost")), e["FCCTL_ALERT_TO"]
        m.set_content(body)
        if shot and Path(shot).exists():
            m.add_attachment(Path(shot).read_bytes(), maintype="image", subtype="png", filename=Path(shot).name)
        with smtplib.SMTP(e["FCCTL_SMTP_HOST"], int(e.get("FCCTL_SMTP_PORT", 587))) as s:
            s.starttls()
            if e.get("FCCTL_SMTP_USER"):
                s.login(e["FCCTL_SMTP_USER"], e.get("FCCTL_SMTP_PASS", ""))
            s.send_message(m)
        return "sent"

    def _telegram(self, subject, body, shot):
        e = self.env
        tok, chat = e.get("FCCTL_TG_TOKEN"), e.get("FCCTL_TG_CHAT")
        if not (tok and chat):
            return "skipped"
        base = f"https://api.telegram.org/bot{tok}"
        text = f"[fcctl] {subject}\n{body}"[:4000]
        if shot and Path(shot).exists():
            r = httpx.post(f"{base}/sendPhoto", data={"chat_id": chat, "caption": text[:1000]},
                           files={"photo": Path(shot).read_bytes()}, timeout=30)
        else:
            r = httpx.post(f"{base}/sendMessage", data={"chat_id": chat, "text": text}, timeout=30)
        r.raise_for_status()
        return "sent"
