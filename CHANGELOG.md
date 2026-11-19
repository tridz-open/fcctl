# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Docs: the prepared site whose backup seeds new tenant sites is now called a template site (was "golden backup").

## [0.1.0a4] - 2026-11-18

### Added
- `fcctl init` creates the session key on first run (OS keyring via the optional `fcctl[keyring]` extra, else a 0600 file); `FCCTL_KEY` still wins. `doctor` suggests an exact fix per failed check and `doctor --fix` applies the safe ones. Read-only `--dry-run` works without a key.
- `api --data @file` and `--data -` (stdin); inline bodies print a warning.
- Retry with exponential backoff and jitter on 429, 5xx and network errors for idempotent requests (GET/HEAD/OPTIONS and writes with an idempotency key), honouring `Retry-After`; `--max-retries` and `FCCTL_MAX_RETRIES`.
- Optional Ed25519 signed approvals: `fcctl approval keygen` and `sign`, `FCCTL_APPROVER_PRIVATE_KEY[_FILE]`, per-profile `approver_pubkeys`, and `--require-signed`.
- Library facade: `unsafe=True` (audited) opt-out on `Sites.drop` and `Backups.restore`; guard model documented.
- Full bash, zsh and fish completion for subcommands and flags, generated from the parser.
- Help line and `Examples:` on every subcommand; generated `docs/COMMANDS.md` with a CI drift check; exit-code table in the README.
- Single `redact()` helper applied to errors, `--debug` output, audit, journal and dry-run output.
- mypy in CI (clean; strict on errors, policy, guard, request, ops), `py.typed`, coverage floor (83%), macOS runner and Python 3.14 in CI.

### Changed
- Every error class and common failure carries specific what/why/fix text.
- Unclassified `api` writes require approval under the `guarded` and `protected` policies; they replay as op `api.raw`.
- `--dry-run` of named guarded commands now shows the HTTP requests; `site provision --dry-run` no longer writes a journal.
- `--debug` no longer re-raises unexpected exceptions raw (they go through the redacted error path).
- Journal arguments are redacted on disk.
- Global flags are listed once in root `--help`.

## [0.1.0a3] - 2026-11-18

