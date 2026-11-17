# Refresh the committed Pilot dashboard contract from a live admin dashboard (network).
# usage: just contract-refresh https://<admin-host>
contract-refresh host:
    rm -rf /tmp/pilot-assets
    python -I scripts/extract_pilot_contract.py --fetch {{host}} /tmp/pilot-assets
    python -I scripts/extract_pilot_contract.py /tmp/pilot-assets > tests/fixtures/pilot_dashboard_contract.json
