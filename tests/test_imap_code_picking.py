"""IMAP code picking against a fake imaplib server."""
from email.message import EmailMessage
from email.utils import formatdate

import pytest

from fcctl.auth import ImapCodePoller

NOW = 1_800_000_000.0


def _date(offset=0):
    return formatdate(NOW + offset, usegmt=True)


def _mail(subject, body, html=False, multipart=False, date=None):
    m = EmailMessage()
    m["Date"] = date or _date(10)
    m["Subject"] = subject
    m["From"] = "Frappe Cloud <noreply@frappe.io>"
    if multipart:
        m.set_content(body)
        m.add_alternative(f"<p>{body}</p>", subtype="html")
    else:
        m.set_content(body, subtype="html" if html else "plain")
    return m.as_bytes()


class FakeServer:
    """Minimal imaplib.IMAP4_SSL stand-in; messages = list of (flags_seen, from_ok, raw)."""
    instances = []

    def __init__(self, msgs):
        self.msgs, self.seen, self.searches, self.logins = msgs, set(), [], []
        self.logged_out = False

    def __call__(self, host):  # acts as imap_cls
        self.host = host
        FakeServer.instances.append(self)
        return self

    def login(self, u, p): self.logins.append((u, p))
    def select(self, f): self.folder = f

    def uid(self, cmd, *a):
        if cmd == "search":
            return self.search(*a)
        if cmd == "fetch":
            return self.fetch(*a)
        return self.store(*a)

    def search(self, charset, *crit):
        # Like Gmail: returns seen and unseen mail alike; the poller filters sender and date itself.
        self.searches.append(crit)
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.msgs)))]

    def fetch(self, mid, spec):
        frm, raw = self.msgs[int(mid) - 1]
        raw = raw.replace(b"From: Frappe Cloud <noreply@frappe.io>", f"From: {frm}".encode(), 1)
        return "OK", [(mid, raw)]

    def store(self, mid, op, flag): self.seen.add(int(mid))
    def logout(self): self.logged_out = True


def poller(srv, **kw):
    p = _poller(srv, **kw)
    p.started = NOW  # start recorded, empty baseline: every mail in the fake is "new"
    return p


def _poller(srv, **kw):
    return ImapCodePoller("imap.gmail.com", "ops@example.com", "app-pass", imap_cls=srv,
                          sleep=lambda s: None, clock=lambda: NOW, **kw)


def test_picks_newest_frappe_code_and_marks_seen():
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "Your code is 111111")),
                      ("noreply@frappe.io", _mail("Login code", "Your code is 222222"))])
    assert poller(srv)() == "222222"
    assert srv.seen == {2} and srv.logged_out
    # Gmail FROM search is word-based ("frappe" misses frappecloud.com), so only SINCE goes to the server
    assert all(c[0] == "SINCE" and "FROM" not in c and "UNSEEN" not in c for c in srv.searches)
    assert srv.logins == [("ops@example.com", "app-pass")]


def test_ignores_other_senders():
    srv = FakeServer([("promo@other.com", _mail("Code", "999999")),
                      ("noreply@frappe.io", _mail("Login code", "123456"))])
    assert poller(srv)() == "123456"
    assert srv.seen == {2}


def test_code_in_subject_and_html_and_multipart():
    for msg in (_mail("Your code 424242", "hello"), _mail("x", "code <b>313131</b>", html=True),
                _mail("x", "code 656565", multipart=True)):
        srv = FakeServer([("noreply@frappe.io", msg)])
        assert poller(srv)() in {"424242", "313131", "656565"}


def test_skips_newest_without_code_falls_back_to_older():
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "707070")),
                      ("noreply@frappe.io", _mail("Welcome", "no digits here"))])
    assert poller(srv)() == "707070"


def test_seen_mail_not_reused():
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "111222"))])
    p = poller(srv)
    assert p() == "111222"
    srv.msgs.append(("noreply@frappe.io", _mail("Login code", "333444")))
    assert p() == "333444"


def test_timeout_when_no_mail():
    import itertools
    from fcctl._sync import _io
    srv = FakeServer([])
    ticks = itertools.count(0, 50)
    p = poller(srv, timeout=120)
    orig = _io.now
    _io.now = lambda: next(ticks)
    try:
        with pytest.raises(TimeoutError):
            p()
    finally:
        _io.now = orig


def test_stale_mail_by_date_header_ignored():
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "111111", date=_date(-3600))),
                      ("noreply@frappe.io", _mail("Login code", "222222", date=_date(-20)))])
    assert poller(srv)() == "222222"  # within 30s skew
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "111111", date=_date(-3600)))])
    import itertools
    from fcctl._sync import _io
    ticks = itertools.count(0, 50)
    p = poller(srv, timeout=120)
    orig = _io.now
    _io.now = lambda: next(ticks)
    try:
        with pytest.raises(TimeoutError):
            p()
    finally:
        _io.now = orig


def test_preexisting_mail_excluded_by_uid_snapshot():
    srv = FakeServer([("noreply@frappe.io", _mail("Login code", "111111"))])
    p = _poller(srv)
    p.start()
    assert p.baseline == {b"1"}
    srv.msgs.append(("noreply@frappe.io", _mail("Login code", "999999")))
    assert p() == "999999"
    assert srv.seen == {2}


def test_login_with_code_calls_start_before_send_code():
    from fcctl.auth import login_with_code
    order = []

    class C:
        def call(self, *a, **k): order.append("send")

        http = type("H", (), {"send": staticmethod(lambda r: None)})
        def build(self, *a, **k): raise RuntimeError("stop")

    class P:
        def start(self): order.append("start")
        def __call__(self): return "123456"

    with pytest.raises(RuntimeError):
        login_with_code(C(), "a@b.c", P())
    assert order == ["start", "send"]


def test_frappecloud_sender_opened_mail_and_hex_colours():
    raw = _mail("157131 is your Frappe Cloud sign-in code", '<p style="color:#737373">157131</p>', html=True)
    srv = FakeServer([("Frappe Cloud <notifications@frappecloud.com>", raw)])
    srv.seen.add(1)  # already opened in Gmail: must still be picked
    assert poller(srv)() == "157131"
    body_only = _mail("Sign in", '<p style="color:#737373">Code: 424242</p>', html=True)
    assert poller(FakeServer([("notifications@frappecloud.com", body_only)]))() == "424242"


def test_extract_ignores_longer_digit_runs():
    assert ImapCodePoller.extract("id 1234567 code 123456") == "123456"
    assert ImapCodePoller.extract("nothing") is None