### Changed
- Alpha cleanup (#9): removed the unverified browser code path, tagged commands that have not been run against the live API as `[experimental]` in `--help` (listed in the README), and rewrote `docs/API_IA.md` to separate verified, unverified and not-implemented endpoints.
- Server and bucket region now come from `--region`, then the `FCCTL_REGION` environment variable, then the `bom` default.

### Documentation
- README rewritten for first-time readers: alpha banner, what fcctl is and who it is for, install from a release tag, first-run walkthrough, copy-pasteable examples for each verified command, experimental commands table, approval section, library example, production note, related repositories and known gaps.

## [0.1.0a2] - 2026-11-18

### Changed
- `provision` workflow: new final `release` step (after `hooks`/`config`, before `health`) lifts `maintenance_mode` and `pause_scheduler` set by `isolate`/`reisolate`; it is idempotent (no write if the template already did it) and skipped with `keep_isolated=True`. Previously sites stayed at 503 unless the template added `core.maintenance_off` and `core.scheduler_resume`. `keep_isolated` is a runtime option, not part of the journaled recipe, so journals from before this step resume and run it.
- `core.set_host_name` and `core.set_fresh_encryption_key` now raise `UsageError` ("Pilot manages host_name; remove core.set_host_name from the template"): Pilot rejects both keys with 422 `protected_configuration`. They stay registered so old templates fail fast.

### Added
- Pilot contract guard: `tests/test_pilot_contract.py` checks every public Pilot call (method, path, body and query keys) against the dashboard API extracted by `scripts/extract_pilot_contract.py` (`just contract-refresh`, weekly `contract-refresh` workflow diffs it), plus a fake-conformance check; `DELETE /apps/{}` (`remove_app`) is allowlisted as unverified (P8).
- `--dry-run` now also prints the request that would be sent (`request`: method, path, params, body; `requests` lists all, max 10) for write ops; existing keys are unchanged and nothing is written.
- `fcctl app add NAME --site S` (repeatable) fetches and installs NAME on each site via `POST /sites/{site}/apps {"app": NAME}`, the same call the dashboard makes. When the app isn't on the bench yet, Pilot runs `get-and-install-app`, which avoids the bench `get-app` validator that rejects apps such as india_compliance.

### Fixed
- Pilot path segments (site, app, log, task, backup timestamp) are now percent-encoded via `pilot.seg`, so names with `/` or spaces cannot alter the route; URL-safe names are unchanged. `monitor_status` docstring now reads "monitor log sources".
- `provision` preflight `running_tasks` now lists `/tasks` without a status filter and keeps `queued` and `running` tasks (queued tasks were missed; `pending` is not a Pilot status).
- `backups.set_schedule` always sends `retention`: when none is given it reads the current schedule first and echoes its retention, so a PUT can no longer reset it.
- `sites.rename_site` now posts `{"new_name", "keep_old_hostname"}` (Pilot reads `new_name`; the old `name`/`make_primary` body was ignored); `make_primary` is dropped. New CLI `fcctl site rename SITE NEW [--keep-old-hostname]` (guarded write, op `site.rename`).
- `logs.content` with `lines` now tails via `GET /logs/{f}?lines=N` (joined with newlines) instead of silently downloading the whole file; without `lines` it still downloads `/content`.
- `transfer` now reads `size_bytes` (legacy `size` still accepted) and classifies parts by `kind` (`database`, `public-file`, `private-file`) with a filename fallback, fixing KeyError in cross-server restore and `provision --from-backup`. The fake backup listing uses the live shape.
- `apps.add_marketplace` with `sites` now installs per site (`POST /sites/{site}/apps {"app"}`) and never posts `/apps`; without sites it resolves `GET /marketplace/apps` (UsageError if missing or not installable) and posts `{name, repo, branch, sites: []}`. `app add` help recommends `--site`.
- `apps.add_repo` (and `add_in_order` repo specs) now resolve the app name via `POST /git/repository-resolutions` before `POST /apps`, instead of sending no name (which made the repo URL the app name and failed get-app); an explicit `name=` skips the resolution.
- `apps.check_updates` now posts `/apps/fetch` (the old `/app-update-checks` route does not exist) and returns the task id; new `apps.check_updates_result` parses the task output, and `fcctl app check-updates` waits and prints the `{app: {...}}` map. Policy now classes it as a write (POST).

## [0.1.0a0] - 2026-11-17

First public alpha.

### Added
- Async and sync clients for Frappe Cloud Central (team, billing, servers, snapshots, buckets) and Pilot (sites, apps, backups, tasks, logs), with a generated sync twin (`scripts/gen_sync.py`).
- `fcctl` CLI: auth, profile, server, snapshot, bucket, billing, notification, app, site, backup, task, logs, fleet, approval, audit, reconcile, watch, alert, doctor, api, guide.
- Safety layer: per-profile policy, approval step for destructive operations (RFC 6238 TOTP compatible with authenticator apps), operation journal with reconcile, hash-chained audit log.
- Email-code login: manual, `--code-stdin`, or IMAP.
- `fcctl.testing.FakeFrappeCloud`: in-process fake of Central and Pilot for offline tests.
- State directory created 0700 with files 0600; honest `User-Agent`.

### Removed before first release
- Playwright browser support (`fcctl.browser`, the `browser` extra, the `CentralClient.fallback` hook, `Reauther(browser=...)`). fcctl never solves challenges; login is the plain HTTP email-code path.
- Unused and unverified helpers: `snapshots.keep/set_automatic/pricing/restore_as_new_server`, `servers.clone_from_snapshot/action_status`, `objects.list_objects/delete_objects/rotate_credentials/s3_copy` and the `s3` extra, `monitor.monitor_status` (the route returns a log-file list, not a status), and the legacy `size` fallback for backup transfers.

### Known limitations
- Uses private, undocumented dashboard endpoints that can change without notice.
- Many request bodies are unverified against the live API (see `docs/API_IA.md`).
- The approval step is a speed bump, not separation of duties: approver names are not authenticated.
- No PyPI release yet; install from a clone.
