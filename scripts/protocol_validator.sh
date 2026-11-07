#!/usr/bin/env bash
# DMTF Redfish Protocol Validator against a running stack (docker compose up --build --wait).
#   RPV (validator command, default rf_protocol_validator), REPORT_DIR (default
#   protocol-report), REDFISH_PORT (or BASE_URL), REDFISH_USERNAME, REDFISH_PASSWORD.
# Install the validator with:  pip install redfish-protocol-validator==1.3.2
set -euo pipefail

BASE_URL=${BASE_URL:-http://127.0.0.1:${REDFISH_PORT:-8000}}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RPV=${RPV:-rf_protocol_validator}
REPORT_DIR=${REPORT_DIR:-$ROOT/protocol-report}
rm -rf "$REPORT_DIR"

# The validator exits non-zero on any failure, including the ones this service accepts
# (no TLS, no roles); the comparison with the known failures below decides instead.
"$RPV" --rhost "$BASE_URL" --user "${REDFISH_USERNAME:-admin}" \
    --password "${REDFISH_PASSWORD:-admin}" --report-dir "$REPORT_DIR" --report-type both || true

reports=("$REPORT_DIR"/*.tsv)
if [[ ! -f ${reports[0]} ]]; then
    echo "FAIL  the validator wrote no report" >&2
    exit 1
fi
python3 "$ROOT/scripts/check_protocol_report.py" "${reports[0]}"
