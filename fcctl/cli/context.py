"""Lazy per-command context. Nothing here does I/O until a property is touched."""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Any

# Tests (and embedders) may set this to an httpx.Client (e.g. FakeFrappeCloud.http()).
HTTP: Any = None


class Ctx:
    def __init__(self, args, parser=None, env=None):
        self.args, self.parser = args, parser
        self.env = os.environ if env is None else env
        self.exit_code = 0
        self._cache: dict[str, Any] = {}

    def _memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # ---- paths / config
    @property
    def home(self) -> Path:
        from .. import config
        return config.home(self.env)

    @property
    def profiles(self):
        from .. import config
        return self._memo("profiles", lambda: config.ProfileStore(config.config_path(self.env)))

    @property
    def profile(self):
        from .. import config
        return self._memo("profile", lambda: config.select_profile(self.args.profile, self.env, self.profiles))

    @property
    def profile_or_none(self):
        try:
            return self.profile
        except Exception:
            return None

    @property
    def eff(self):
        from .. import config
        return config.effective_policy(self.profile, self.profiles.list())

    @property
    def actor(self) -> str:
        return self.env.get("FCCTL_USER") or getpass.getuser()

    # ---- clients
    @property
    def store(self):
        def mk():
            from .. import config
            from ..session_store import SessionStore
            p = self.profile_or_none
            name = f"session-{config.session_key(p)}.enc" if p else "session.enc"
            key = self.env.get("FCCTL_KEY")
            return SessionStore(self.home / name, key, profile_dir=self.home / "profile")
        return self._memo("store", mk)

    @property
    def client(self):
        """The sync facade Client (fcctl._sync.client.Client); ctx.central/pilot come from it."""
        def mk():
            from .._sync.client import Client
            from ..alerts import Alerter
            from ..reauth import Reauther
            p = self.profile_or_none
            cl = Client(self.store, team=p.team if p else self.env.get("FCCTL_TEAM"), http=HTTP)
            Reauther(self.store, cl.central, alerter=Alerter(self.env))
            return cl
        return self._memo("client", mk)

    @property
    def central(self):
        return self.client.central

    def server_id(self) -> str:
        from ..errors import UsageError
        s = self.args.server or self.env.get("FCCTL_SERVER")
        if not s:
            raise UsageError("a server is required", what="no server given",
                             why="this command talks to a Pilot server",
                             fix="pass -s server-xxxx or set FCCTL_SERVER")
        return s

    def pilot(self, server: str | None = None):
        server = server or self.server_id()

        def mk():
            p = self.client.pilot(server, http=HTTP)
            if getattr(self.central, "reauth", None) is not None:
                self.central.reauth.pilot = p
            return p
        return self._memo(f"pilot:{server}", mk)

    def client_factory(self):
        def f(kind, server):
            return self.central if kind == "central" else self.pilot(server)
        return f

    # ---- safety plumbing
    @property
    def audit(self):
        from ..audit import AuditLog
        return self._memo("audit", lambda: AuditLog(self.home / "audit" / "audit.jsonl"))

    @property
    def journal(self):
        from ..journal import Journal
        return self._memo("journal", lambda: Journal(self.home / "journal"))

    @property
    def guard(self):
        def mk():
            from ..alerts import Alerter
            from ..guard import Guard
            return Guard(home=self.home, key=self.env.get("FCCTL_KEY"), secret=self.env.get("FCCTL_APPROVAL_SECRET", ""),
                         alerter=Alerter(self.env), user=self.actor)
        return self._memo("guard", mk)

    def op_context(self, plane: str = "central", **kw):
        """OpContext for ops.run_op / rawapi.call_raw."""
        from ..ops import OpContext
        try:
            guard = self.guard
        except Exception:
            guard = None
        clients = {"central": self.central}
        if self.args.server or self.env.get("FCCTL_SERVER"):
            clients["pilot"] = self.pilot()
        p = self.profile
        base = dict(eff=self.eff, journal=self.journal, guard=guard, audit=self.audit, alerter=None,
                    profile=p.name, actor=self.actor, dry_run=bool(self.args.dry_run),
                    confirm=self.args.confirm, kind=plane, server=self.args.server, clients=clients)
        base.update(kw)
        return OpContext(**base)

    # ---- progress / output
    @property
    def progress(self):
        from ..liveness import ProgressPrinter
        return ProgressPrinter(sys.stderr, no_progress=bool(self.args.no_progress))

    def info(self, msg: str) -> None:
        print(msg, file=sys.stderr)

    def render(self, data, columns=None) -> None:
        from .. import output
        fmt = self.args.output or output.default_format(sys.stdout)
        output.render(data, fmt, sys.stdout, columns)

    def close(self) -> None:
        pass
