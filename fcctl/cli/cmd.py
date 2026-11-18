"""Registration helpers: every command and group needs help text and an Examples epilog.

Commands are declared through ``add_cmd`` / ``add_group`` so that a command without a
one-line ``help`` or at least one ``fcctl ...`` example cannot be registered (it raises
at import time, and tests/test_cli_help.py walks the whole tree to enforce the same).
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence

GLOBAL_HINT = "Global options (--profile, --server, --output, --dry-run, ...) are listed in `fcctl --help`."


def _examples(example: str | Sequence[str]) -> list[str]:
    items = [example] if isinstance(example, str) else list(example)
    items = [e.strip() for e in items if e and e.strip()]
    if not items:
        raise ValueError("at least one example is required")
    for e in items:
        if not e.startswith("fcctl "):
            raise ValueError(f"example must start with 'fcctl ': {e!r}")
    return items


def _epilog(example: str | Sequence[str]) -> str:
    return "Examples:\n" + "\n".join(f"  {e}" for e in _examples(example)) + "\n\n" + GLOBAL_HINT


def _check_help(name: str, help: str) -> str:
    if not help or not help.strip() or help is argparse.SUPPRESS:
        raise ValueError(f"command {name!r} needs a non-empty help= line")
    return help.strip()


def add_cmd(sub, common, name: str, help: str, example: str | Sequence[str], *,
            description: str | None = None, aliases: Sequence[str] = ()) -> argparse.ArgumentParser:
    """Add a leaf command (or a group parser, see add_group) with help, description and Examples."""
    help = _check_help(name, help)
    return sub.add_parser(name, parents=[common], help=help, description=description or help,
                          epilog=_epilog(example), formatter_class=argparse.RawDescriptionHelpFormatter,
                          aliases=list(aliases))


def add_group(sub, common, name: str, help: str, example: str | Sequence[str], dest: str, *,
              description: str | None = None):
    """Add a command group; returns its (required) subparsers action."""
    g = add_cmd(sub, common, name, help, example, description=description)
    return g.add_subparsers(dest=dest, required=True, metavar="COMMAND")
