"""Every `fcctl ...` line in a README code fence must parse with the real parser."""
import re
import shlex
from pathlib import Path

import pytest

from fcctl.cli.main import build_parser

README = (Path(__file__).resolve().parent.parent / "README.md").read_text()
# Placeholders argparse cannot satisfy are avoided in the README; keep this skip list minimal.
SKIP = ()


def _commands():
    out = []
    blocks, cur = [], None
    for ln in README.splitlines():
        if ln.startswith("```"):
            if cur is None:
                cur = []
            else:
                blocks.append("\n".join(cur))
                cur = None
        elif cur is not None:
            cur.append(ln)
    for block in blocks:
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip()
            m = re.search(r"(?:^|\|\s*|then\s+|if ! )(fcctl\s.*)$", line)
            if not m:
                continue
            cmd = re.sub(r"\s+#.*$", "", m.group(1))
            cmd = re.split(r"\s*(?:>|\|\||;|\bthen\b)", cmd)[0].strip()
            out.append(cmd)
    return out


CMDS = _commands()


def test_found_examples():
    assert len(CMDS) > 30


@pytest.mark.parametrize("cmd", CMDS)
def test_example_parses(cmd):
    if cmd in SKIP:
        pytest.skip("placeholder")
    argv = shlex.split(cmd)[1:]
    if argv[:1] in (["--version"], ["--help"]) or argv[-1:] == ["--help"]:
        with pytest.raises(SystemExit) as e:
            build_parser().parse_args(argv)
        assert e.value.code == 0
        return
    build_parser().parse_args(argv)
