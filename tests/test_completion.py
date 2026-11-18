"""Shell completion scripts are generated from the argparse tree and must not drift from it."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from fcctl.cli import completion as C
from fcctl.cli.main import build_parser, main


@pytest.fixture(scope="module")
def tree():
    return C.build_tree(build_parser())


@pytest.fixture(scope="module")
def scripts():
    return {sh: C.generate(sh, build_parser()) for sh in C.SHELLS}


def _cli(shell, capsys):
    assert main(["completion", shell]) == 0
    return capsys.readouterr().out


@pytest.mark.parametrize("shell", C.SHELLS)
def test_command_output_equals_regenerated_from_tree(shell, scripts, capsys):
    assert _cli(shell, capsys) == scripts[shell]


@pytest.mark.parametrize("shell", C.SHELLS)
def test_every_command_and_long_flag_present(shell, tree, scripts):
    s = scripts[shell]
    for path in C.paths(tree):
        if shell == "bash":
            assert f'"{" ".join(path[:-1])}")' in s and path[-1] in s
        elif shell == "zsh":
            assert C._zfn(path) + "() {" in s
        else:
            parent = " ".join(path[:-1])
            assert f"-n '__fcctl_at \"{parent}\"' -a {path[-1]}" in s
    flags = C.long_flags(tree)
    assert "--output" in flags and "--dry-run" in flags and "--data" in flags
    for f in flags:
        needle = f[2:] if shell == "fish" else f
        assert needle in s, f
    assert len(C.paths(tree)) > 50


def test_choices_present(scripts):
    assert "table json yaml ndjson" in scripts["bash"]
    assert "(table json yaml ndjson)" in scripts["zsh"]
    assert "table json yaml ndjson" in scripts["fish"]
    for sh in C.SHELLS:
        assert "bash zsh fish" in scripts[sh].replace("(bash zsh fish)", "bash zsh fish")


def test_bash_syntax(scripts, tmp_path):
    f = tmp_path / "fcctl.bash"
    f.write_text(scripts["bash"])
    r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_syntax(scripts, tmp_path):
    f = tmp_path / "_fcctl"
    f.write_text(scripts["zsh"])
    r = subprocess.run(["zsh", "-n", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not shutil.which("fish"), reason="fish not installed")
def test_fish_syntax(scripts, tmp_path):
    f = tmp_path / "fcctl.fish"
    f.write_text(scripts["fish"])
    r = subprocess.run(["fish", "-n", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _bash_complete(script, tmp_path, words):
    f = tmp_path / "fcctl.bash"
    f.write_text(script)
    arr = " ".join("'" + w + "'" for w in words)
    code = (f'source "{f}"; COMP_WORDS=({arr}); COMP_CWORD=$(({len(words)} - 1)); '
            f'COMP_LINE="${{COMP_WORDS[*]}}"; _fcctl; printf "%s\\n" "${{COMPREPLY[@]}}"')
    r = subprocess.run(["bash", "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_bash_top_level_and_nested_verbs(scripts, tmp_path):
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", ""])
    assert {"site", "server", "completion"} <= set(out)
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", "site", ""])
    assert {"drop", "list", "config", "domain"} <= set(out)
    assert "server" not in out
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", "site", "config", ""])
    assert set(out) == {"get", "set"}
    assert _bash_complete(scripts["bash"], tmp_path, ["fcctl", "si"]) == ["site"]


def test_bash_flags_global_anywhere_and_choices(scripts, tmp_path):
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", "site", "drop", "--"])
    assert "--output" in out and "--dry-run" in out and "--profile" in out
    # global flag before the command still resolves the nested path
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", "--dry-run", "-o", "json", "site", ""])
    assert "drop" in out
    assert set(_bash_complete(scripts["bash"], tmp_path, ["fcctl", "-o", ""])) == {"table", "json", "yaml", "ndjson"}
    assert _bash_complete(scripts["bash"], tmp_path, ["fcctl", "site", "list", "--output=y"]) == ["--output=yaml"]
    assert set(_bash_complete(scripts["bash"], tmp_path, ["fcctl", "completion", ""])) == {"bash", "zsh", "fish"}


def test_bash_data_file_completion(scripts, tmp_path):
    (tmp_path / "body.json").write_text("{}")
    out = _bash_complete(scripts["bash"], tmp_path, ["fcctl", "api", "central", "POST", "x", "--data", "@bo"])
    assert out == ["@body.json"]
