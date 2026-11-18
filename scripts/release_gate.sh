#!/usr/bin/env bash
# Release gate: fails (exit 1) if any leaked identifier, internal reference or banned file is present.
# Run from anywhere; it checks the repository root that contains this script.
set -u
cd "$(dirname "$0")/.."
command -v rg >/dev/null || { echo "release_gate: ripgrep (rg) is required" >&2; exit 2; }
fail=0
G=(rg -n --hidden -g '!.git' -g '!uv.lock' -g '!.venv' -g '!build' -g '!*.egg-info' -g '!__pycache__' \
   -g '!.pytest_cache' -g '!.ruff_cache' -g '!.hypothesis' -g '!scripts/release_gate.sh')

hit() { # hit <description> <command...>: any output = failure
  local desc=$1; shift
  local out; out=$("$@" 2>/dev/null || true)
  if [ -n "$out" ]; then echo "GATE HIT: $desc"; echo "$out"; fail=1; fi
}

hit "GSTIN" "${G[@]}" -i '\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]\b' .
# 'tridz' is allowed only as the project URL (tridz-open/fcctl) or the copyright holder (Tridz Technologies)
hit "tridz" bash -c "$(printf '%q ' "${G[@]}") -i tridz . | rg -v 'tridz-open/fcctl|Tridz Technologies'"
# Real names, team/server ids and other private values are never written in this repo: list them, one regex per
# line, in an UNTRACKED file (default .release-denylist, gitignored, or RELEASE_DENYLIST=<path>).
DENY="${RELEASE_DENYLIST:-.release-denylist}"
if [ -f "$DENY" ]; then
  while IFS= read -r pat; do
    [ -z "$pat" ] || [ "${pat#\#}" != "$pat" ] && continue
    hit "private denylist entry" "${G[@]}" -g "!$DENY" -i -e "$pat" .
  done < "$DENY"
else
  echo "release_gate: note: no $DENY file; only generic checks ran" >&2
fi
hit "billing identifiers" "${G[@]}" -i 'last4|card_last|exp_month|exp_year|mandate_id|razorpay|stripe_(customer|payment)' .
hit "card numbers" "${G[@]}" '\b(4[0-9]{12}([0-9]{3})?|5[1-5][0-9]{14})\b' .
hit "local paths" "${G[@]}" '/Users/|/private/tmp|/home/[a-z]' .
# URY is named publicly only in the README's "Why it exists" paragraph (the owner's decision); internal refs stay out.
hit "internal project refs" bash -c "$(printf '%q ' "${G[@]}") -i 'ury-portal|ury saas|URY_ORDER|\\bury\\b' . | rg -v '^\\./README\\.md:[0-9]+:fcctl was created to start the SaaS work behind \\[URY\\]'"
hit "real gateway host" "${G[@]}" 'bom\.frappe\.cloud' .
hit "missing research refs" "${G[@]}" 'research/' . -g '!.gitignore'
hit "phone numbers" "${G[@]}" -i '\+91[ -]?[0-9]{5}|[0-9]{10}' docs README.md
hit "hard-coded secrets" "${G[@]}" -i "api[_-]?(key|secret)\s*[:=]\s*[\"'][A-Za-z0-9]{12,}" .
# call_via_page is intentionally kept (deprecated browser fallback, owner decision) and is not gated.
for p in docs/bruno spikes PROGRESS.md fcctl/hooks.py docs/SDK_PLAN_V2.md docs/PILOT_SELFHOST_MEMO.md docs/ROLES.md scripts/fcctl-relogin.sh; do
  if [ -e "$p" ]; then echo "GATE HIT: banned path exists: $p"; fail=1; fi
done
if command -v gitleaks >/dev/null; then
  hit "gitleaks" gitleaks detect --no-git --source . --no-banner --redact
fi
[ "$fail" -eq 0 ] && echo "release gate: OK" || echo "release gate: FAILED"
exit "$fail"
