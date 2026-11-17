import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import gen_sync  # noqa: E402


def test_sync_is_generated_and_fresh(tmp_path):
    gen_sync.generate(tmp_path / "_sync")
    assert gen_sync.same(tmp_path / "_sync", ROOT / "fcctl" / "_sync")


def test_check_flag_passes():
    assert subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_sync.py"), "--check"]).returncode == 0


def test_subpackage_recursive(tmp_path):
    src = tmp_path / "_async"
    (src / "workflows").mkdir(parents=True)
    (src / "workflows" / "__init__.py").write_text("")
    (src / "workflows" / "w.py").write_text("async def f(c):\n    await c.aclose()\n")
    gen_sync.generate(tmp_path / "_sync", src)
    out = (tmp_path / "_sync" / "workflows" / "w.py").read_text()
    assert out.startswith(gen_sync.HEADER) and "def f(c):\n    c.close()" in out
    assert (tmp_path / "_sync" / "workflows" / "__init__.py").exists()
    assert gen_sync.same(tmp_path / "_sync", tmp_path / "_sync")
    (tmp_path / "_sync" / "workflows" / "extra.py").write_text("")
    gen_sync.generate(tmp_path / "other", src)
    assert not gen_sync.same(tmp_path / "_sync", tmp_path / "other")
