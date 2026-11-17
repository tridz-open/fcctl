# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `fcctl app add NAME --site S` (repeatable) fetches and installs NAME on each site via `POST /sites/{site}/apps {"app": NAME}`, the same call the dashboard makes. When the app isn't on the bench yet, Pilot runs `get-and-install-app`, which avoids the bench `get-app` validator that rejects apps such as india_compliance.

### Fixed
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

### Deprecated
- `fcctl.browser.Browser` and the `browser` extra (Playwright fallback). Emits `DeprecationWarning`; will be removed.

### Known limitations
- Uses private, undocumented dashboard endpoints that can change without notice.
- Many request bodies are unverified against the live API (see `docs/API_IA.md`).
- The approval step is a speed bump, not separation of duties: approver names are not authenticated.
- No PyPI release yet; install from a clone.
