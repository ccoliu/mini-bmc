# CLAUDE.md — mini-bmc

Context for AI coding assistants (and humans) working in this repo.
Keep the **Roadmap & status** section up to date as work lands.

## What this project is

A small, self-contained simulator of a server BMC (Baseboard Management Controller),
built as a portfolio project to demonstrate server-firmware and test-infrastructure skills:
C systems code, a standards-based REST management API (DMTF Redfish), and a
test/CI pipeline with coverage, static analysis, and conformance validation.

No real hardware is needed. Sensors are simulated in software.

## Architecture

Three layers, all exercised in GitHub Actions CI:

| Layer | Language | Role |
|---|---|---|
| `sensord` | C11 | Simulated sensors (temperature, fan, power) with fault injection, served over a Unix domain socket |
| `redfishd` | Python 3.11+ / FastAPI | Implements a subset of the DMTF Redfish API; reads sensor data from `sensord` |
| `tests/` | Python / pytest | Integration, fault-injection, and Redfish conformance tests against the running stack |

Analogy: `sensord` is the thermometer and fans, `redfishd` is the butler who reports
on them, and the test layer is the auditor who questions the butler every night.

## Repository layout

```
mini-bmc/
├── sensord/
│   ├── src/              # daemon source
│   ├── tests/            # Unity unit tests
│   ├── third_party/unity # vendored Unity v2.6.0 (unmodified)
│   └── Makefile
├── redfishd/
│   ├── app/              # FastAPI application
│   └── pyproject.toml
├── tests/                # integration + conformance tests (pytest)
├── docker-compose.yml
├── .github/workflows/ci.yml
└── CLAUDE.md
```

## sensord

### Build & test

```
make -C sensord            # build/sensord
make -C sensord test       # Unity unit tests
make -C sensord sanitize   # unit tests under ASan + UBSan (binary: build/asan/sensord)
make -C sensord coverage   # gcov/lcov report in build/cov/html
make -C sensord cppcheck   # static analysis, fails on findings
pytest tests/              # socket-level integration tests (SENSORD_BIN overrides the binary)
```

Source modules (`sensord/src/`):

- `json.[ch]` — minimal JSON: flat-object parser (string values only) + bounded writer
- `sensors.[ch]` — sensor table, simulation step, thresholds, fault injection (no I/O)
- `protocol.[ch]` — one request line in, one response line out (pure, unit-tested)
- `server.[ch]` — poll()-based Unix socket server, signal handling
- `main.c` — option / environment parsing

### Interface

- Transport: newline-delimited JSON over a Unix domain socket.
  Path from `--socket`, else `SENSORD_SOCKET`, else `/tmp/sensord.sock`.
  Other knobs: `--seed`/`SENSORD_SEED` (PRNG seed, for reproducible runs),
  `--tick-ms`/`SENSORD_TICK_MS` (simulation step, default 1000 ms).
- Sensors: `cpu_temp`, `gpu_temp`, `inlet_temp` (°C), `fan1_rpm`, `fan2_rpm` (RPM), `psu_watts` (W).
- Requests (one JSON object per line; request values must be strings):
  - `{"cmd": "read_all"}` → `{"ok": true, "sensors": [{"name": "...", "value": 42.0, "unit": "C", "status": "ok"}, ...]}`
  - `{"cmd": "read", "sensor": "cpu_temp"}` → `{"ok": true, "sensor": {...}}`
  - `{"cmd": "inject_fault", "sensor": "cpu_temp", "mode": "overtemp|stuck|disconnected|noise"}` → `{"ok": true}`
  - `{"cmd": "clear_faults"}` → `{"ok": true}` (all sensors return to nominal)
- `status` is one of `ok`, `warning`, `critical`, `unavailable`; derived from per-sensor
  thresholds (see `sensors.c`). An `unavailable` sensor reports `"value": null`.
- Fault semantics (take effect immediately):
  - `overtemp` — temperature sensors only; value jumps above the critical threshold
  - `stuck` — value freezes; status stays whatever it was (a silent failure, detectable only by
    watching the value over time)
  - `disconnected` — `value: null`, `status: "unavailable"`
  - `noise` — jitter ×10 around nominal
- Errors: `{"ok": false, "error": "<code>"}`, never a crash. Codes:
  `bad_request` (not a flat JSON object of strings), `missing_field`, `unknown_cmd`,
  `unknown_sensor`, `unknown_mode`, `invalid_fault_for_sensor`, `line_too_long` (> 1023 bytes;
  the rest of that line is discarded, the connection stays open).
- A client that does not read its responses (send would block) is disconnected rather than
  stalling the daemon.

## redfishd scope

MVP (week 2):
- `GET /redfish/v1` (ServiceRoot)
- `GET /redfish/v1/Chassis/{id}` and thermal readings (temperatures, fans)
- `GET /redfish/v1/Systems/{id}`, `POST .../Actions/ComputerSystem.Reset`
- `SessionService` login with `X-Auth-Token`
- Correct `@odata.id` / `@odata.type` on every resource

Week 3:
- `UpdateService` with a simulated firmware update: image checksum verification,
  reject corrupt images, automatic rollback on failure
- Proper error responses when `sensord` is down or a sensor is faulted

## Conventions

- Develop and test on Linux (WSL2 or a container), not native Windows.
- C: C11, `-Wall -Wextra -Werror`; unit tests with Unity; coverage with gcov/lcov;
  static analysis with cppcheck; run tests under AddressSanitizer and UBSan in CI.
- Python: 3.11+, FastAPI, pytest, ruff with an explicitly pinned rule set in
  `pyproject.toml` (`[tool.ruff.lint] select = [...]`) so results do not drift across ruff versions.
- Every feature lands together with its tests. CI must stay green on `main`.
- Validate Redfish responses against the official DMTF JSON schemas.
- Do not name anything `openbmc`; that is an unrelated Linux Foundation project.

## Roadmap & status

- [x] **Week 0** — GitHub Actions CI added to the author's existing repos (Coworkify, Codoctopus); both green
- [x] **Week 1** — `sensord`: C daemon, socket protocol above, Unity tests, Makefile, coverage, cppcheck, sanitizers, CI
- [ ] **Week 2** — `redfishd` MVP endpoints, pytest suite, schema validation, Docker Compose, CI → **MVP done**
- [ ] **Week 3** — firmware update + rollback, fault-injection tests, DMTF Redfish Service Validator in CI
- [ ] **Week 4 (stretch)** — nightly regression run scheduled by Coworkify; failed-log triage via a Codoctopus agent step

## References

- DMTF Redfish specification (DSP0266) and Redfish JSON schemas
- DMTF Redfish-Service-Validator (open-source conformance tool)
- DMTF Redfish Mockup Server (reference behaviour for comparison)
