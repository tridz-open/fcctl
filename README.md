# fcctl

> [!WARNING]
> **ALPHA SOFTWARE. Bleeding edge. Read before you use it.**
>
> - fcctl is early-stage (0.1.0 alpha). It works against real Frappe Cloud accounts, but it is young.
> - It talks to Frappe Cloud's **pre-public dashboard and Pilot API**. That API is not documented or published, and it can change or disappear without notice. Any command can break when it does.
> - **Breaking changes are expected** between releases: the CLI, the library and the on-disk formats can all change.
> - **Pin an exact version** (a release tag) and upgrade deliberately.
> - **Try writes on throwaway sites and a non-production team first.**
> - **Not affiliated with or endorsed by Frappe Technologies.** "Frappe" and "Frappe Cloud" are their trademarks.

## What is fcctl?

fcctl is a command-line tool and Python library that drives [Frappe Cloud](https://frappecloud.com) from your terminal or your code. With it you can list servers and sites, install apps, take and restore backups, read logs, follow background tasks, edit site config, rename or drop sites, and provision whole new sites from a backup, all without clicking through the dashboard.

On top of the raw calls it adds a safety layer: per-profile policies, an approval step for destructive operations, a local journal so interrupted operations can be reconciled, and a hash-chained audit log.

**Who it is for:** developers and operators who run sites on Frappe Cloud and want to script them, and teams building a Frappe SaaS that needs to create and manage customer sites automatically. If you only manage a couple of sites by hand, the dashboard is the better tool.

## Related: Frappe Cloud today vs. v2

fcctl targets the **upcoming Frappe Cloud v2**, which is split into **Central** (dashboard.frappe.io: team, billing, servers, snapshots, buckets) and **Pilot** (the per-server gateway: sites, apps, backups, tasks, logs).
For the **current** Frappe Cloud (the `press.api.*` RPC API the existing dashboard uses, with API-key auth, bench and site lifecycle, domains and backups), see the separate Python SDK [esafwan/frappe_cloud_sdk](https://github.com/esafwan/frappe_cloud_sdk), built from the open-source frappe/press backend and checked against a live account.
Neither project is official, and neither is affiliated with Frappe.

## Why it exists

fcctl was created to start the SaaS work behind [URY](https://ury.app), an open-source restaurant POS and ERP built on Frappe and ERPNext ([github.com/ury-erp/ury](https://github.com/ury-erp/ury)). URY's hosted offering needed to create, restore and manage customer sites on Frappe Cloud from a control plane, without a person clicking through the dashboard. fcctl is that automation layer, kept product-neutral so any Frappe SaaS can use it.

## A note to the Frappe team

fcctl is built by Tridz Technologies, a team that builds on Frappe Cloud. We wrote it to automate our own sites and servers: listing and inspecting them, taking backups, provisioning, and guarding destructive operations behind an approval step.

- It calls the same dashboard endpoints the Frappe Cloud web UI calls, signed in with a normal user account (email code). Nothing else.
- It is unofficial and not endorsed by Frappe Technologies.
- It does not bypass Cloudflare or any bot protection. It sends an honest User-Agent (`fcctl/<version> (+https://github.com/tridz-open/fcctl)`), and when a challenge appears it stops with exit code 10.
- We will move to an official API or token flow the moment one exists, and we are happy to change, rate-limit or take this project down on request.
- Contact: open an issue on this repository, or see [SECURITY.md](SECURITY.md) for security reports.

## Contents

[Install](#install) · [First run](#first-run) · [Usage examples](#usage-examples) · [Experimental commands](#experimental-commands) · [Safety and approvals](#safety-model) · [Library](#library) · [How it is used in production](#how-it-is-used-in-production) · [Related repositories](#related-repositories) · [Known gaps](#known-gaps) · [Contributing, security, license](#contributing-security-and-license)

## Prerequisites

- Python 3.11 or newer.
- A Frappe Cloud v2 account, plus the id of the team you want to manage (`TEAM-xxxxx`, shown in the dashboard).
- Access to that account's mailbox. Sign-in works by a 6-digit code sent by email; fcctl does not support passwords or API keys.
- To manage sites on a server, your role needs the `server:console` capability. Without it, fcctl cannot get a Pilot token.
- [uv](https://docs.astral.sh/uv/) or pipx (recommended, to install fcctl as a tool).

## Install

fcctl is not on PyPI. Install it from a release tag and pin that tag. Tags are listed on the [releases page](https://github.com/tridz-open/fcctl/tags); replace `v0.1.0-alpha.3` below with the tag you want.

```bash
# uv (recommended): puts `fcctl` on your PATH
uv tool install "git+https://github.com/tridz-open/fcctl@v0.1.0-alpha.3"

# or pipx
pipx install "git+https://github.com/tridz-open/fcctl@v0.1.0-alpha.3"

# or pip, inside a virtualenv
python3 -m venv .venv && . .venv/bin/activate
pip install "git+https://github.com/tridz-open/fcctl@v0.1.0-alpha.3"
```

From a local clone (this is also how to develop on it):

```bash
git clone https://github.com/tridz-open/fcctl.git && cd fcctl
uv tool install .
# or: uv venv && uv pip install -e '.[dev]'
```

Check the install:

```bash
fcctl --version      # prints the installed version, e.g. fcctl 0.1.0a4
fcctl --help
fcctl guide          # built-in usage guide
```

Optional extra: `.[yaml]` adds pyyaml for `-o yaml` output.

## First run

### 1. Initialise (`fcctl init`)

fcctl encrypts your session (the dashboard `sid` cookie, the CSRF token and Pilot tokens) with a Fernet key, and uses the same key to sign its approval records. Run `fcctl init` once: it generates the key and stores it in the OS keyring or, if that is unavailable, in a 0600 file `$FCCTL_HOME/session.key`. The key is never printed. `--store keyring|file` picks the location. The keyring needs an optional extra:

```bash
pip install 'fcctl[keyring]'      # optional; without it `fcctl init` uses the 0600 key file
fcctl init
fcctl doctor                      # prints an exact fix for each failed check
fcctl doctor --fix                # applies the safe fixes (creates the key, tightens permissions)
```

Alternative: set `FCCTL_KEY` yourself in the environment (a Fernet key; for CI or containers where there is no keyring or persistent home). The lookup order is the environment, then the keyring, then the key file.

Every later command needs the same key. If you lose or change it, run `fcctl auth login` again.

### 2. Create a profile

A profile ties together a login email, a team and a policy:

```bash
fcctl profile create me --email you@example.com --team TEAM-xxxxx   # policy defaults to guarded
fcctl profile use me
fcctl profile list
```

To set a different policy, pass `--policy full|guarded|read_only|protected`. To let a profile run one specific operation that its policy otherwise denies, pass `--allow OP` (repeatable). Profiles are stored in `$FCCTL_HOME/config.toml`.

### 3. Sign in

fcctl emails you a 6-digit code. Pick one way to enter it.

**Manual (interactive):**

```bash
fcctl auth login
```

**From stdin (scripts):**

```bash
read -r CODE && echo "$CODE" | fcctl auth login --code-stdin
```

**IMAP auto-login (unattended):** fcctl requests the code, then polls a mailbox for the message from Frappe Cloud. Use a dedicated mailbox and an app password, never your main password:

```bash
export FCCTL_IMAP_HOST=imap.gmail.com
export FCCTL_IMAP_USER=ops-mailbox@example.com
export FCCTL_IMAP_PASS='app-password'        # better: read it from your keychain or secret manager
fcctl auth login --code-source imap
```

fcctl accepts only messages that arrive after the login request starts, so an old code is never reused. Your Frappe Cloud terms still apply: if you plan to run fcctl unattended or at volume, check them first. fcctl never solves CAPTCHAs or Cloudflare challenges; when one appears it stops with exit code 10.

### 4. Check everything

```bash
fcctl auth whoami            # add --caps to list your capabilities
fcctl doctor                 # checks the key, config, profile and session
```

## Usage examples

Replace `server-xxxx` with your server id (`fcctl server list` shows them) and the site names with your own. You can also set `FCCTL_SERVER` instead of passing `-s` every time. Add `--dry-run` to any write to see what it would do without sending it.

**Sites**

```bash
fcctl server list                                     # servers in the team (Central)
fcctl site list -s server-xxxx                        # sites on one server (Pilot)
fcctl site get shop.example.com -s server-xxxx
fcctl site maintenance shop.example.com on -s server-xxxx
```

**Install an app on a site** (fetches and installs in one step, the same call the dashboard makes):

```bash
fcctl app add india_compliance --site shop.example.com -s server-xxxx
fcctl app list -s server-xxxx
```

**Backups** (take one before any risky change):

```bash
fcctl backup list shop.example.com -s server-xxxx
fcctl backup create shop.example.com -s server-xxxx     # waits for the task by default
```

**Logs**

```bash
fcctl logs list -s server-xxxx
fcctl logs tail FILENAME --lines 100 -s server-xxxx     # add -f to follow
```

**Tasks** (long operations run as background tasks):

```bash
fcctl task list -s server-xxxx
fcctl task wait TASK_ID -s server-xxxx          # Ctrl-C detaches (exit 130); the task keeps running
fcctl task output TASK_ID -s server-xxxx        # full log, useful for a failed task
```

**Site config**

```bash
fcctl site config get shop.example.com -s server-xxxx
fcctl site config set shop.example.com KEY=VALUE -s server-xxxx
```

**Rename a site**

```bash
fcctl site rename shop.example.com store.example.com -s server-xxxx            # add --keep-old-hostname to keep the old name
```

**Drop a site, through approval.** Dropping is destructive, so under the default `guarded` policy it is filed for approval and exits with code 7 instead of running (`--no-backup` skips the pre-drop backup; avoid it):

```bash
fcctl site drop old.example.com -s server-xxxx --dry-run      # decision: NeedsApproval
fcctl site drop old.example.com -s server-xxxx                # files the request, exit 7
fcctl approval list
fcctl approval approve APPROVAL_ID --approver alice           # approves and executes
```

See [Safety model](#safety-model) for how approval tokens work.

**Provision a new site** (resumable workflow; preview first):

```bash
fcctl site provision acme.example.com -s server-xxxx --app erpnext --app hrms --dry-run
fcctl site provision acme.example.com -s server-xxxx --app erpnext --app hrms

# From a golden backup of another site (profile:site:timestamp), with a config value:
fcctl site provision acme.example.com -s server-xxxx --app erpnext \
  --from-backup me:template.example.com:TIMESTAMP --config KEY=VALUE
```

An interrupted run can be resumed with `--workflow-id ID`. See `fcctl site provision --help` for `--hook`, `--max-age` and `--bench-app`.

**Script it: JSON out, exit codes in**

```bash
if ! fcctl site get acme.example.com -s server-xxxx -o json > site.json; then
  echo "lookup failed with exit $?"   # 3 auth, 5 not found, 10 Cloudflare challenge ...
fi
fcctl audit verify                    # check the local audit chain
fcctl auth logout                     # ends the server session too
```

**Raw API calls.** `fcctl api` sends any request. Pass the body from a file or stdin so it stays out of `ps` and shell history; inline `--data '{...}'` works but prints a warning:

```bash
fcctl api pilot GET /api/method/ping
fcctl api central POST press.api.site.new --data @body.json --dry-run
cat body.json | fcctl api central POST press.api.site.new --data -
```

Reads (GET/HEAD/OPTIONS) run directly. Under the `guarded` and `protected` policies, a write that fcctl does not recognise (an unknown `api` POST/PUT/PATCH/DELETE) is not executed: it files a pending approval and exits 7, and `--yes` or `--confirm` do not bypass that.

**Dry run.** `--dry-run` on any write prints the decision and the HTTP requests it would send (method, path, body with secrets redacted), and sends nothing.

**Retries.** Idempotent requests (GET, HEAD, OPTIONS, and writes that carry an idempotency key, which includes every Pilot POST) are retried on 429, 5xx and network errors with exponential backoff and jitter, honouring `Retry-After` (waits are capped at 30 seconds). The default is 3 retries; set `--max-retries N` or `FCCTL_MAX_RETRIES=N` (0 disables). Writes without an idempotency key (for example Central POSTs) are never retried automatically: an interrupted write is settled by `fcctl reconcile`.

**Shell completion** (bash, zsh, fish):

```bash
eval "$(fcctl completion bash)"                          # in ~/.bashrc
fcctl completion zsh > ~/.zfunc/_fcctl                   # then: autoload -U compinit && compinit
fcctl completion fish > ~/.config/fish/completions/fcctl.fish
```

**Command reference.** Every command and flag is listed in [docs/COMMANDS.md](docs/COMMANDS.md). It is generated from the parser; regenerate it with `python scripts/gen_commands.py`.

Global flags: `--profile`, `-s/--server`, `-o table|json|yaml|ndjson` (json when stdout is not a terminal), `--wait/--no-wait`, `--timeout`, `--dry-run`, `--confirm`, `--yes`, `--no-progress`, `--debug`. Data goes to stdout; progress and errors go to stderr. Run `fcctl <group> --help` for any group.

Command groups: `auth profile server snapshot bucket billing notification app site backup task logs fleet approval audit reconcile watch alert doctor api guide completion version`.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | API error, API drift, or any other failure |
| 2 | usage error |
| 3 | auth: session or CSRF token missing, expired or rejected |
| 4 | policy denied, outside the profile's scope, or feature disabled |
| 5 | not found |
| 6 | conflict: another task is running on the resource, or the name exists |
| 7 | approval pending |
| 8 | wait timeout (the remote operation keeps running) |
| 9 | task failed |
| 10 | Cloudflare challenge |
| 11 | rate limited or quota exceeded |
| 12 | validation error (including a refused disk shrink) |
| 13 | outcome unknown (the connection broke after the request was sent) |
| 130 | interrupted |

## Experimental commands

These are implemented and covered by offline tests (respx and the in-process fake), but have not been run against the live Frappe Cloud API. Request bodies and response shapes may be wrong. Try them on throwaway resources first. Each is tagged `[experimental]` in `--help`.

| Command | Why experimental |
|---|---|
| `app add NAME -s SERVER` (bench-only, no `--site`) | not live-tested; the bench validator can reject it |
| `app add --repo URL` | name resolution is contract-tested only |
| `app check-updates` | not live-tested |
| `app remove` | not live-tested |
| `backup restore` (full restore) | not live-tested; the approval gate stopped the live attempt |
| `server create / start / stop / restart / resize / protect / terminate` | Central writes, not live-tested |
| `snapshot take / delete` | Central writes, not live-tested |
| `bucket create / quota / delete / put` | Central writes, not live-tested |
| `billing health` | computed from field names that are not live-verified (billing has no write commands) |

Live-verified on Frappe Cloud (Pilot): site create, rename, drop (through the approval gate), app install and `app add --site`, config, logs, backup create/list/schedule, cross-site backup upload, task output, and the full provision workflow from a golden backup (hooks, release, health). The endpoint catalogue in [docs/API_IA.md](docs/API_IA.md) separates verified, unverified and not-implemented calls.

Region for server and bucket creation defaults to `bom`; set `FCCTL_REGION` (for example `blr`) or pass `--region` to change it.

## Safety model

Every write passes through the safety layer before any API call:

1. **Policy (per profile).**
   - `full`: allows everything.
   - `guarded` (default): allows writes, but destructive operations need approval and cost-changing ones need `--confirm TARGET`.
   - `read_only`: allows no writes.
   - `protected`: allows no writes, except operations in the profile's `allow` list.
2. **Approval.** Destructive operations do not run straight away. These include `site drop`, `backup restore`, `server terminate` and `snapshot delete`. The command files a signed approval and exits with code 7. Someone then runs `fcctl approval approve ID --approver NAME` with the approval token, taken from `FCCTL_APPROVAL_TOKEN` or an interactive prompt (never from the command line). The token is the approval secret itself or a 6-digit RFC 6238 TOTP code; `fcctl approval enroll` generates a base32 secret plus an `otpauth://` URI, so a normal authenticator app works. The approver name must differ from the requester name, but names are not authenticated, so on one machine a requester can approve their own request by supplying another name. Treat this as a speed bump against accidents and agents, not separation of duties.
   - **Signed approvals (optional).** Set `FCCTL_APPROVER_KEY` to a secret held only by the approver and the executing side, and approvals are signed with it; `fcctl approval approve ... --require-signed` (or `FCCTL_REQUIRE_SIGNED_APPROVAL=1`) rejects any approval without a valid signature. This gives real separation only when the requester never holds that key. Without it, single-machine self-approval is a speed bump, not separation of duties.
   - **Gated operations** under `guarded`:
     - Pilot: `site drop`, `site uninstall_app`, `backup restore`, `app remove`, `app switch_branch`, `task cancel`.
     - Central: `server terminate`, `stop` and `resize`; turning termination protection off; `snapshot delete`; `bucket delete`.
   - **Expiry:** approvals expire after 30 minutes. File a fresh request and approve it straight away (`approve` also executes).
   - **Automation and services:** a service that drives fcctl as a library (for example a SaaS control plane) does not need a person with an authenticator. Operations on a site that the same provisioning workflow created pass `owned_by_workflow=<workflow id>` and are exempt, which covers the restore during provisioning and a later drop of that site. Anything else returns a `PendingApproval` instead of acting, so the service needs its own approval step (for example an operator approval in its admin) before it re-runs the operation as the owning workflow. Do not set `FCCTL_APPROVAL_SECRET` on unattended servers just to get past the gate.
3. **Journal and reconcile.** fcctl records each operation before it sends the request. If the process dies mid-call, `fcctl reconcile --once` asks the remote what happened instead of blindly retrying.
4. **Audit.** fcctl appends a hash-chained JSONL log under `$FCCTL_HOME`. `fcctl audit verify` checks the chain.

**Limits.** All of this runs on your machine. The guard catches mistakes, buggy scripts and over-eager AI agents that go *through fcctl*. It is not access control:

- Anyone with your session, `FCCTL_KEY` and the approval secret can bypass it.
- Requester and approver names come from the environment and are not authenticated.
- Pilot tokens are bench-scoped: holding one gives full control of every site on that server.

For real separation of duties, use separate Frappe Cloud accounts with restricted roles.

## Environment variables

| Variable | Purpose |
|---|---|
| `FCCTL_KEY` | Optional override (else keyring, then `session.key`; run `fcctl init`). Fernet key that encrypts the session and signs approvals |
| `FCCTL_HOME` | State directory (default `~/.fcctl`) |
| `FCCTL_PROFILE` | Profile to use (overrides `profile use`) |
| `FCCTL_EMAIL`, `FCCTL_TEAM`, `FCCTL_SERVER` | Defaults when there is no profile / no `-s` |
| `FCCTL_IMAP_HOST`, `FCCTL_IMAP_USER`, `FCCTL_IMAP_PASS` | IMAP auto-login (`--code-source imap`) |
| `FCCTL_APPROVAL_SECRET` | Base32 approver secret for guarded operations (`fcctl approval enroll` generates one) |
| `FCCTL_APPROVER_KEY` | Optional approver signing key, distinct from `FCCTL_KEY` and `FCCTL_APPROVAL_SECRET`; approvals then carry a signature (see Safety model) |
| `FCCTL_REQUIRE_SIGNED_APPROVAL` | `1` rejects approvals without a valid approver signature (same as `--require-signed`) |
| `FCCTL_MAX_RETRIES` | Retries for idempotent requests (default 3; 0 disables; same as `--max-retries`) |
| `FCCTL_APPROVAL_TOKEN` | Approval token or authenticator code (otherwise `approve` prompts for it) |
| `FCCTL_REGION` | Default region for server and bucket creation (default `bom`) |
| `FCCTL_ENV_FILE`, `FCCTL_SID`, `FCCTL_LOGIN`, `FCCTL_CSRF`, `FCCTL_OWNER_ID` | Non-interactive session and automation-profile inputs (CI and services) |
| `FCCTL_USER` | Requester identity recorded on approvals (default: OS user) |
| `FCCTL_SMTP_HOST/PORT/USER/PASS`, `FCCTL_ALERT_FROM`, `FCCTL_ALERT_TO` | Email alerts |
| `FCCTL_TG_TOKEN`, `FCCTL_TG_CHAT` | Telegram alerts |
| `FCCTL_POLL` | Pilot task poll interval in seconds (default 15) |

Pass secrets through the environment or a secret manager. Don't put them in command-line arguments, which end up in shell history and `ps` output.

## Where state lives

`$FCCTL_HOME` (default `~/.fcctl`, created 0700; files are 0600) holds:

- `config.toml`: profiles.
- `session.enc`: the session, encrypted with `FCCTL_KEY`.
- `approvals.json`: signed approval records.
- The journal and the audit log.

The approvals, the journal and the audit log are not encrypted. fcctl creates the directory private; keep it that way.

## Library

The stable surface is `fcctl.Client` (sync) and `fcctl.AsyncClient` (async, same methods):

```python
from fcctl import Client
from fcctl.session_store import SessionStore

c = Client(SessionStore(), team="TEAM-xxxxx")   # reads FCCTL_KEY and $FCCTL_HOME/session.enc (log in with the CLI first)
sites = c.sites.list("server-xxxx")
c.sites.suspend("server-xxxx", "mysite.example.com")      # maintenance_mode + pause_scheduler on
run = c.workflows.provision_site(server="server-xxxx", site="demo.example.com", site_apps=["erpnext"])
run.wait()
c.close()
```

Notes:

- Destructive facade calls (`sites.drop`, `backups.restore`) return a `PendingApproval` instead of acting.
- `servers.resize` refuses to shrink the disk (`DiskShrinkRefused`).
- `sites.admin(server, site)` returns a client signed in to the site as Administrator, with `get_list`, `get_doc`, `insert`, `update`, `delete` and `call`.

To test your own code, `fcctl.testing.FakeFrappeCloud` provides an in-process fake of Central and Pilot.

How it fits together, with diagrams (request path, safety layer, auth flow, provisioning states, cross-server restore): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Guard limits for library users

The safety layer is enforced in two places only: the CLI and the `Client` facade. Anything below the facade does not check policy.

| Call | Guard |
|---|---|
| CLI commands (`fcctl site drop`, `backup restore`, ...) | Enforced: policy, approval, audit, journal |
| `Client.sites.drop`, `Client.backups.restore` | Guarded by default: return `PendingApproval` instead of acting |
| `owned_by_workflow=<workflow id>` on those calls | Exempt, but only if the journal shows that workflow created the site; otherwise `PolicyDenied` |
| `unsafe=True` on those calls | Opt-out with no guard and no ownership check; an audit entry is written first and the call fails if it cannot be written |
| `Servers.resize` | Not approval-gated (the CLI's `--confirm` has no library equivalent); it refuses a disk shrink |
| `Workflows.provision_site` | Unguarded; it only restores onto a site the workflow itself creates |
| Other facade reads and writes | Not gated |
| `client.pilot(server).post(...)`, `client.central.call(...)` | Not enforced: raw transport |
| `fcctl.sites.*`, `fcctl.servers.*` and other top-level module functions | Not enforced for direct calls: low-level helpers |
| `fcctl api ...` (raw) | Reads run directly; unknown writes need approval under `guarded` or `protected`, but a `full` profile allows them |

If your code needs the guard, call the facade and do not reach for the low-level clients.

## How it is used in production

fcctl powers a SaaS control plane (`saas_core`, a Frappe app) that provisions customer sites on Frappe Cloud from a golden backup: it creates the site, restores the template, runs setup hooks, lifts maintenance mode and checks health, with destructive steps held behind approval. fcctl itself is product-neutral, so any Frappe SaaS can use it the same way.

## Development

```bash
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest -q          # or: . .venv/bin/activate && pytest
.venv/bin/ruff check .
.venv/bin/python scripts/gen_sync.py --check
```

The tests mock all HTTP with respx and an in-process fake, so they never call Frappe Cloud. They need no account and no network.

`fcctl/_sync` is generated from `fcctl/_async` by `scripts/gen_sync.py` (unasync). Edit only `_async`, then regenerate. The `fcctl.servers`, `fcctl.sites` and similar top-level modules are thin re-exports kept for compatibility.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md). Endpoint catalogue: [docs/API_IA.md](docs/API_IA.md).

## Related repositories

- [tridz-open/frappe-saaskit-sdk](https://github.com/tridz-open/frappe-saaskit-sdk): `@frappe-saaskit` TypeScript SDK (client, React, Vue, Node, mock) for apps that talk to a `saas_core` backend.
- [tridz-open/frappe-saaskit-starter](https://github.com/tridz-open/frappe-saaskit-starter): React + Vite SaaS starter for Frappe (`saas_core`).

## Contributing, security and license

- Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md) and the [CHANGELOG](CHANGELOG.md). Please report bugs and API drift as issues on this repository, not to Frappe.
- Security reports: see [SECURITY.md](SECURITY.md). Do not open public issues for vulnerabilities.
- License: MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Tridz Technologies.

## Known gaps

- Many request bodies are unverified (see `docs/API_IA.md`), and the whole API is unofficial.
- CSRF refresh falls back to reading the dashboard boot page, which is fragile.
- The API has no in-place snapshot restore.
- No MCP server yet.
- Everything under [Experimental commands](#experimental-commands) is untested against the live API.
- The safety layer is a speed bump against mistakes, not access control (see [Safety model](#safety-model)).

## Use at your own risk, and contributions

**Use at your own risk:** this is early alpha software, provided "as is" without warranty of any kind (see [LICENSE](LICENSE)). Don't use it for production data you can't afford to lose.

**Contributions:** we are not accepting pull requests or outside contributions at this time. If you find a bug or a problem, please [open a GitHub issue](https://github.com/tridz-open/fcctl/issues).
