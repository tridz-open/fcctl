"""argparse root, global flags, group auto-discovery, error/exit mapping, rendering."""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
import traceback

GLOBAL_DEFAULTS = {"profile": None, "server": None, "output": None, "wait": None, "timeout": None,
                   "dry_run": False, "debug": False, "no_color": False, "yes": False, "confirm": None,
                   "no_progress": False, "max_retries": None}


def _wait_arg(v):
    try:
        n = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--wait takes an optional number of seconds, got {v!r}") from None
    return False if n <= 0 else n


def add_global_flags(p, *, show: bool = True) -> None:
    """Register the global flags. With show=False (sub-command parsers) they parse but stay out of
    usage/help, so only the root `fcctl --help` lists them. Defaults are SUPPRESS in both places."""
    S = argparse.SUPPRESS
    tgt = p.add_argument_group("global options (accepted before or after the command)") if show else p

    def h(text):
        return text if show else S
    tgt.add_argument("--profile", default=S, help=h("profile name (else FCCTL_PROFILE or `profile use`)"))
    tgt.add_argument("-s", "--server", default=S, help=h("Frappe Cloud server id (server-xxxx)"))
    tgt.add_argument("-o", "--output", default=S, choices=["table", "json", "yaml", "ndjson"],
                     help=h("output format (non-TTY default: json)"))
    tgt.add_argument("--wait", dest="wait", nargs="?", const=True, default=S, type=_wait_arg, metavar="SECS",
                     help=h("wait for tasks (default); `--wait 0` = no wait, `--wait N` = wait up to N seconds"))
    tgt.add_argument("--no-wait", dest="wait", action="store_false", default=S, help=h("return after submit"))
    tgt.add_argument("--timeout", type=float, default=S,
                     help=h("wait timeout in seconds (default 1800 for server/snapshot, 3600 for tasks); exit 8 on timeout"))
    tgt.add_argument("--dry-run", action="store_true", default=S, help=h("show what a write would do"))
    tgt.add_argument("--debug", action="store_true", default=S, help=h("show tracebacks"))
    tgt.add_argument("--no-color", action="store_true", default=S, help=h("disable colored output"))
    tgt.add_argument("--yes", action="store_true", default=S, help=h("skip prompts (never bypasses guard/policy)"))
    tgt.add_argument("--confirm", nargs="?", const=True, default=S, metavar="TARGET",
                     help=h("confirm a guarded write (echo the target)"))
    tgt.add_argument("--max-retries", type=int, default=S, metavar="N",
                     help=h("retries on 429/5xx/network errors for idempotent requests (default 3, env FCCTL_MAX_RETRIES; 0 disables)"))
    tgt.add_argument("--no-progress", action="store_true", default=S, help=h("no progress on stderr"))


def _version() -> str:
    from .. import __version__ as v  # type: ignore[attr-defined]
    return v


def discover(subparsers, common) -> list[str]:
    from . import __path__ as pkg_path
    loaded = []
    for m in sorted(pkgutil.iter_modules(pkg_path), key=lambda m: m.name):
        if not m.name.startswith("g_"):
            continue
        try:
            mod = importlib.import_module(f"{__package__}.{m.name}")
            mod.register(subparsers, common)
            loaded.append(m.name)
        except Exception as e:  # a broken group must not take the whole CLI down
            print(f"warning: command group {m.name} failed to load: {type(e).__name__}: {e}", file=sys.stderr)
    return loaded


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        "fcctl", description="Frappe Cloud control CLI: sites, servers, backups, billing and guarded writes.",
        epilog="Examples:\n  fcctl profile list\n  fcctl --dry-run site drop demo.example.com\n"
               "  fcctl -o table site list\n\nRun `fcctl COMMAND --help` for a command's options and examples.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_global_flags(root)
    root.add_argument("--version", action="version", version=f"fcctl {_version()}")
    common = argparse.ArgumentParser(add_help=False)
    add_global_flags(common, show=False)
    sub = root.add_subparsers(dest="cmd", metavar="COMMAND")
    discover(sub, common)
    root.set_defaults(_parser=root)
    return root


def _fail(exc: BaseException, debug: bool) -> int:
    from ..errors import exit_code_for, format_error
    if isinstance(exc, KeyboardInterrupt):
        print("interrupted", file=sys.stderr)
        return 130
    print(format_error(exc), file=sys.stderr)
    if debug:
        from ..redact import redact_text
        sys.stderr.write(redact_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))
    return exit_code_for(exc)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # --help / --version / usage errors
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 2)
    for k, v in GLOBAL_DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)
    if isinstance(args.wait, float):  # `--wait N` = wait, bounded by N seconds
        if args.timeout is None:
            args.timeout = args.wait
        args.wait = True
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return 2
    if args.max_retries is not None:
        from .. import retry
        retry.set_default(retry.from_env().with_max_retries(args.max_retries))
    from .context import Ctx
    if args.debug:
        from ..redact import install_debug_logging
        install_debug_logging()
    ctx = Ctx(args, parser=parser)
    try:
        data = func(ctx, args)
    except BaseException as e:  # noqa: BLE001
        if isinstance(e, SystemExit):
            raise
        from ..errors import FcError
        from ..guard import GuardError
        from ..session_store import SessionStoreError
        if isinstance(e, (FcError, GuardError, SessionStoreError, KeyboardInterrupt)):
            return _fail(e, args.debug)
        return _fail(e, bool(args.debug))
    finally:
        ctx.close()
    if isinstance(data, int) and not isinstance(data, bool):
        return data
    if data is not None:
        ctx.render(data)
    return ctx.exit_code
