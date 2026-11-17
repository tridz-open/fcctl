# Frappe Cloud v2: API documentation IA and SDK resource tree

Method catalogue of the Frappe Cloud dashboard (Central) and Pilot endpoints that fcctl wraps, and the resource tree it exposes. Verified entries were checked against the live API during development; the rest are from reading source.

Legend. Path prefix: Central = `/api/method/<dotted>` (C:`api.x.y` means `central.api.x.y`; C:`bill.y` = `central.billing.api.dashboard.y`; C:`svc.storage.y` = `central.services.api.storage.y`; C:`notif.y` = `central.notification.api.y`). Pilot = `/api/v1/...` on the bench host (bearer token from `sso.get_bench_link`). Verified: V = live 2026-11-16, S = source only (route via `fcctl api` until verified), R = read verified, W = write unverified. Destructive: G = guarded, g = candidate guard (not in v1 guard list, proposed), blank = no. v1 = in the v1 command groups.

## 1. Scope hierarchy

```
client
 ├─ auth            (transport)      send_code, verify_code, bench link, sessions
 ├─ account         (identity)       profile, capabilities, my teams/invitations
 ├─ teams           (tenant)         members, roles, invitations, settings
 │   ├─ billing     (team)           overview, wallet, invoices, payments, projects, subscriptions, profile
 │   ├─ infra       (team, read)     regions/images/apps/plans catalog
 │   ├─ servers     (team)           lifecycle, power, resize, network, access, snapshots, metrics, activity
 │   │   └─ bench   (via Pilot)      apps, updates, git, task worker, settings, database, logs, migrations
 │   │       └─ sites                lifecycle, apps, backups, restore, clone, config, domains/TLS, login, monitoring
 │   ├─ services    (team)           storage buckets/objects, ai (LLM)
 │   ├─ observability               notifications, activity, metrics, realtime, logs, tasks
 │   ├─ support
 │   └─ trial       (internal/product signup)
```
Rule: the SDK exposes the tree as attributes; scope ids are arguments, never global state: `client.servers.get(server)`, `client.bench(server).sites.get(name)`. The team is a client-level default (`client.team`), overridable per call (`team=`). `client.bench(server)` obtains a Pilot client by minting a bench token (`server:console`).

## 2. Mapping tables

Columns: SDK method | CLI | plane | HTTP | capability/scope | destr. | status.

### 2.1 Auth and transport (`auth`, v1)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| auth.send_code(email) | fcctl auth login | C | POST api.auth.send_code | public | | V |
| auth.verify_code(email, code) | fcctl auth login | C | POST api.auth.verify_code | public | | V |
| auth.sign_up_with_invitation | (later) | C | POST api.auth.sign_up_with_invitation | public | | S |
| auth.keepalive() | fcctl auth keepalive | C | GET api.identity.my_profile | session | | V |
| auth.bench_link(server) | (internal; `fcctl doctor`) | C | POST api.sso.get_bench_link | server:console | | V |
| pilot.auth.session.get/delete | fcctl auth status/logout | P | GET/DELETE /auth/session | bench | | S |
| pilot.auth.sessions.list/revoke/revoke_all | (later) | P | GET /auth/sessions; POST /auth/sessions/revoke/<jti>, /revoke/all | bench | g | S |
| pilot.auth.password / two_factor.* | (later, cut v1) | P | POST /auth/password; /auth/two-factor... | bench | | S |

### 2.2 Account (`account`, v1 via whoami)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| account.profile.get | fcctl auth whoami | C | api.identity.my_profile | session | | V |
| account.profile.update / set_photo | later | C | api.identity.update_profile / set_profile_photo | session | | S |
| account.capabilities.list | fcctl auth whoami --caps | C | api.identity.my_capabilities | session | | R |
| account.teams.list | fcctl auth whoami | C | api.identity.my_teams | session | | R |
| account.invitations.list | later | C | api.identity.my_invitations | session | | S |

