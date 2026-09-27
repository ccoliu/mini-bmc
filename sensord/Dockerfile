# syntax=docker/dockerfile:1

# --- build stage: compiler and headers live only here ---------------------------
FROM debian:bookworm-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev make \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN make clean all

# --- runtime stage: just the binary and libc -------------------------------------
FROM debian:bookworm-slim
# Same fixed UID as the redfishd image: the socket is created with mode 0755, so
# only the same user can connect to it through the shared volume.
RUN useradd --system --uid 10001 --no-create-home bmc \
 && mkdir -p /run/mini-bmc \
 && chown bmc /run/mini-bmc
COPY --from=build /src/build/sensord /usr/local/bin/sensord
USER 10001
ENV SENSORD_SOCKET=/run/mini-bmc/sensord.sock
# sensord creates the socket only once it is bound and listening.
HEALTHCHECK --interval=2s --timeout=2s --retries=15 CMD ["test", "-S", "/run/mini-bmc/sensord.sock"]
ENTRYPOINT ["sensord"]
