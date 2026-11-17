# Contributing

## Setup

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/gen_sync.py --check
bash scripts/release_gate.sh
```

## Rules

- `fcctl/_async` is the source of truth. `fcctl/_sync` is generated: edit `_async`, then run `python scripts/gen_sync.py`. Never edit `_sync` by hand.
- Time and sleeping go through `_io` only (a test enforces it).
- Tests must not call the live Frappe Cloud API. Mock HTTP with respx or use `fcctl.testing.FakeFrappeCloud`.
- Never commit captured real responses, emails, hostnames, team ids, tokens or other account data. Use placeholders (`TEAM-00001`, `a.example.com`).
- Keep the User-Agent honest and never add code that works around bot protection.
- Run `bash scripts/release_gate.sh` before opening a PR; it fails on leaked identifiers.
