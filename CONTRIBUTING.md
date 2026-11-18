# Contributing

fcctl is early alpha software and is not currently accepting pull requests or outside contributions. Pull requests will be closed without review.

**Issues are welcome.** If you find a bug or a problem, please [open a GitHub issue](https://github.com/tridz-open/fcctl/issues/new/choose).

## What to include in a bug report

- The version or release tag you are using (output of `fcctl version`).
- Your environment: OS, runtime version, and how you installed it.
- What you did (exact commands or code), what you expected, and what happened instead.
- The full error output or stack trace.
- A minimal reproduction if you can make one.
- **Remove secrets and account data first**: tokens, session cookies, API keys, emails, hostnames, team ids.

## Security issues

Do not file security problems as public issues. See [SECURITY.md](SECURITY.md).

## Use at your own risk

Alpha software, provided "as is" without warranty of any kind (see [LICENSE](LICENSE)).

## Development setup (for reference, and for your own fork)


### Setup

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/gen_sync.py --check
bash scripts/release_gate.sh
```

### Rules

- `fcctl/_async` is the source of truth. `fcctl/_sync` is generated: edit `_async`, then run `python scripts/gen_sync.py`. Never edit `_sync` by hand.
- Time and sleeping go through `_io` only (a test enforces it).
- Tests must not call the live Frappe Cloud API. Mock HTTP with respx or use `fcctl.testing.FakeFrappeCloud`.
- Never commit captured real responses, emails, hostnames, team ids, tokens or other account data. Use placeholders (`TEAM-00001`, `a.example.com`).
- Keep the User-Agent honest and never add code that works around bot protection.
- Run `bash scripts/release_gate.sh` before opening a PR; it fails on leaked identifiers.
