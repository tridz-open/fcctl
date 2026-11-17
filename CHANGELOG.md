# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `fcctl app add NAME --site S` (repeatable) fetches and installs NAME on each site via `POST /sites/{site}/apps {"app": NAME}`, the same call the dashboard makes. When the app isn't on the bench yet, Pilot runs `get-and-install-app`, which avoids the bench `get-app` validator that rejects apps such as india_compliance.

### Fixed
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

### Deprecated
- `fcctl.browser.Browser` and the `browser` extra (Playwright fallback). Emits `DeprecationWarning`; will be removed.

### Known limitations
- Uses private, undocumented dashboard endpoints that can change without notice.
- Many request bodies are unverified against the live API (see `docs/API_IA.md`).
- The approval step is a speed bump, not separation of duties: approver names are not authenticated.
- No PyPI release yet; install from a clone.
