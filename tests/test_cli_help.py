"""Every command in the argparse tree has help, a description and a runnable Examples epilog."""
from __future__ import annotations

import argparse
import pkgutil
import shlex

import pytest

from fcctl import cli as cli_pkg
from fcctl.cli.main import build_parser


def _walk(parser, path=()):
    """Yield (path, parser, help_text_in_parent) for every sub-command, depth first."""
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {ca.dest: ca.help for ca in action._choices_actions}
        seen = set()
        for name, sp in action.choices.items():
            if id(sp) in seen:
                continue
            seen.add(id(sp))
            yield path + (name,), sp, helps.get(name)
            yield from _walk(sp, path + (name,))


def _examples(parser):
    ep = parser.epilog or ""
    assert ep.startswith("Examples:"), "epilog must start with 'Examples:'"
    return [ln.strip() for ln in ep.splitlines()[1:] if ln.startswith("  fcctl ")]


NODES = list(_walk(build_parser()))
IDS = [" ".join(p) for p, _, _ in NODES]


def test_tree_is_not_empty_and_all_groups_loaded(capsys):
    build_parser()
    assert "failed to load" not in capsys.readouterr().err
    modules = {m.name for m in pkgutil.iter_modules(cli_pkg.__path__) if m.name.startswith("g_")}
    assert len(modules) >= 16
    top = {p[0] for p, _, _ in NODES}
    assert {"site", "server", "backup", "api", "doctor", "completion"} <= top


@pytest.mark.parametrize("node", NODES, ids=IDS)
def test_command_has_help_description_and_examples(node):
    path, sp, parent_help = node
    assert parent_help and parent_help.strip(), f"{' '.join(path)}: no help= line in the parent listing"
    assert sp.description and sp.description.strip(), f"{' '.join(path)}: no description"
    assert isinstance(sp.formatter_class(prog="x"), argparse.RawDescriptionHelpFormatter)
    assert _examples(sp), f"{' '.join(path)}: no 'fcctl ...' example under Examples:"


@pytest.mark.parametrize("node", NODES, ids=IDS)
def test_examples_parse_and_resolve_to_this_command(node):
    path, sp, _ = node
    for ex in _examples(sp):
        argv = shlex.split(ex)[1:]
        parser = build_parser()  # fresh: parse_args mutates nothing global, but keep it isolated
        try:
            ns = parser.parse_args(argv)
        except SystemExit as e:  # pragma: no cover - failure path
            raise AssertionError(f"example does not parse: {ex}") from e
        assert callable(getattr(ns, "func", None)), f"example does not reach a runnable command: {ex}"


def test_root_lists_global_flags_but_subcommands_hide_them(capsys):
    root = build_parser().format_help()
    for flag in ("--dry-run", "--profile", "--output", "--confirm"):
        assert flag in root
    for path, sp, _ in NODES:
        h = sp.format_help()
        for flag in ("--dry-run", "--profile", "--max-retries"):
            assert flag not in h.split("Examples:")[0], f"{' '.join(path)} repeats global flag {flag}"


def test_global_flags_still_parse_after_the_subcommand():
    ns = build_parser().parse_args(["site", "list", "--dry-run", "-o", "table", "--profile", "p"])
    assert ns.dry_run is True and ns.output == "table" and ns.profile == "p"
    ns = build_parser().parse_args(["--dry-run", "site", "list"])
    assert ns.dry_run is True


def test_add_cmd_rejects_missing_help_or_example():
    from fcctl.cli.cmd import add_cmd
    root = argparse.ArgumentParser()
    sub = root.add_subparsers()
    common = argparse.ArgumentParser(add_help=False)
    with pytest.raises(ValueError):
        add_cmd(sub, common, "x", "", "fcctl x")
    with pytest.raises(ValueError):
        add_cmd(sub, common, "x", "help", [])
    with pytest.raises(ValueError):
        add_cmd(sub, common, "x", "help", "other thing")