### 2.3 Teams (`teams`, later; read subset via whoami)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| teams.create / rename / archive / leave | fcctl team ... | C | api.teams.create_team / rename_team / archive_team / leave_team | team:edit / team:delete | archive: g | S |
| teams.archive_checks | | C | api.teams.get_team_archive_checks | team:delete | | S |
| teams.transfer_ownership | | C | api.teams.transfer_team_ownership | owner | g | S |
| teams.set_logo / set_onboarding_step | | C | api.teams.set_team_logo / set_onboarding_step | team:edit | | S |
| teams.members.list / remove | | C | api.teams.list_team_members / remove_team_member | team:manage_members | remove: g | S |
| teams.members.set_roles / set_status | | C | api.teams.set_team_member_roles / set_team_member_status | team:manage_members | | S |
| teams.roles.list / create / delete | | C | api.teams.list_team_roles / create_custom_role / delete_custom_role | team:manage_members | delete: g | S |
| teams.invitations.create / resend / revoke / get / accept / decline | | C | api.teams.invite_team_member / resend_invitation / revoke_invitation / get_invitation / accept_invitation / decline_invitation | team:manage_members | | S |

### 2.4 Billing (`billing`, v1 reads: tier, balance, invoices, usage)
Overview, projects, wallet and read paths were captured live (billing-full.md); all writes are unverified and `billing:manage`.
| SDK | CLI | Plane | HTTP (C:bill.) | Cap | D | Status |
|---|---|---|---|---|---|---|
| billing.overview.get | fcctl billing usage | C | get_team_overview, get_cycle_costs, get_forecast, get_spend_history, get_next_payment, get_collection_status | billing:view | | R |
| billing.trust_tier.get | fcctl billing tier | C | get_trust_tier | billing:view | | V |
| billing.wallet.balance / ledger | fcctl billing balance | C | get_credit_balance / credit_ledger | billing:view | | R |
| billing.wallet.topup_options / topups.create / confirm / receipt | later | C | get_topup_options / create_topup_order / confirm_topup / download_topup_receipt | billing:manage | money: g | S |
| billing.invoices.list / get / statement / pdf / export | fcctl billing invoices | C | list_invoices / get_invoice / get_statement / download_invoice_pdf / export_csv | billing:view | | R |
| billing.invoices.pay / checkout.pay / checkout.confirm | later | C | pay_invoice / pay_invoice_checkout / confirm_invoice_checkout | billing:manage | money: g | S |
| billing.payments.methods.list / options / default / reorder / remove | later | C | list_payment_methods / get_payment_method_options / set_default_payment_method / reorder_payment_methods / remove_payment_method | billing:manage | remove: g | R/W |
| billing.payments.cards.setup / confirm; methods.setup_order / confirm_order | later | C | initiate_card_setup, confirm_card, setup_payment_method_order, confirm_payment_method_order, get_setup_wait | billing:manage | | S |
| billing.payments.attempts.list / refunds.list / schedule.get | later | C | list_payment_attempts / list_refunds / get_payment_schedule | billing:view | | R |
| billing.projects.list / create / rename / set_enabled / set_spending_limit | later | C | list_projects / create_project / rename_project / set_project_enabled / set_project_spending_limit | billing:manage | | list R, rest S |
| billing.subscriptions.list / pause / resume / set_project / subscribe_metered | later | C | list_subscriptions / pause_subscription / resume_subscription / set_subscription_project / subscribe_metered_service; get_metered_services | billing:manage | pause: g | list R |
| billing.profile.get / save / geo / recheck_gst; tax.summary | later | C | get_billing_profile / save_billing_profile / get_billing_geo / recheck_gst_status / get_tax_summary | billing:manage | | R/S |
| billing.settings.get / save; collection_mode.set | later | C | get_billing_settings / save_billing_settings / set_collection_mode | billing:manage | | S |

