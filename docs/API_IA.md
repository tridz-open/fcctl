# fcctl API coverage

What fcctl actually wraps on the Frappe Cloud dashboard (Central) and Pilot endpoints, split by how much evidence we have. These are private, undocumented endpoints; they can change without notice.

Every row below is implemented in this repository. Rows are only in section 1 when the call was run against the live API. Section 2 is implemented and covered by offline tests (respx and the in-process fake in `fcctl.testing`), but request bodies or response shapes have not been checked live. Section 3 is not implemented; it is a roadmap, not a promise.

Path notation. Central: `/api/v2/method/<dotted>`, where `api.x.y` means `central.api.x.y`, `bill.y` means `central.billing.api.dashboard.y` and `svc.storage.y` means `central.services.api.storage.y`. Pilot: `/api/v1/...` on the server's gateway, with a bearer token from `central.api.sso.get_bench_link` (never persisted, re-minted on 401). Guarded (G) means the call goes through the approval step under the default `guarded` policy.

Scope rule. Ids are arguments, never global state: the team is a client default, a server is the Central resource id (for example `server-abc123def4`), a site is its hostname, backups use their timestamp.

## 1. Implemented and verified live

| Capability | CLI | Plane | HTTP |
|---|---|---|---|
| Email-code login, keepalive | `auth login`, `auth keepalive` | Central | `api.auth.send_code`, `api.auth.verify_code`, `api.identity.my_profile` |
| Whoami, capabilities, teams | `auth whoami [--caps]` | Central | `api.identity.my_profile`, `my_capabilities`, `my_teams` |
| Pilot bench token | internal, `doctor` | Central | `api.sso.get_bench_link` |
| List and get servers | `server list`, `server get` | Central | `api.servers.registry`, `server_overview` |
| Server hostnames | `server get` | Central | `api.servers.server_hostnames` |
| Server images and offerings | `server images`, `server plans` | Central | `api.images.list_images`, `list_offerings`, `eligible_plans` |
| Server metrics | `server metrics` | Central | `api.servers.server_metrics` |
| Server activity | library only (`fcctl.servers.activity`) | Central | `api.activity.server_activity` |
| List snapshots | `snapshot list` | Central | `api.snapshots.list_snapshots` |
| List buckets, bucket usage | `bucket list`, `bucket usage` | Central | `svc.storage.get_object_storage`, `get_bucket_usage` |
| Billing reads (trust tier, balance, invoices, forecast and cycle costs) | `billing tier`, `balance`, `invoices`, `usage` | Central | `bill.get_trust_tier`, `get_credit_balance`, `credit_ledger`, `list_invoices`, `get_invoice`, `get_team_overview`, `get_cycle_costs`, `get_forecast`, `get_next_payment`, `get_collection_status` |
| Notifications | `notification list`, `notification read` | Central | `notif.list_notifications`, `notification_badge`, `mark_notification_read`, `mark_all_notifications_read` |
| Team activity | `audit remote` | Central | `api.activity.team_activity` |
| List and get sites | `site list`, `site get` | Pilot | `GET /sites`, `GET /sites/<n>` |
| Create a site | `site create` | Pilot | `POST /sites` |
| Rename a site | `site rename` | Pilot | `POST /sites/<n>/actions/rename` `{new_name, keep_old_hostname}` |
| Drop a site (G) | `site drop` | Pilot | `DELETE /sites/<n>` |
| List bench apps | `app list` | Pilot | `GET /apps`, `GET /sites/<n>/apps` |
| Fetch and install an app on a site | `app add NAME --site S`, `app install` | Pilot | `POST /apps/fetch`, `POST /sites/<n>/apps` |
| Site config get and set | `site config get/set` | Pilot | `GET/PATCH /sites/<n>/configuration` |
| Logs | `logs list`, `logs tail` | Pilot | `GET /logs`, `/logs/<f>?lines=N`, `/logs/<f>/events` |
| Backup create, list, schedule | `backup create`, `backup list` | Pilot | `GET/POST /sites/<n>/backups`, `GET/PUT /sites/<n>/backup-schedule` |
| Cross-site backup transfer (upload) | used by `site provision` | Pilot | `POST /sites/<n>/actions/restore-upload` and the upload routes |
| Tasks: status, events, output | `task list/get/wait/attach/output` | Pilot | `GET /tasks[/<id>]`, `/tasks/<id>/events`, `/output/content` |
| Provision workflow from a template site's backup (hooks, release order, health) | `site provision` | Pilot | composed from the calls above |

## 2. Implemented, not verified live

These ship, are tagged `[experimental]` in `--help` where they have a CLI command, and are listed in the README under Experimental commands. Treat request bodies as unverified.

