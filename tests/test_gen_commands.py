import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("gen_commands", ROOT / "scripts" / "gen_commands.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_commands_md_is_current():
    assert _load().main.__module__
    mod = _load()
    assert (ROOT / "docs" / "COMMANDS.md").read_text() == mod.render()


def test_render_is_deterministic():
    mod = _load()
    assert mod.render() == mod.render()


def test_every_command_path_documented():
    from fcctl.cli.completion import build_tree
    text = (ROOT / "docs" / "COMMANDS.md").read_text()

    def paths(n, pre):
        for s in n["subs"]:
            p = pre + [s["name"]]
            yield p
            yield from paths(s, p)

    found = list(paths(build_tree()["root"], []))
    assert found
    for p in found:
        assert f"## fcctl {' '.join(p)}\n" in text, p
