import importlib.util
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy not installed")


def test_mypy_clean():
    r = subprocess.run([sys.executable, "-m", "mypy", "fcctl"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


STRICT_MODULES = {"fcctl.errors", "fcctl.policy", "fcctl.guard", "fcctl.request", "fcctl.ops",
                  "fcctl.retry", "fcctl.redact"}


def test_strict_override_block_present():
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((root / "pyproject.toml").read_text())
    covered = set()
    for ov in cfg["tool"]["mypy"]["overrides"]:
        mods = ov["module"] if isinstance(ov["module"], list) else [ov["module"]]
        if ov.get("disallow_untyped_defs") and ov.get("disallow_any_generics") and ov.get("warn_return_any"):
            covered.update(mods)
    assert STRICT_MODULES <= covered
    assert (root / "fcctl" / "py.typed").exists()
    assert "Typing :: Typed" in cfg["project"]["classifiers"]
    assert cfg["tool"]["setuptools"]["package-data"]["fcctl"] == ["py.typed"]
