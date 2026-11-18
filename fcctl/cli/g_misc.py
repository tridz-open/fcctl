"""reconcile, watch, alert test, doctor, api, guide, completion, version."""
from __future__ import annotations

import json
import os
import sys

GUIDE = """fcctl quick guide
  0. fcctl init                (first run: creates the encryption key, never printed)
  1. fcctl profile create main --email you@example.com --team TEAM && fcctl profile use main
  2. fcctl auth login          (email code; --code-stdin for scripts)
  3. fcctl auth whoami
  Writes are policy-checked; guarded ones need --confirm TARGET or a two-person approval
  (fcctl approval list / approve ID).  stdout is data only, progress and errors go to stderr.
  fcctl doctor [--api] checks your setup; fcctl audit verify checks the audit chain.
"""


def reconcile(ctx, args):
    from .. import reconcile as rc
    def pilot_for(e):
        try:
            return ctx.pilot((e.get("args") or {}).get("server") or None)
        except Exception:
            return None
    out = {"journal": rc.reconcile(ctx.journal, rc.default_resolvers(lambda: ctx.central, pilot_for))}
    try:
        out["markers"] = rc.clear_orphan_markers(ctx.guard, ctx.journal)
    except Exception as e:
        out["markers"] = {"error": str(e)}
    return out


def watch(ctx, args):
    from .._sync import _io
    n = 0
    while True:
        ctx.render(reconcile(ctx, args))
        n += 1
        if args.count and n >= args.count:
            return 0
        _io.sleep(args.interval)


def alert_test(ctx, args):
    from ..alerts import Alerter
    return Alerter(ctx.env).send("fcctl test alert", "This is a test alert from `fcctl alert test`.")


def _state_paths(home):
    """(path, wanted_mode) for private state that exists under FCCTL_HOME."""
    out = []
    if home.exists():
        out.append((home, 0o700))
        for d in ("audit", "journal", "profile"):
            if (home / d).is_dir():
                out.append((home / d, 0o700))
        for pat in ("config.toml", "session*.enc", "session.key", "approvals.json", "audit.jsonl"):
            out.extend((f, 0o600) for f in sorted(home.glob(pat)) if f.is_file())
    return out


def _loose(home):
    import stat
    bad = []
    for p, want in _state_paths(home):
        mode = stat.S_IMODE(p.stat().st_mode)
        if mode & ~want & 0o777 & (0o077 if want == 0o600 else 0o077):
            bad.append((p, want))
    return bad


def init(ctx, args):
    from ..keys import init_key
    status, where = init_key(ctx.env, ctx.home, args.store)
    steps = ["fcctl profile create main --email you@example.com --team TEAM-xxxx", "fcctl profile use main",
             "fcctl auth login"]
    if status == "exists":
        return {"status": "already initialised", "key": where, "home": str(ctx.home)}
    return {"status": "initialised", "key": f"stored in {where}", "home": str(ctx.home), "next": steps}


def doctor(ctx, args):
    from ..keys import KeyFileInsecure, init_key, resolve_key
    if args.fix:
        fixed = []
        try:
            if resolve_key(ctx.env, ctx.home)[1] == "none":
                init_key(ctx.env, ctx.home)
                fixed.append("generated key")
        except KeyFileInsecure:
            pass
        for p, want in _loose(ctx.home):
            os.chmod(p, want)
            fixed.append(f"chmod {want:o} {p}")
        for f in fixed:
            sys.stderr.write(f"fixed: {f}\n")
    checks = []

    def add(name, ok, detail="", fix=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})
    add("python", sys.version_info >= (3, 11), sys.version.split()[0])
    key, src, key_err = None, "none", ""
    try:
        key, src = resolve_key(ctx.env, ctx.home)
    except Exception as e:
        key_err = str(e)
    if key_err:
        add("encryption key", False, key_err, f"chmod 600 {ctx.home / 'session.key'}")
    else:
        add("encryption key", bool(key), f"from {src}" if key else "needed for session + approvals encryption",
            "run fcctl init")
    for p, want in _loose(ctx.home):
        add("permissions", False, f"{p} is not private", f"chmod {want:o} {p}")
    try:
        profs = ctx.profiles.list()
        add("config", True, f"{len(profs)} profile(s) in {ctx.profiles.path}")
    except Exception as e:
        profs = []
        add("config", False, str(e), f"fix the TOML syntax in {ctx.profiles.path}")
    p = ctx.profile_or_none
    add("profile selected", p is not None, p.name if p else "use --profile or `profile use`",
        "fcctl profile create <name> --email you@example.com --team TEAM-xxxx" if not profs
        else "fcctl profile use <name>")
    if p and key:
        try:
            add("session present", bool(ctx.store.sid), "run `fcctl auth login`" if not ctx.store.sid else "",
                "fcctl auth login")
        except Exception as e:
            add("session present", False, str(e), "fcctl auth login")
    if args.api and p:
        try:
            from .. import auth
            r = auth.keepalive(ctx.central)
            add("api reachable", True, (r or {}).get("email", "") if isinstance(r, dict) else "")
        except Exception as e:
            add("api reachable", False, str(e), "fcctl auth login")
    if not all(c["ok"] for c in checks):
        ctx.exit_code = 1
    return checks


