#!/usr/bin/env bash
# Smoke test against a running stack (docker compose up --build --wait).
#   REDFISH_PORT (or BASE_URL), REDFISH_USERNAME, REDFISH_PASSWORD override the defaults.
set -euo pipefail

BASE_URL=${BASE_URL:-http://127.0.0.1:${REDFISH_PORT:-8000}}
USER_PASS="${REDFISH_USERNAME:-admin}:${REDFISH_PASSWORD:-admin}"
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

check "service root is public"            200 "$BASE_URL/redfish/v1/"
check "chassis requires auth"             401 "$BASE_URL/redfish/v1/Chassis/1"
check "chassis with basic auth"           200 -u "$USER_PASS" "$BASE_URL/redfish/v1/Chassis/1"
check "wrong password is rejected"        401 -u "admin:wrong" "$BASE_URL/redfish/v1/Chassis/1"

# End to end through both containers: a live reading from sensord.
# The status code goes on the last line so a failure can report it.
response=$(curl -s -w '\n%{http_code}' -u "$USER_PASS" \
    "$BASE_URL/redfish/v1/Chassis/1/Sensors/cpu_temp") || true
status=${response##*$'\n'}
# Prints the Reading, or nothing if the body is not JSON or has no Reading (e.g. an error).
reading=$(printf '%s' "${response%$'\n'*}" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["Reading"])
except (ValueError, KeyError, TypeError):
    pass
')
if [[ $reading =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "ok    cpu_temp reading from sensord ($reading Cel)"
else
    echo "FAIL  cpu_temp reading: HTTP $status, got '$reading'$(no_response_hint "$status")"
    failures=$((failures + 1))
fi

check "power off via Reset action"        204 -u "$USER_PASS" -X POST \
    -H 'Content-Type: application/json' -d '{"ResetType": "ForceOff"}' \
    "$BASE_URL/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"

if ((failures)); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
