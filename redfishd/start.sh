#!/bin/sh
# Starts redfishd over HTTPS (the image's CMD). Like a real BMC on its first boot, it makes a
# self-signed certificate once and keeps it in the tls volume, so a client trusts it once:
#   docker compose cp redfishd:/var/lib/redfishd/tls/cert.pem mini-bmc-ca.pem
set -eu

TLS_DIR=${REDFISH_TLS_DIR:-/var/lib/redfishd/tls}

if [ ! -s "$TLS_DIR/cert.pem" ]; then
    umask 077  # the key is readable by this user only
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 825 \
        -subj "/CN=mini-bmc" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
        -keyout "$TLS_DIR/key.pem.tmp" -out "$TLS_DIR/cert.pem.tmp" 2>/dev/null
    chmod 644 "$TLS_DIR/cert.pem.tmp"  # public; clients copy it out
    mv "$TLS_DIR/key.pem.tmp" "$TLS_DIR/key.pem"
    mv "$TLS_DIR/cert.pem.tmp" "$TLS_DIR/cert.pem"  # last: a cert means both files are whole
    echo "redfishd: created a self-signed TLS certificate in $TLS_DIR" >&2
fi

exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 \
    --ssl-keyfile "$TLS_DIR/key.pem" --ssl-certfile "$TLS_DIR/cert.pem"