def _read_body(data):
    """Resolve --data: '@file', '-' (stdin) or inline JSON. Never echoes the body in errors."""
    from ..errors import UsageError
    if not data:
        return None
    if data == "-":
        raw, src = sys.stdin.read(), "stdin"
    elif data.startswith("@"):
        path = data[1:]
        src = f"file {path!r}"
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except (OSError, UnicodeDecodeError) as e:
            why = e.strerror if isinstance(e, OSError) and e.strerror else type(e).__name__
            raise UsageError("cannot read --data file", what=f"could not read {src}", why=why,
                             fix="check the path exists and is readable UTF-8, or use --data - to read stdin")
    else:
        sys.stderr.write("warning: --data passed inline is visible in `ps` and shell history; "
                         "use --data @file or --data -\n")
        raw, src = data, "--data"
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise UsageError("invalid JSON body", what=f"{src} is not valid JSON",
                         why=f"parse error at line {e.lineno} column {e.colno} ({e.msg})",
                         fix="fix the JSON syntax and re-run")


def api(ctx, args):
    from .. import rawapi
    body = _read_body(args.data)
    path = args.path
    oc = ctx.op_context(args.plane)
    return rawapi.call_raw(oc, args.plane, args.method, path, body=body, confirm=args.confirm,
                           guarded=args.guarded, client=oc.clients[args.plane] if args.plane in oc.clients else None)


def guide(ctx, args):
    sys.stdout.write(GUIDE)
    return 0


def _commands(parser):
    for a in parser._actions:
        if hasattr(a, "choices") and isinstance(getattr(a, "choices", None), dict) and a.dest == "cmd":
            return sorted(a.choices)
    return []


def completion(ctx, args):
    cmds = " ".join(_commands(ctx.parser))
    if args.shell == "zsh":
        sys.stdout.write(f"#compdef fcctl\n_arguments '1:command:({cmds})'\n")
    else:
        sys.stdout.write(f'complete -W "{cmds}" fcctl\n')
    return 0


def version(ctx, args):
    from .. import __version__
    return {"fcctl": __version__, "python": sys.version.split()[0]}


def register(sub, common):
    x = sub.add_parser("reconcile", parents=[common], help="settle ambiguous journal entries")
    x.add_argument("--once", action="store_true"); x.set_defaults(func=reconcile)
    x = sub.add_parser("watch", parents=[common], help="reconcile on an interval")
    x.add_argument("--interval", type=float, default=60.0)
    x.add_argument("--count", type=int, default=0, help=argparse_suppress())
    x.set_defaults(func=watch)
    al = sub.add_parser("alert", parents=[common], help="alert channel tools").add_subparsers(dest="alert_cmd", required=True)
    al.add_parser("test", parents=[common], help="send a test alert").set_defaults(func=alert_test)
    x = sub.add_parser("init", parents=[common], help="first-run setup: create the encryption key and config dir")
    x.add_argument("--store", choices=["keyring", "file"], help="where to keep the key (default: keyring if available, else file)")
    x.set_defaults(func=init)
    x = sub.add_parser("doctor", parents=[common], help="check config, session and policy"); x.add_argument("--api", action="store_true")
    x.add_argument("--fix", action="store_true", help="apply safe fixes (create key, tighten permissions)")
    x.set_defaults(func=doctor)
    x = sub.add_parser("api", parents=[common], help="raw API call (classified, audited, guarded)")
    x.add_argument("plane", choices=["central", "pilot"]); x.add_argument("method")
    x.add_argument("path", help="central: dotted method; pilot: URL path")
    x.add_argument("--data", help="JSON body: @file reads it from a file, - reads stdin (keeps it out of ps/history); inline JSON works but warns"); x.add_argument("--guarded", action="store_true")
    x.set_defaults(func=api)
    sub.add_parser("guide", parents=[common], help="print the built-in usage guide").set_defaults(func=guide)
    x = sub.add_parser("completion", parents=[common], help="print a bash or zsh completion script"); x.add_argument("shell", choices=["bash", "zsh"])
    x.set_defaults(func=completion)
    sub.add_parser("version", parents=[common], help="print fcctl and Python versions").set_defaults(func=version)


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