| Capability | CLI | Plane | HTTP | Note |
|---|---|---|---|---|
| Add an app to the bench only | `app add NAME -s SRV` (no `--site`) | Pilot | `GET /marketplace/apps`, then `POST /apps {name, repo, branch, sites: []}` | the bench validator can reject it |
| Add an app from a git repo | `app add --repo URL` | Pilot | `POST /git/repository-resolutions`, then `POST /apps` | name resolution is contract-tested only |
| Check bench app updates | `app check-updates` | Pilot | `POST /apps/fetch` (task); result is the last JSON line of the task output | |
| Remove a bench app (G) | `app remove` | Pilot | `DELETE /apps/<a>` | route unverified |
| Uninstall an app from a site (G) | `app uninstall` | Pilot | `DELETE /sites/<n>/apps/<app>` | |
| Full backup restore (G) | `backup restore` | Pilot | `POST /sites/<n>/actions/restore` | the approval gate stopped the live attempt |
| Backup download links, offsite list | `backup download`, `backup offsite` | Pilot | `GET /sites/<n>/backups/<ts>/download-links`, `.../files/<id>/content`, `POST /sites/<n>/actions/remote-backups` | download-links returned 500 live (no offsite config) |
| Site maintenance mode, migrate, clear cache | `site maintenance`, `site migrate`, `site clear-cache` | Pilot | `PATCH /sites/<n>/configuration`, `POST /sites/<n>/actions/{migrate,clear-cache}` | |
| Site login link | `site login` | Pilot | `POST /sites/<n>/login` | |
| Site domains (read only) | `site domain list`, `site domain records` | Pilot | `GET /sites/<n>/domains`, `.../dns-records` | no add, remove or TLS |
| Site clone, build assets, update apps, switch branch | library only (`fcctl.sites`, `fcctl.apps`) | Pilot | `POST /sites/<n>/actions/clone`, `.../build-assets`, update and switch-branch routes | no CLI command |
| Cancel a task (G) | `task cancel` | Pilot | `DELETE /tasks/<id>` | |
| Create a server | `server create [--snapshot]` | Central | `api.servers.create_server` | idempotent via `--request-key` |
| Start, stop (G), restart, resize (G), termination protection, terminate (G) | `server start/stop/restart/resize/protect/terminate` | Central | `api.servers.start_server`, `stop_server`, `restart_server`, `resize_server`, `set_termination_protection`, `terminate_server` | resize refuses a disk shrink |
| Take and delete snapshots (G) | `snapshot take`, `snapshot delete` | Central | `api.snapshots.take_snapshot`, `delete_snapshots` | |
| Create, delete (G) a bucket, set quota | `bucket create/delete/quota` | Central | `svc.storage.create_bucket`, `delete_bucket`, `set_bucket_quota` | |
| Presigned object put and get | `bucket put/get/presign` | Central | `svc.storage.get_upload_url`, `get_object_url` | |
| Billing health | `billing health` | Central | computed from team overview fields | field names not live-verified |
| Raw API escape hatch | `api` | both | any route | |

Known gaps: there is no in-place snapshot restore in the API (clone to a new server only). CSRF refresh falls back to scraping the dashboard boot page, which is fragile.

## 3. Not implemented (roadmap)

Nothing here exists in the code, the CLI or the library. Use `fcctl api` for one-off calls. Each item would need a live check before it ships.

- Account and teams: profile update, team create, rename, archive, members, roles, invitations.
- Billing writes: top-ups, paying invoices, payment methods, projects, subscriptions, billing profile and settings.
- Servers: rename, firewall and rulesets, public IP, WireGuard, SSH keys, rescue mode, console, in-place restore, snapshot keep and automatic snapshots.
- Bench: start, stop, restart, clone, delete; app updates apply; git connection; task worker; settings; database diagnostics; migrations; bench SSH keys; WAF; editor and console.
- Sites: reinstall, enable TLS, domain add, remove and primary, backup delete, monitoring and uptime reads.
- Services: AI/LLM keys, bucket allowed origins, listing and deleting objects, credential rotation.
- Support tickets, trial signup, GitHub integration.
- Realtime events (Socket.IO), MCP server.

## 4. Conventions

- Destructive operations are guarded: they file a signed approval and exit 7 until approved. `--dry-run` prints the would-be request for any write.
- Errors are typed (`AuthError`, `CsrfError`, `CloudflareChallenge`, `ApiError`, `PendingApproval`, and others), each with an exit code. A Cloudflare challenge is raised, never solved.
- Pilot tasks are followed over SSE (`/tasks/<id>/events`) with a poll fallback. Central actions poll the server status.
- A journal entry is written before every mutating request, and `reconcile` resolves in-flight entries. The contract guard `tests/test_pilot_contract.py` replays every public Pilot function against `tests/fixtures/pilot_dashboard_contract.json` (refresh with `just contract-refresh <host>`).
- Auth: Central session `sid` plus `X-Frappe-CSRF-Token`; Pilot bearer re-minted on 401. The `User-Agent` is `fcctl/<version>`.
