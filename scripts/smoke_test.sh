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
    got=$(curl -s -o /dev/null -w '%{http_code}' "$@")
    if [[ $got == "$want" ]]; then
        echo "ok    $what ($got)"
    else
        echo "FAIL  $what: expected $want, got $got"
        failures=$((failures + 1))
    fi
}

check "service root is public"            200 "$BASE_URL/redfish/v1/"
check "chassis requires auth"             401 "$BASE_URL/redfish/v1/Chassis/1"
check "chassis with basic auth"           200 -u "$USER_PASS" "$BASE_URL/redfish/v1/Chassis/1"
check "wrong password is rejected"        401 -u "admin:wrong" "$BASE_URL/redfish/v1/Chassis/1"

# End to end through both containers: a live reading from sensord.
reading=$(curl -s -u "$USER_PASS" "$BASE_URL/redfish/v1/Chassis/1/Sensors/cpu_temp" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["Reading"])')
if [[ $reading =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
    echo "ok    cpu_temp reading from sensord ($reading Cel)"
else
    echo "FAIL  cpu_temp reading: got '$reading'"
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
