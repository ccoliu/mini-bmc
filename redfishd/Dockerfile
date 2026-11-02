# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Same fixed UID as the sensord image, so the shared socket is reachable. The firmware
# directory is owned by it so the named volume mounted there starts out writable.
RUN useradd --system --uid 10001 --no-create-home bmc \
    && mkdir -p /var/lib/redfishd/firmware \
    && chown 10001 /var/lib/redfishd/firmware

WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

USER 10001
ENV SENSORD_SOCKET=/run/mini-bmc/sensord.sock \
    REDFISH_FIRMWARE_DIR=/var/lib/redfishd/firmware
EXPOSE 8000
# The service root is the one resource readable without credentials.
HEALTHCHECK --interval=2s --timeout=2s --retries=15 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/redfish/v1/', timeout=2)"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
