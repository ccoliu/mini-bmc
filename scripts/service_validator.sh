#!/usr/bin/env bash
# DMTF Redfish Service Validator against a running stack (docker compose up --build --wait).
#   RSV (validator command, default rf_service_validator), REPORT_DIR (default
#   validator-report), REDFISH_PORT (or BASE_URL), REDFISH_USERNAME, REDFISH_PASSWORD,
#   REDFISH_CA (the stack's certificate, for this script's own curl requests; the validator
#   itself does not verify certificates).
# Install the validator with:  pip install redfish_service_validator==3.1.8
set -euo pipefail

BASE_URL=${BASE_URL:-https://127.0.0.1:${REDFISH_PORT:-8000}}
CURL_TLS=()
[[ -n ${REDFISH_CA:-} ]] && CURL_TLS=(--cacert "$REDFISH_CA")
curl() { command curl "${CURL_TLS[@]}" "$@"; }
USERNAME=${REDFISH_USERNAME:-admin}
PASSWORD=${REDFISH_PASSWORD:-admin}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RSV=${RSV:-rf_service_validator}
REPORT_DIR=${REPORT_DIR:-$ROOT/validator-report}
tmp=$(mktemp -d)
cleanup() {
    "$ROOT/scripts/sensordctl.sh" '{"cmd": "clear_faults"}' >/dev/null || true
    rm -rf "$tmp"
}
trap cleanup EXIT

# A fresh service shows only healthy sensors and no Tasks. Give the crawl the other shapes
# too: Critical, unreadable (Reading null) and stuck (Status.Conditions) sensors, and a
# firmware update Task that ended in Exception.
fault() {
    "$ROOT/scripts/sensordctl.sh" \
        "{\"cmd\": \"inject_fault\", \"sensor\": \"$1\", \"mode\": \"$2\"}" >/dev/null
}
fault cpu_temp overtemp
fault fan2_rpm disconnected
fault gpu_temp stuck

PYTHONPATH="$ROOT/redfishd" python3 "$ROOT/scripts/make_fw_image.py" 9.9.9 --fail-selftest \
    -o "$tmp/fails-selftest.bin" >/dev/null
monitor=$(curl -fsS -o /dev/null -D - -u "$USERNAME:$PASSWORD" \
    -F 'UpdateParameters={};type=application/json' \
    -F "UpdateFile=@$tmp/fails-selftest.bin;type=application/octet-stream" \
    "$BASE_URL/redfish/v1/UpdateService/upload" |
    awk 'tolower($1) == "location:" {print $2}' | tr -d '\r')

# Wait for that Task to finish and for gpu_temp to be flagged (sensord needs STUCK_TICKS).
deadline=$((SECONDS + 30))
until [[ $(curl -s -o /dev/null -w '%{http_code}' -u "$USERNAME:$PASSWORD" \
        "$BASE_URL$monitor") == 200 ]] &&
    curl -fsS -u "$USERNAME:$PASSWORD" "$BASE_URL/redfish/v1/Chassis/1/Sensors/gpu_temp" |
    grep -q '"Conditions"'; do
    if ((SECONDS >= deadline)); then
        echo "FAIL  setup: the Task did not finish or gpu_temp was not flagged as stuck" >&2
        exit 1
    fi
    sleep 0.5
done

# CSDL from the same pinned, checksummed DMTF release as the JSON schemas; --skipschema
# keeps the validator from downloading whatever release dmtf.org has today.
python3 "$ROOT/tests/redfish_schemas/vendor.py" --csdl "$tmp/csdl"
# Exits non-zero on any FAIL; the HTML report in REPORT_DIR lists every property checked.
"$RSV" --rhost "$BASE_URL" --user "$USERNAME" --password "$PASSWORD" --authtype Session \
    --schema_directory "$tmp/csdl" --skipschema --logdir "$REPORT_DIR"