### 2.5 Infrastructure catalog (`infra`; backs `server plans/images`)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| infra.images.list | fcctl server images | C | api.images.list_images | server:view | | V |
| infra.offerings.list (regions+plans) | fcctl server plans | C | api.images.list_offerings | server:view | | V |
| infra.plans.eligible | fcctl server plans | C | api.images.eligible_plans; C:bill.catalog.get_eligible_plans | server:create | | R |
| infra.apps.list / requirements | | C | api.images.list_apps / get_app_requirements | server:view | | R |
| infra.composed_config.get | | C | C:bill.catalog.get_composed_config | server:create | | S |
| infra.ipv4_pricing / snapshot_pricing | | C | api.servers.public_ipv4_pricing / api.snapshots.snapshot_pricing | server:view | | R |

### 2.6 Servers (`servers`, v1)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| servers.list | fcctl server list | C | api.servers.registry (and list_instances, refresh_servers) | server:view | | V |
| servers.get / overview | fcctl server get | C | api.servers.server_overview | server:view | | V |
| servers.create / create_composed | fcctl server create | C | api.servers.create_server / create_composed_server | server:create | cost | V |
| servers.create(from_snapshot=) | fcctl server create --snapshot | C | api.servers.create_server | server:create+snapshot | cost | S |
| servers.rename | fcctl server set --name | C | api.servers.rename_server | server:create | | S |
| servers.start / stop / restart | fcctl server start/stop/restart | C | api.servers.start_server / stop_server / restart_server | server:power | stop: g | W |
| servers.resize | fcctl server resize | C | api.servers.resize_server | server:resize | g | W |
| servers.terminate | fcctl server terminate | C | api.servers.terminate_server | server:terminate | **G** | W |
| servers.set_termination_protection | fcctl server protect on/off | C | api.servers.set_termination_protection | server:terminate | off: g | W |
| servers.hostnames | fcctl server get | C | api.servers.server_hostnames | server:view | | R |
| servers.actions.get(id) / retry | fcctl server wait | C | api.servers.action_status / retry_action | server:view / power | | S |
| servers.keep_trial_site | internal | C | api.servers.keep_trial_site | team | | S |
| **network** servers.firewall.update | later | C | api.servers.update_server_firewall | server:network | g | S |
| servers.firewall_rulesets.list / save / delete / attach | later | C | api.firewall_rulesets.list_firewall_rulesets / save_firewall_ruleset / delete_firewall_ruleset / set_server_firewall_rulesets | view / server:network | delete: g | list R |
| servers.network.public_ip.set / ipv4_internet.set | later | C | api.servers.set_server_public_ip / set_server_ipv4_internet | server:network | g | S |
| servers.wireguard.devices.list / create / delete | later | C | api.wireguard.list_/create_/delete_wireguard_device | server:wireguard | delete: g | S |
| servers.wireguard.access.set | later | C | api.servers.set_server_wireguard_access | server:network | | S |
| **access** account.ssh_keys.list / create / rotate / delete / retry_sync | later | C | api.ssh_keys.list_/create_/rotate_/delete_/retry_ team_ssh_key(s) | view / server:ssh-key | delete: g | list/create V |
| servers.ssh_keys.set | later | C | api.ssh_keys.set_server_ssh_keys | server:ssh-key | | V |
| servers.console.open | fcctl api | C | api.servers.open_console | server:console | | S |
| servers.rescue.get / set | later | C | api.servers.get_rescue_state / set_rescue_mode | server:console | set: g | S |
| servers.snapshots.list | fcctl snapshot list | C | api.snapshots.list_snapshots | server:snapshot | | V |
| servers.snapshots.create | fcctl snapshot take | C | api.snapshots.take_snapshot | server:snapshot | | W |
| servers.snapshots.delete | fcctl snapshot delete | C | api.snapshots.delete_snapshots | server:snapshot | **G** | W |
| servers.snapshots.keep / auto.set | later | C | api.snapshots.keep_snapshot / set_automatic_snapshots | server:snapshot | | W |
| servers.metrics.get(window) | fcctl server metrics | C | api.servers.server_metrics | server:view | | V |
| servers.activity.list | fcctl audit list --server | C | api.activity.server_activity | server:view | | V |

