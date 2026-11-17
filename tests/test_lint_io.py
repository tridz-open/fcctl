import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "fcctl"
PAT = re.compile(r"\b(time\.sleep|asyncio\.sleep)\b")
# TODO: migrate these legacy modules onto fcctl._async._io, then empty this list.
ALLOWLIST: set[str] = set()
ALLOWED_DIRS = ()  # TODO: same migration


def test_no_raw_sleep_in_core():
    bad = [str(p.relative_to(PKG)) for d in ("_async", "_sync") for p in (PKG / d).glob("*.py")
           if p.name != "_io.py" and PAT.search(p.read_text())]
    assert bad == []


def test_legacy_sleepers_are_allowlisted():
    bad = [str(p.relative_to(PKG)) for p in PKG.rglob("*.py")
           if p.relative_to(PKG).parts[0] not in ("_async", "_sync", *ALLOWED_DIRS)
           and str(p.relative_to(PKG)) not in ALLOWLIST and PAT.search(p.read_text())]
    assert bad == []
