import importlib.util
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("mypy") is None, reason="mypy not installed")


def test_mypy_clean():
    r = subprocess.run([sys.executable, "-m", "mypy", "fcctl"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
