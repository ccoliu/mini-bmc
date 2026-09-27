#!/usr/bin/env bash
# Sends one request line to sensord inside the running compose stack and prints the reply.
#   scripts/sensordctl.sh '{"cmd": "inject_fault", "sensor": "cpu_temp", "mode": "overtemp"}'
# sensord has no network and no shell tools, so this goes through the redfishd container,
# which shares the socket volume (and the UID allowed to connect to it).
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 '<json request>'" >&2
    exit 2
fi

exec docker compose exec -T redfishd python -c '
import socket, sys
with socket.socket(socket.AF_UNIX) as s:
    s.connect("/run/mini-bmc/sensord.sock")
    s.sendall(sys.argv[1].encode() + b"\n")
    print(s.makefile().readline(), end="")
' "$1"