GAP: in-place server restore from snapshot (clone only).

### 2.7 Bench via Pilot (`bench`; `client.bench(server).*`). v1: apps, tasks, logs, health. Everything is bench-scope; no finer scope exists.
| SDK | CLI | HTTP | D | Status |
|---|---|---|---|---|
| bench.get / list / start / stop / restart / clone / delete | later (v1: none) | GET /benches[/<n>]; POST /benches/<n>/actions/{start,stop,restart,clone}; POST /benches; DELETE /benches/<n> | stop/clone/delete: g | S |
| bench.apps.list / get | fcctl app list | GET /apps[/<n>] | | V |
| bench.apps.add (fetch, install) | fcctl app add | POST /apps/fetch; POST /apps | | V (ERPNext, git) |
| bench.apps.update / switch_branch | later | PATCH /apps/<n>; POST /apps/<n>/actions/switch-branch | switch: **G** | S |
| bench.apps.remove | fcctl app remove | DELETE /apps/<n> | **G** | S |
| bench.updates.list / check / apply | later | GET /app-updates; POST /app-update-checks; POST /updates | apply: g | S |
| bench.git.connection.* / repositories / branches | later | GET/PUT/DELETE /git/connection; POST /git/connection-attempts; GET /git/repositories, /git/branches; POST /git/repository-resolutions | del: g | S |
| bench.marketplace.apps.list | later | GET /marketplace/apps | | S |
| bench.task_worker.get / start / stop | later | GET /task-worker; POST /task-worker/actions/{start,stop} | stop: g | S |
| bench.settings.get / update; common_site_config.get / update | later | GET/PATCH /settings; GET/PATCH /settings/common-site-config | g | S |
| bench.database.* (diagnostics, size, processlist, slow_queries, binlogs, config, quick_actions, kill, purge) | later | GET /database/*; POST /database/processlist/kill, /database/binlogs/purge, /database/quick-actions/* | kill/purge/restart: g | S |
| bench.migrations.list / get / current / retry / dismiss / bypass_patch / restore | later | GET /migrations[/<op>|/current]; POST /migrations/<op>/actions/{retry,dismiss,bypass-patch,restore} | restore: **G** | S |
| bench.health.get | fcctl doctor | GET /health | | S |
| bench.ssh_keys.list / add / remove | later | GET/POST /ssh-keys; DELETE /ssh-keys/<fp> | del: g | S |
| bench.editor.* (files, git), bench.console.* (completions) | not in SDK (use `fcctl api`) | /editor/*, /console/* | g | S |
| bench.setup.* , cli_update.*, waf.get, network.client | not in SDK | /setup/*, /cli-*, /waf, /network/client | | S |

### 2.8 Sites (`client.bench(server).sites`, v1)
| SDK | CLI | HTTP | Cap | D | Status |
|---|---|---|---|---|---|
| sites.list / get | fcctl site list/get | GET /sites; GET /sites/<n> | bench | | V |
| sites.create | fcctl site create | POST /sites | bench | | S (stub body) |
| sites.provision (workflow) | fcctl site provision | composed | bench | | S (restore cross-server open) |
| sites.drop | fcctl site drop | DELETE /sites/<n> | bench | **G** | S |
| sites.rename | fcctl site rename SITE NEW [--keep-old-hostname] | POST /sites/<n>/actions/rename body {new_name, keep_old_hostname} | bench | g | S |
| sites.clone | fcctl site clone | POST /sites/<n>/actions/clone (keys name, target_bench) | bench | **G** | S |
| sites.apps.list / install | fcctl app add NAME --site S; fcctl app install --site S NAME | GET/POST /sites/<n>/apps; body {app} (get-and-install-app if not on bench) or {repo, branch} | bench | | S |
| sites.apps.uninstall | fcctl app remove --site | DELETE /sites/<n>/apps/<app> | bench | **G** | S |
| sites.update_apps / reinstall | later | POST /sites/<n>/actions/update-apps, /reinstall | bench | reinstall: g | S |
| sites.backups.list / get / create | fcctl backup list/create | GET /sites/<n>/backups[/<ts>]; POST /sites/<n>/backups | bench | | S |
| sites.backups.download | fcctl backup download | GET /sites/<n>/backups/<ts>/download-links, /files/<id>/content | bench | | S |
| sites.backups.delete | later | DELETE /sites/<n>/backups/<ts> | bench | g | S |
| sites.backups.offsite | fcctl backup offsite | POST /sites/<n>/actions/remote-backups | bench | | S |
| sites.backups.schedule.get / set / delete | later | GET/PUT/DELETE /sites/<n>/backup-schedule; PUT always sends {schedule, retention} (current retention is fetched when not given) | bench | | S |
| sites.restore (backup / other site) | fcctl backup restore | POST /sites/<n>/actions/restore | bench | **G** | S |
| sites.restore_upload; sites.uploads.* | later | POST /sites/<n>/actions/restore-upload; /uploads... | bench | **G** | S |
| sites.migrate / clear_cache / build_assets | fcctl site migrate / clear-cache | POST /sites/<n>/actions/{migrate,clear-cache,build-assets} | bench | | S |
| sites.config.get / set | fcctl site config get/set | GET/PATCH /sites/<n>/configuration | bench | | S |
| sites.maintenance.set(on) | fcctl site maintenance on/off | PATCH /sites/<n>/configuration `{maintenance_mode}` (no dedicated route) | bench | | S |
| sites.domains.list / add / get / update / remove / dns_records | later | /sites/<n>/domains[/<d>[/dns-records]] | bench | remove: g | S |
| sites.tls.enable | later | POST /sites/<n>/actions/enable-tls | bench | | S |
| sites.login | fcctl site login | POST /sites/<n>/login (also GET /sites/<n>/account-url) | bench | | S |
| sites.monitoring.get / uptime.get / storage.get / refresh_storage | later (v1 metrics via bench) | GET /sites/<n>/monitoring, /uptime, /storage; POST /actions/refresh-storage | bench | | S |
| sites.tasks.get | fcctl task get | GET /sites/<n>/tasks/<id> | bench | | S |
| sites.integrations.frappe_cloud.* | not in SDK | /sites/<n>/integrations/frappe-cloud* | | | S |
| sites.central.call | fcctl api | GET/POST /sites/<n>/central/<path> | | g | S |

#### 2.8a Bench apps and parity notes (pilot dashboard contract)
| SDK | CLI | HTTP | Status |
|---|---|---|---|
| apps.add_marketplace (with sites) | fcctl app add NAME --site S | POST /sites/<s>/apps {app} per site (no POST /apps) | S |
| apps.add_marketplace (bench only) | fcctl app add NAME | GET /marketplace/apps lookup, then POST /apps {name, repo, branch, sites: []} | S |
| apps.add_repo | fcctl app add --repo URL | POST /git/repository-resolutions {repo, branch} for the name (or `name=`), then POST /apps {name, repo, branch, sites} | S |
| apps.check_updates / check_updates_result | fcctl app check-updates | POST /apps/fetch (task); result is the last JSON line of /tasks/<id>/output/content | S |
| apps.remove_app | fcctl app remove | DELETE /apps/<a> (unverified, allowlisted; see FCCTL_PARITY W9) | unverified |
| logs.content(lines=N) | fcctl logs tail F --lines N | GET /logs/<f>?lines=N (JSON); no lines: GET /logs/<f>/content | S |
| transfer (restore from site backup) | fcctl backup restore / site provision --from-backup | reads files[].size_bytes and files[].kind from GET /sites/<n>/backups/<ts> | S |

Contract guard: `tests/test_pilot_contract.py` replays every public SDK function against `tests/fixtures/pilot_dashboard_contract.json` (refresh with `just contract-refresh <host>`). `--dry-run` now prints the would-be `request` (method, path, params, body). Path segments are percent-encoded via `pilot.seg`.

### 2.9 Services (`services`: later; bucket in v1)
| SDK | CLI | Plane | HTTP (C:svc.) | Cap | D | Status |
|---|---|---|---|---|---|---|
| services.storage.get | fcctl bucket list | C | storage.get_object_storage | service:view | | V |
| services.storage.buckets.create | fcctl bucket create | C | storage.create_bucket | service:manage | | V |
| services.storage.buckets.usage | fcctl bucket usage | C | storage.get_bucket_usage | service:view | | V |
| services.storage.buckets.delete | fcctl bucket delete | C | storage.delete_bucket | service:manage | **G** | V |
| services.storage.buckets.set_quota / rotate_credentials | later | C | storage.set_bucket_quota / rotate_credentials | service:manage | rotate: g | V |
| services.storage.buckets.origins.get / set | later | C | storage.get_/set_bucket_allowed_origins | service:manage | | V |
| services.storage.objects.list / url / upload_url / delete | later (S3-native preferred) | C | storage.list_objects / get_object_url / get_upload_url / delete_objects | service:manage | delete: g | V |
| services.ai.get / enable / models / usage / pull_usage | later | C | services.api.ai.get_ai / enable_ai / get_models / get_usage / pull_usage | service:view/manage | | S |
| services.ai.keys.list / create / revoke / set_caps; prompt_logging.set | later | C | ai.list_api_keys / create_api_key / revoke_api_key / set_api_key_caps / set_prompt_logging | service:manage | revoke: g | S |

### 2.10 Observability (`observability`, v1: notification, task, logs, metrics)
| SDK | CLI | Plane | HTTP | Cap | D | Status |
|---|---|---|---|---|---|---|
| notifications.list / badge / read / read_all | fcctl notification list/read | C | notif.list_notifications / notification_badge / mark_notification_read / mark_all_notifications_read | session | | R/W |
| notifications.preferences.get / set | later | C | notif.get_user_preferences / save_user_preferences | session | | S |
| pilot.notifications.list / read / webhooks.test | later | P | GET /notifications; POST /notifications/<n>/read, /read-all, /webhooks/test | bench | | S |
| activity.team.list | fcctl audit list | C | api.activity.team_activity | team | | V |
| metrics.server.get | fcctl server metrics | C | api.servers.server_metrics | server:view | | V |
| bench.metrics.get / system / monitor.status / monitor.history | fcctl server metrics --live | P | GET /metrics, /system, /monitor/status, /monitor/history | bench | | S |
| events.subscribe(room) (realtime, Socket.IO, `Cookie: sid`) | later (v1.1) | C | Node realtime; rooms `Team/<team>`; events activity_changed, team_notification | session | | S |
| logs.list / get / content | fcctl logs list | P | GET /logs, /logs/<f>, /logs/<f>/content | bench | | S |
| logs.tail (SSE) | fcctl logs tail --follow | P | GET /logs/<f>/events | bench | | S |
| tasks.list / get / create | fcctl task list/get | P | GET /tasks[/<id>]; POST /tasks | bench | | V (status, events) |
| tasks.events (SSE) / output / debug | fcctl task wait/attach/output | P | GET /tasks/<id>/events, /output/content, /debug | bench | | events V |
| tasks.retry / cancel | fcctl task cancel | P | POST /tasks/<id>/actions/retry; DELETE /tasks/<id> | bench | cancel: g | S |
| audit.events.list / export | fcctl audit | P | GET /audit-events[/export] | bench | | S |

### 2.11 Support, trial
| SDK | CLI | Plane | HTTP | Status |
|---|---|---|---|---|
| support.tickets.list / create / options / find_related | later | C | api.support.list_tickets / create_ticket / get_ticket_options / find_related | S |
| trial.signup.product.get | not in SDK public | C | api.signups.get_product | S |
| trial.teams.create; trial.sites.create / get / claim / terminate / check_subdomain / login / install_app / onboarding_status / domain | internal | C | api.sites.create_trial_team / create_trial_site / get_site / claim_site / terminate_site (g) / check_subdomain / login_site / install_trial_app / get_trial_app_install / onboarding_status / site_domain; get_open_trial | S |
| integrations.github.* | later | C | api.integrations.github.complete_connection / get_connection_attempt / get_installation_repositories | S |

## 3. Naming rules

1. Resources are singular-noun *namespaces* on the client, plural collections (`servers`, `sites`); singular only for singletons (`profile`, `settings`, `trust_tier`). CLI nouns are singular (`server`, `site`), matching the 16 groups.
2. Verbs: `list`, `get`, `create`, `update` (partial), `delete` (remote removal that is reversible or not, always wording matches the product: `terminate` for servers, `drop` for sites, `remove` for apps, `uninstall` for site apps, `delete` otherwise); `set_x`/`x.set` for single-value idempotent settings; `enable/disable`, `start/stop/restart`, `pause/resume`, `rotate`, `retry`, `cancel` for actions. Never `fetch`, `remove` for non-apps, or `save` (map Central `save_*` to `create`/`update`).
3. Central method names map mechanically: strip the verb prefix and the noun (`take_snapshot` -> `snapshots.create` with CLI alias `take`).
4. Ids: `server` is the Central server name (e.g. `server-abc123def4`); `site` is the hostname; `snapshot`, `task_id` are opaque strings; backups use `timestamp`. Always keyword args after the first parent id; accept a model or its id.
5. Pagination: Central lists use `start`/`limit` and return `has_next_page`; SDK `list()` returns an iterator that pages transparently, `list_page()` returns one page. Pilot lists are unpaginated.
6. Async ops: any call that returns a task or action returns a handle, never blocks. Pilot -> `TaskHandle` (`.wait(timeout)`, `.events()`, `.attach()`, `.cancel()`); Central -> `ActionHandle` over `servers.actions.get`. `wait=True`/`--wait` is sugar; `--no-wait` prints the handle. Handles serialize (`to_dict/from_dict`).
7. Destructive operations carry the `risk` decorator and return a `GuardRequest` first; they run only through request/approve/execute. `--dry-run` is available on every write.

## 4. Cross-cutting conventions

- Errors: typed taxonomy; `FeatureDisabled(flag)` for storage/LLM/firewall/public-IP flags; `PermissionDenied(capability)` (the capability the role lacks); Cloudflare challenge = `Challenged`. Each error exposes request id and `cf-ray`; CLI exit codes 0-12, 7 pending approval, 8 timeout, 130 interrupt.
- Polling: Pilot tasks use SSE `/tasks/<id>/events` first, then one shared `GET /tasks` poll per server; Central actions poll `action_status`. Stall warning 120 s, diagnosis 600 s.
- Idempotency: journal entry before every mutating request; `reconcile` resolves in-flight entries.
- Auth: Central session `sid`; Pilot bearer from `get_bench_link`, re-minted on 401. Honest UA `fcctl/<ver>`.
- Models: tolerant (`extra="allow"`), unknown enum -> `UNKNOWN`.

## 5. v1 vs later

| Phase | Groups |
|---|---|
| v1 | auth, profile, server, snapshot, bucket, billing (tier, balance, invoices, usage), notification, app, site (+provision), backup, task, logs, fleet, approval, audit/alert/reconcile, doctor/api |
| v1.1 | portal backends, MCP, realtime events |
| Later (each live-verified first) | teams, support, firewall/WireGuard/SSH keys, bench settings/database/migrations/git/updates, domains/TLS, LLM, object ls/put/rm, billing writes, trial/integrations |
| Never in SDK | editor, console, setup, CLI-update, Pilot password/2FA (raw `fcctl api` only) |
