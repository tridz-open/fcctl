# fcctl

> **Experimental. Built on a pre-public API.** fcctl talks to Frappe Cloud's private dashboard and Pilot endpoints, which are not a published API and can change without notice. Expect breaking changes between 0.x releases, pin a version, and try writes on a throwaway site first. See [Status](#status-alpha-unofficial-api).

A Python 3.11+ CLI and library for Frappe Cloud v2. It covers both halves of the platform: Central (`dashboard.frappe.io`: team, billing, servers, snapshots, buckets) and Pilot (the gateway on each server: sites, apps, backups, tasks, logs). It adds a safety layer on top: per-profile policies, approval for destructive operations, a local journal so interrupted operations can be reconciled, and a hash-chained audit log.

## Why it exists

fcctl was created to start the SaaS work behind [URY](https://ury.app), an open-source restaurant POS and ERP built on Frappe and ERPNext ([github.com/ury-erp/ury](https://github.com/ury-erp/ury)). URY's hosted offering needed to create, restore and manage customer sites on Frappe Cloud from a control plane, without a person clicking through the dashboard. fcctl is that automation layer, kept product-neutral so any Frappe SaaS can use it.

## A note to the Frappe team

fcctl is built by Tridz Technologies, a team that builds on Frappe Cloud. We wrote it to automate our own sites and servers: listing and inspecting them, taking backups, provisioning, and guarding destructive operations behind an approval step.

- It calls the same dashboard endpoints the Frappe Cloud web UI calls, signed in with a normal user account (email code). Nothing else.
- It is unofficial and not endorsed by Frappe Technologies.
- It does not bypass Cloudflare or any bot protection. It sends an honest User-Agent (`fcctl/<version> (+https://github.com/tridz-dev/fcctl)`), and when a challenge appears it stops with exit code 10.
- We will move to an official API or token flow the moment one exists, and we are happy to change, rate-limit or take this project down on request.
- Contact: open an issue on this repository, or see [SECURITY.md](SECURITY.md) for security reports.

## Status: alpha, unofficial API

Read this before using fcctl.

- **Not affiliated with or endorsed by Frappe Technologies.** "Frappe" and "Frappe Cloud" are their trademarks.
- **fcctl uses the private dashboard API.** fcctl calls the same HTTP endpoints the Frappe Cloud web dashboard calls, signed in with your own account. Frappe has not documented or published these endpoints as a public API. They can change or disappear without notice, and any fcctl command can break when they do. Pin a version, and test against a non-production team first.
- **Some request bodies are unverified.** Many endpoints were mapped by reading the open-source code rather than by calling them live; `docs/API_IA.md` marks which are verified (V/R) and which are source-only (S/W). Treat every write you have not tried yourself as experimental.
- **Your Frappe Cloud terms still apply.** You are responsible for using fcctl within them. Automated sign-in (the IMAP option below) and automation in general may be restricted. If you plan to run fcctl unattended or at volume, ask Frappe first. fcctl never solves CAPTCHAs or Cloudflare challenges: when one appears, it stops with exit code 10.
- **The safety layer guards against mistakes, not attackers.** Anyone who holds your session can call the API directly and bypass every fcctl check. For the full model, see [Safety model](#safety-model).
- **The interfaces may change.** The CLI, the library and the on-disk formats can change between 0.x releases.

Please report bugs and API drift as issues. Do not open issues with Frappe about fcctl behaviour.

## Prerequisites

- Python 3.11 or newer.
- A Frappe Cloud v2 account, plus the id of the team you want to manage (`TEAM-xxxxx`, shown in the dashboard).
- Access to that account's mailbox. Sign-in works by a 6-digit code sent by email; fcctl does not support passwords or API keys.
- To manage sites on a server, your role needs the `server:console` capability. Without it, fcctl cannot get a Pilot token.
- Optional: [uv](https://docs.astral.sh/uv/) or pipx, to install fcctl as a tool.

## Install

fcctl is not on PyPI yet. Install from a clone:

```bash
git clone https://github.com/tridz-dev/fcctl.git
cd fcctl

# Option A: uv (recommended)
uv tool install .                 # puts `fcctl` on your PATH
# or, for development:
uv venv && uv pip install -e '.[dev]'

# Option B: pipx
pipx install .

# Option C: plain pip in a virtualenv
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

Optional extras:

| Extra | Adds | Needed for |
|---|---|---|
| `yaml` | pyyaml | `-o yaml` output |
| `s3` | boto3 | copying backups to S3-compatible storage |
| `browser` | playwright | DEPRECATED, will be removed: library-only Playwright fallback (see [Deprecated: browser fallback](#deprecated-browser-fallback-will-be-removed)) |

Check the install with `fcctl --help`, `fcctl version` and `fcctl guide`.

## First run

### 1. Create an encryption key

fcctl encrypts your session (the dashboard `sid` cookie, the CSRF token, and Pilot tokens) with a Fernet key, and uses the same key to sign its approval records. Generate the key once and keep it somewhere safe, such as a password manager or the OS keychain:

```bash
export FCCTL_KEY=$(python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
```

Every later command needs the same `FCCTL_KEY`. If you lose or change it, run `fcctl auth login` again.

### 2. Create a profile

A profile ties together a login email, a team and a policy:

```bash
fcctl profile create me --email you@example.com --team TEAM-xxxxx   # policy defaults to guarded
fcctl profile use me
fcctl profile list
```

To set a different policy, pass `--policy full|guarded|read_only|protected`. To let a profile run one specific operation that its policy otherwise denies, pass `--allow OP` (repeatable). Profiles are stored in `$FCCTL_HOME/config.toml`.

### 3. Sign in

Pick one of three ways to enter the code.

**Manual (interactive):** fcctl emails a code and prompts you for it:

```bash
fcctl auth login
```

**From stdin (scripts):**

```bash
read -r CODE && echo "$CODE" | fcctl auth login --code-stdin
```

**IMAP auto-login (unattended):** fcctl requests the code, then polls a mailbox for the message from Frappe Cloud. Use a dedicated mailbox and an app password (for Gmail, an app password with IMAP enabled), never your main password:

```bash
export FCCTL_IMAP_HOST=imap.gmail.com
export FCCTL_IMAP_USER=ops-mailbox@example.com
export FCCTL_IMAP_PASS='app-password'        # better: read it from your keychain or secret manager
fcctl auth login --code-source imap
```

fcctl accepts only messages that arrive after the login request starts, so an old code is never reused.

Then confirm the session:

```bash
fcctl auth whoami            # add --caps to list your capabilities
fcctl doctor                 # checks the key, config, profile and session
```

## Usage examples

**Sign in once, then check the session**

```bash
fcctl profile create work --email you@example.com --team TEAM-xxxxx
fcctl profile use work
fcctl auth login                       # emails you a code; paste it
fcctl doctor                           # key, config, profile and session
```

**Install a marketplace app on a site**

```bash
# Fetch and install in one step, the same call the dashboard makes.
fcctl app add india_compliance --site shop.example.com -s server-xxxx

# Add to the bench only (no site); runs the bench validator.
fcctl app add hrms -s server-xxxx

# A private or forked app from GitHub, on a specific branch.
fcctl app add --repo https://github.com/you/your_app --branch main -s server-xxxx
```

**Provision a customer site (preview first)**

```bash
fcctl site provision acme.bom.frappe.cloud -s server-xxxx \
  --app erpnext --app hrms --dry-run       # shows the plan, changes nothing
fcctl site provision acme.bom.frappe.cloud -s server-xxxx --app erpnext --app hrms
```

**Back up before a risky change, and wait on long tasks**

```bash
fcctl backup create acme.bom.frappe.cloud -s server-xxxx   # waits by default
fcctl task list -s server-xxxx -o json | jq '.[0] | {command, status}'
fcctl task output TASK_ID -s server-xxxx   # full log of a failed task
```

**Script it: JSON out, exit codes in**

```bash
if ! fcctl site get acme.bom.frappe.cloud -s server-xxxx -o json > site.json; then
  echo "lookup failed with exit $?"   # 3 auth, 5 not found, 10 Cloudflare challenge ...
fi
```

**From Python**

```python
from fcctl import Client
from fcctl.session_store import SessionStore

c = Client(SessionStore(), team="TEAM-xxxxx")   # uses the session from `fcctl auth login`
for site in c.sites.list("server-xxxx"):
    print(site["name"], site.get("status"))
c.close()
```

## Common commands

```bash
fcctl server list                                   # servers in the team (Central)
fcctl site list -s server-xxxx                      # sites on one server (Pilot)
fcctl site get mysite.example.com -s server-xxxx
fcctl backup list mysite.example.com -s server-xxxx
fcctl backup create mysite.example.com -s server-xxxx
fcctl site maintenance mysite.example.com on -s server-xxxx
fcctl site provision demo.example.com -s server-xxxx --app erpnext --dry-run   # preview only
fcctl task wait TASK_ID -s server-xxxx              # Ctrl-C detaches (exit 130); the task keeps running
fcctl audit verify                                  # check the local audit chain
fcctl auth logout                                   # ends the server session too
```

Global flags: `--profile`, `-s/--server`, `-o table|json|yaml|ndjson` (json when stdout is not a terminal), `--wait/--no-wait`, `--timeout`, `--dry-run`, `--confirm`, `--no-progress`, `--debug`. Data goes to stdout; progress and errors go to stderr. Run `fcctl <group> --help` for any group.

Command groups: `auth profile server snapshot bucket billing notification app site backup task logs fleet approval audit reconcile watch alert doctor api guide completion version`.

Exit codes: 0 ok, 2 usage, 3 auth, 4 policy/permission, 5 not found, 6 conflict, 7 approval pending, 8 wait timeout (the remote operation keeps running), 9 task failed, 10 Cloudflare challenge, 11 rate limited, 12 validation, 13 outcome unknown, 130 interrupted.

## Environment variables

| Variable | Purpose |
|---|---|
| `FCCTL_KEY` | **Required.** Fernet key that encrypts the session and signs approvals |
| `FCCTL_HOME` | State directory (default `~/.fcctl`) |
| `FCCTL_PROFILE` | Profile to use (overrides `profile use`) |
| `FCCTL_EMAIL`, `FCCTL_TEAM`, `FCCTL_SERVER` | Defaults when there is no profile / no `-s` |
| `FCCTL_IMAP_HOST`, `FCCTL_IMAP_USER`, `FCCTL_IMAP_PASS` | IMAP auto-login (`--code-source imap`) |
| `FCCTL_APPROVAL_SECRET` | Base32 approver secret for guarded operations (`fcctl approval enroll` generates one) |
| `FCCTL_APPROVAL_TOKEN` | Approval token or authenticator code (otherwise `approve` prompts for it) |
| `FCCTL_USER` | Requester identity recorded on approvals (default: OS user) |
| `FCCTL_SMTP_HOST/PORT/USER/PASS`, `FCCTL_ALERT_FROM`, `FCCTL_ALERT_TO` | Email alerts |
| `FCCTL_TG_TOKEN`, `FCCTL_TG_CHAT` | Telegram alerts |
| `FCCTL_POLL` | Pilot task poll interval in seconds (default 15) |

Pass secrets through the environment or a secret manager. Don't put them in command-line arguments, which end up in shell history and `ps` output.

## Safety model

Every write passes through the safety layer before any API call:

1. **Policy (per profile).**
   - `full`: allows everything.
   - `guarded` (default): allows writes, but destructive operations need approval and cost-changing ones need `--confirm TARGET`.
   - `read_only`: allows no writes.
   - `protected`: allows no writes, except operations in the profile's `allow` list.
2. **Approval.** Destructive operations do not run straight away. These include `site drop`, `backup restore`, `server terminate` and `snapshot delete`. The command files a signed approval and exits with code 7. Someone then runs `fcctl approval approve ID --approver NAME` with the approval token, taken from `FCCTL_APPROVAL_TOKEN` or an interactive prompt (never from the command line). The token is the approval secret itself or a 6-digit RFC 6238 TOTP code; `fcctl approval enroll` generates a base32 secret plus an `otpauth://` URI, so a normal authenticator app works. The approver name must differ from the requester name, but names are not authenticated, so on one machine a requester can approve their own request by supplying another name. Treat this as a speed bump against accidents and agents, not separation of duties.
3. **Journal and reconcile.** fcctl records each operation before it sends the request. If the process dies mid-call, `fcctl reconcile --once` asks the remote what happened instead of blindly retrying.
4. **Audit.** fcctl appends a hash-chained JSONL log under `$FCCTL_HOME`. `fcctl audit verify` checks the chain.

**Limits.** All of this runs on your machine. The guard catches mistakes, buggy scripts and over-eager AI agents that go *through fcctl*. It is not access control:

- Anyone with your session, `FCCTL_KEY` and the approval secret can bypass it.
- Requester and approver names come from the environment and are not authenticated.
- Pilot tokens are bench-scoped: holding one gives full control of every site on that server.

For real separation of duties, use separate Frappe Cloud accounts with restricted roles.

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

## Known gaps

- Many request bodies are unverified (see `docs/API_IA.md`).
- CSRF refresh falls back to reading the dashboard boot page, which is fragile.
- The API has no in-place snapshot restore.
- No MCP server yet.

## Deprecated: browser fallback (will be removed)

`fcctl.browser.Browser` (Playwright, `pip install -e '.[browser]'` from a clone) and the `fallback` hook on `CentralClient` are deprecated and will be removed. Constructing `Browser` or using the fallback emits a `DeprecationWarning`. Default behaviour is HTTP-only: the fallback runs only if a library user wires it in explicitly. fcctl never solves challenges; the supported login is the plain HTTP path (`send_code`/`verify_code`: manual code, `--code-stdin`, or IMAP).

## License

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 Tridz Technologies.
