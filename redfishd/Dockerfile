# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Same fixed UID as the sensord image, so the shared socket is reachable. The firmware and
# TLS directories are owned by it so the named volumes mounted there start out writable.
RUN useradd --system --uid 10001 --no-create-home bmc \
    && mkdir -p /var/lib/redfishd/firmware /var/lib/redfishd/tls \
    && chown 10001 /var/lib/redfishd/firmware /var/lib/redfishd/tls

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY --chmod=755 start.sh /usr/local/bin/redfishd-start

USER 10001
ENV SENSORD_SOCKET=/run/mini-bmc/sensord.sock \
    REDFISH_FIRMWARE_DIR=/var/lib/redfishd/firmware
EXPOSE 8000
# The service root is the one resource readable without credentials; the check verifies the
# server against its own certificate, so a broken TLS setup is unhealthy too.
HEALTHCHECK --interval=2s --timeout=2s --retries=15 \
    CMD ["python", "-c", "import ssl, urllib.request as u; u.urlopen('https://127.0.0.1:8000/redfish/v1/', timeout=2, context=ssl.create_default_context(cafile='/var/lib/redfishd/tls/cert.pem'))"]
CMD ["redfishd-start"]
