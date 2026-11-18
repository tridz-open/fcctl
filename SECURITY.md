# Security policy

fcctl is early alpha software, provided "as is" without warranty of any kind (see [LICENSE](LICENSE)).

## Reporting a vulnerability

Please report security issues **privately**, not in public issues. Use GitHub's private vulnerability reporting: open the repository's **Security** tab and click **Report a vulnerability**
(<https://github.com/tridz-open/fcctl/security/advisories/new>).

Include the version or tag, what you did, what you expected and what happened. We aim to acknowledge within 7 days.

## Scope and threat model

- fcctl runs on your machine with your Frappe Cloud session. Anyone who holds the session, `FCCTL_KEY` and the approval secret can bypass every fcctl check by calling the API directly.
- The approval step is a local speed bump against accidents, buggy scripts and over-eager agents. Approver names are not authenticated, so it is not separation of duties. Use separate Frappe Cloud accounts with restricted roles for that.
- State lives in `$FCCTL_HOME` (directory 0700, files 0600). The session file is Fernet-encrypted with `FCCTL_KEY`. Approvals are HMAC-signed; the audit log is hash-chained for tamper evidence. Approvals, journal and audit are not encrypted (integrity, not confidentiality).
- Credentials (sid, CSRF, Pilot tokens, approval tokens) are not logged, and the approval token is never accepted on the command line.
- fcctl does not bypass Cloudflare or bot protection.
