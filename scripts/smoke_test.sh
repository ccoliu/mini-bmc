#!/usr/bin/env bash
# Smoke test against a running stack (docker compose up --build --wait).
#   REDFISH_PORT (or BASE_URL), REDFISH_USERNAME, REDFISH_PASSWORD override the defaults.
set -euo pipefail

BASE_URL=${BASE_URL:-http://127.0.0.1:${REDFISH_PORT:-8000}}
USER_PASS="${REDFISH_USERNAME:-admin}:${REDFISH_PASSWORD:-admin}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
failures=0

check() {  # check <description> <expected status> <curl args...>
    local what=$1 want=$2
    shift 2
    local got
    # curl exits non-zero when it gets no response, but still prints 000 for the status;
    # without `|| true`, set -e would end the script here with no output at all.
    got=$(curl -s -o /dev/null -w '%{http_code}' "$@") || true
    if [[ $got == "$want" ]]; then
        echo "ok    $what ($got)"
    else
        echo "FAIL  $what: expected $want, got $got$(no_response_hint "$got")"
        failures=$((failures + 1))
    fi
}

no_response_hint() {
    [[ $1 == 000 ]] && echo " (no response: is the stack running?)"
    return 0
}

json_get() {  # json_get <key>... < body: prints body[key]..., or nothing if absent or not JSON
    python3 -c '
import json, sys
try:
    value = json.load(sys.stdin)
    for key in sys.argv[1:]:
        value = value[key]
    print(value)
except (ValueError, KeyError, TypeError):
    pass
' "$@"
}

fail() {
    echo "FAIL  $1"
    failures=$((failures + 1))
}

check "service root is public"            200 "$BASE_URL/redfish/v1/"
check "chassis requires auth"             401 "$BASE_URL/redfish/v1/Chassis/1"
check "chassis with basic auth"           200 -u "$USER_PASS" "$BASE_URL/redfish/v1/Chassis/1"
check "wrong password is rejected"        401 -u "admin:wrong" "$BASE_URL/redfish/v1/Chassis/1"

# End to end through both containers: a live reading from sensord.
# The status code goes on the last line so a failure can report it.
response=$(curl -s -w '\n%{http_code}' -u "$USER_PASS" \
    "$BASE_URL/redfish/v1/Chassis/1/Sensors/cpu_temp") || true
status=${response##*$'\n'}
reading=$(printf '%s' "${response%$'\n'*}" | json_get Reading)
if [[ $reading =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "ok    cpu_temp reading from sensord ($reading Cel)"
else
    fail "cpu_temp reading: HTTP $status, got '$reading'$(no_response_hint "$status")"
fi

check "power off via Reset action"        204 -u "$USER_PASS" -X POST \
    -H 'Content-Type: application/json' -d '{"ResetType": "ForceOff"}' \
    "$BASE_URL/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"

# Firmware update end to end: a corrupt image is refused, a good one is installed as a Task,
# survives its trial boot in the container's firmware volume and becomes the active version.
# The images come from the project's own builder (standard library only, so no venv needed).
UPLOAD="$BASE_URL/redfish/v1/UpdateService/upload"
FW_VERSION=1.1.0
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
touch "$tmp/headers" "$tmp/task"  # so a failed request is reported, not fatal under set -e
make_image() { PYTHONPATH="$ROOT/redfishd" python3 "$ROOT/scripts/make_fw_image.py" "$@" >/dev/null; }
make_image "$FW_VERSION" -o "$tmp/good.bin"
make_image 1.2.0 --corrupt -o "$tmp/corrupt.bin"
# The DSP0266 multipart push: an UpdateParameters part and an UpdateFile part.
PARAMS=(-u "$USER_PASS" -F 'UpdateParameters={};type=application/json')

check "corrupt image is rejected"         400 "${PARAMS[@]}" \
    -F "UpdateFile=@$tmp/corrupt.bin;type=application/octet-stream" "$UPLOAD"

status=$(curl -s -o /dev/null -D "$tmp/headers" -w '%{http_code}' "${PARAMS[@]}" \
    -F "UpdateFile=@$tmp/good.bin;type=application/octet-stream" "$UPLOAD") || true
monitor=$(awk 'tolower($1) == "location:" {print $2}' "$tmp/headers" | tr -d '\r')
if [[ $status != 202 || -z $monitor ]]; then
    fail "firmware upload: expected 202 with a Location, got $status$(no_response_hint "$status")"
    fail "firmware update: not attempted, the upload was refused"  # one line per check
else
    echo "ok    firmware upload accepted as a task ($status)"
    # The task monitor answers 202 while the update runs (including the simulated reboot).
    deadline=$((SECONDS + 30))
    while status=$(curl -s -o "$tmp/task" -w '%{http_code}' -u "$USER_PASS" \
            "$BASE_URL$monitor") && [[ $status == 202 ]] && ((SECONDS < deadline)); do
        sleep 0.5
    done
    state=$(json_get TaskState <"$tmp/task")
    version=$(curl -s -u "$USER_PASS" "$BASE_URL/redfish/v1/UpdateService/FirmwareInventory/BMC" |
        json_get Version)
    if [[ $status == 200 && $state == Completed && $version == "$FW_VERSION" ]]; then
        echo "ok    firmware update completed (BMC now at $version)"
    else
        fail "firmware update: monitor HTTP $status, TaskState '$state', BMC version '$version'"
    fi
fi

if ((failures)); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
