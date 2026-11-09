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
│   ├── Makefile
│   └── Dockerfile        # multi-stage: gcc build stage, slim runtime
├── redfishd/
│   ├── start.sh          # image CMD: makes the TLS certificate on first start, runs uvicorn
│   ├── app/              # FastAPI application (import name: app)
│   │   └── routers/      # one module per Redfish resource family
│   ├── tests/            # unit tests (FakeSensord, no daemon needed)
│   ├── pyproject.toml    # runtime + dev dependencies, pinned
│   └── Dockerfile
├── tests/                # integration + conformance tests against a real sensord
│   └── redfish_schemas/  # vendored DMTF JSON schemas + vendor.py to refresh them
├── scripts/              # smoke_test.sh (curl checks), service_validator.sh and
│                         # protocol_validator.sh (DMTF validators), check_protocol_report.py,
│                         # sensordctl.sh (raw sensord requests), make_fw_image.py
├── ops/coworkify/        # nightly.py (workflow definition + installer), steps/ (its Python steps)
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
  - `{"cmd": "read_all"}` → `{"ok": true, "sensors": [{"name": "...", "value": 42.0, "unit": "C", "status": "ok", "stuck": false}, ...]}`
  - `{"cmd": "read", "sensor": "cpu_temp"}` → `{"ok": true, "sensor": {...}}`
  - `{"cmd": "inject_fault", "sensor": "cpu_temp", "mode": "overtemp|stuck|disconnected|noise"}` → `{"ok": true}`
  - `{"cmd": "clear_faults"}` → `{"ok": true}` (all sensors return to nominal)
- `status` is one of `ok`, `warning`, `critical`, `unavailable`; derived from per-sensor
  thresholds (see `sensors.c`). An `unavailable` sensor reports `"value": null`.
- `stuck` is `true` once a sensor's value has been identical for `STUCK_TICKS` (10) consecutive
  ticks. It is judged from the values alone (full `double` precision, before the one-decimal
  rounding on the wire), never from the injected fault, and is independent of `status`.
  A healthy sensor jitters every tick, so it is never stuck; a disconnected one (NaN) neither.
  `clear_faults` resets it at once; otherwise it clears on the first tick the value moves.
- Fault semantics (take effect immediately):
  - `overtemp` — temperature sensors only; value jumps above the critical threshold
  - `stuck` — value freezes; status stays whatever it was (a silent failure, detectable only by
    watching the value over time: `"stuck": true` after `STUCK_TICKS` ticks)
  - `disconnected` — `value: null`, `status: "unavailable"`
  - `noise` — jitter ×10 around nominal
- Errors: `{"ok": false, "error": "<code>"}`, never a crash. Codes:
  `bad_request` (not a flat JSON object of strings), `missing_field`, `unknown_cmd`,
  `unknown_sensor`, `unknown_mode`, `invalid_fault_for_sensor`, `line_too_long` (> 1023 bytes;
  the rest of that line is discarded, the connection stays open).
- When all client slots are taken, a new connection gets `too_many_clients`, then is
  half-closed (`shutdown(SHUT_WR)`) and kept up to 1 s (at most 4 such connections), so a
  client that writes its request late still reads the reply instead of getting `EPIPE`.
- A client that does not read its responses (send would block) is disconnected rather than
  stalling the daemon.

## redfishd

### Setup, build & test

```
python3 -m venv .venv && .venv/bin/pip install -e "./redfishd[dev]"   # one venv for everything
(cd redfishd && ../.venv/bin/pytest)       # unit tests
.venv/bin/pytest                           # integration + DMTF conformance (needs make -C sensord)
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pyrefly check && (cd redfishd && ../.venv/bin/pyrefly check)
docker compose up --build --wait                                       # REDFISH_PORT picks the host port
docker compose cp redfishd:/var/lib/redfishd/tls/cert.pem mini-bmc-ca.pem && export REDFISH_CA=mini-bmc-ca.pem
scripts/smoke_test.sh                                                  # HTTPS, verified against REDFISH_CA
PYTHONPATH=redfishd python3 scripts/make_fw_image.py 1.1.0 -o fw.bin   # demo image (--fail-selftest, --corrupt)
RSV=/tmp/dmtf/bin/rf_service_validator scripts/service_validator.sh    # DMTF Service Validator (stack running)
RPV=/tmp/dmtf/bin/rf_protocol_validator scripts/protocol_validator.sh  # DMTF Protocol Validator (stack running)
```

Run with `uvicorn --factory app.main:create_app`: importing `app.main` has no side effects
(no module-level app, no firmware directory created). Environment: `REDFISH_USERNAME`,
`REDFISH_PASSWORD`, `REDFISH_UUID`, `REDFISH_FIRMWARE_DIR` (default `/tmp/mini-bmc-firmware`;
a named volume in Compose), `REDFISH_REBOOT_SECONDS` (simulated reboot, default 3).

Modules (`redfishd/app/`): `main.py` (app factory), `config.py` (env settings),
`odata.py` (pinned @odata.type versions), `query.py` (query parameters, `$expand`), `protocol.py` (HTTP headers, JSON response class),
`errors.py` (Redfish error bodies, Base 1.24
registry subset), `sensord_client.py`, `sensor_catalog.py` (static Redfish metadata per
sensor), `power.py` (simulated host power), `sessions.py` + `auth.py`, `tasks.py`
(TaskManager), `firmware_image.py` (image format, verification), `firmware_store.py`
(A/B slots, rollback), `firmware_update.py` (update flow + Update registry subset), and `routers/`.

### Implemented (Week 2 MVP)

- `GET /redfish`, `GET /redfish/v1/` (ServiceRoot), `GET /redfish/v1/$metadata` (CSDL) and
  `GET /redfish/v1/odata` (service document) — the only unauthenticated reads (DSP0266).
  `$metadata` is generated from `odata.TYPES`; ServiceRoot links and the service document
  share `TOP_LEVEL` in `routers/service_root.py`
- `Chassis/1`, its `Sensors` collection and `Sensor` resources (modern model, not legacy
  `Thermal`); `HealthRollup` is the worst sensor health, an unreadable sensor counts as Warning.
  A sensor sensord reports as `stuck` is at least Warning (Critical stays Critical), keeps its
  last `Reading`, and gets `Status.Conditions` with `SensorEvent.1.2.InvalidSensorReading`
  (a Condition uses `Severity`, not `MessageSeverity`, and has no `@odata.type`)
- `Systems/1` with `ComputerSystem.Reset` (7 ResetTypes, advertised via AllowableValues)
- `SessionService`: login returns `X-Auth-Token` + `Location`; DELETE logs out; 1800 s idle
  timeout. HTTP Basic is accepted too. A sent token is never silently replaced by Basic.
- `AccountService` (`routers/account_service.py`), read-only: `Accounts/1` is the env account
  (`RoleId` Administrator, `Password` null, `Links.Role`), `Roles/{Administrator,Operator,ReadOnly}`
  with DSP0266's exact `AssignedPrivileges` (the Protocol Validator compares the sets). Writes are
  405 (GET-only routes). The account response comes from `protocol.with_etag()`: a weak ETag,
  SHA-256 of the canonical JSON before `@odata.etag` is added, in both the header and
  `@odata.etag` (DSP0266 requires ETags on ManagerAccount). Sensors get no ETag: readings change
  every tick. Roles are descriptive only; nothing is enforced with one Administrator account.
- Query parameters (issue #5, `query.py`): an app-wide dependency ignores unknown params without
  `$`, answers any other `$` param with 501 `QueryParameterUnsupported`, and `$expand` on a route
  outside `EXPANDABLE` (only the Sensors collection) with 400 `QueryNotSupportedOnResource`.
  `$expand` = `*` or `.`, optionally `($levels=1)`: Members become full Sensor bodies (same
  `sensor_body()` as a single GET) from one `read_all`. `~` -> 501; `$levels` other than 1 -> 400
  `QueryParameterOutOfRange`; anything else -> 400 `QueryParameterValueFormatError`.
  `ProtocolFeaturesSupported` in ServiceRoot is `query.PROTOCOL_FEATURES`. The plain collection
  still needs no sensord. Rules come from DSP0266 "Query parameter overview" (quoted in DMTF's
  Redfish-Protocol-Validator); messages from Base 1.24.0.
- HTTP headers (`protocol.py`, installed last in `create_app`): a request with an `OData-Version`
  other than 4.0 -> 412 `HeaderInvalid`; every response gets `OData-Version: 4.0` and
  `Cache-Control: no-cache`; every GET to a known path gets `Allow` (from `app.openapi()` paths:
  FastAPI 0.141 includes routers lazily, so `app.routes` does not list them); every resource body
  gets `Link: <schema>; rel=describedby` from its `@odata.type` via `RedfishJSONResponse`, the
  app's `default_response_class`. A route that builds its own response must use
  `RedfishJSONResponse`, not `JSONResponse`. POST `Sessions/Members` is the same login as POST
  `Sessions` (DSP0266).
- Every failure is a Redfish error body; sensord unreachable -> 503 + `Retry-After`
- `/docs`, `/redoc`, `/openapi.json` are disabled (they would be unauthenticated)
- Responses validated against DMTF schemas from Redfish-Publications **2026.2**
  (`tests/redfish_schemas/vendor.py` pins the tag and SHA-256)

### Implemented (Week 3: firmware update)

- Image: `MBFW` magic, format version, 2-byte manifest length, JSON manifest
  (`component`, `version`, `sha256`, optional `selftest`), payload. Checked in order: total
  size, magic, format, manifest, component, version, SHA-256. Unknown manifest keys are ignored.
  Each rejection has a stable `reason` code.
- `FirmwareStore`: `slot_a.bin` / `slot_b.bin` + `state.json` (the commit point). Writes are
  tmp + fsync + `os.replace` + directory fsync. `stage()` first marks the target slot as being
  rewritten, then writes it, then records it as the trial slot; `boot_trial()` commits or rolls
  back. At startup an unfinished trial is rolled back and a damaged active slot falls back to
  the other one; a corrupt `state.json` raises `StoreError` (the service refuses to start).
  In memory the state is one frozen `StoreState` (active, trial, slots); `_commit()` saves it,
  then swaps the reference, so readers in other threads never see a half-applied change or one
  a failed write left unsaved. A handler needing several values reads `store.state` once.
- `TaskService` + `Tasks/{id}` + `Tasks/{id}/Monitor` (DSP0266 task monitor: 202 with
  `Location` and `Retry-After` while running, then 200). `TaskEvent.1.1` messages; work raises
  `TaskFailed` for an expected failure, anything else is logged as a crash. Up to 32 tasks,
  oldest finished one evicted; all running -> `TaskLimitReached` -> 503.
- `UpdateService` with `MultipartHttpPushUri` (`UpdateService/upload`, parts
  `UpdateParameters` + `UpdateFile`; `Targets` empty or the BMC). Verification is synchronous:
  bad image -> 400 `Update.1.5.VerificationFailed` with the reason in `Resolution`, too large
  -> 413, update already running -> 409 `ResourceInUse`. Otherwise 202 + task monitor `Location`.
  A failed self-test ends the Task in `Exception` with `ActivateFailed` and the old version
  still running. Blocking file I/O runs in `asyncio.to_thread`.
- `FirmwareInventory`: `BMC` (active) and `BMC_Backup` (`Absent` / `Updating` / `StandbySpare`).
- Root `tests/`: conformance crawl includes a completed and a rolled-back Task;
  `test_firmware_update.py` checks sensors stay readable during the simulated reboot.
  The smoke test pushes a corrupt and a good image through the Compose stack.

### Fault injection (Week 3, issue #4)

`tests/test_fault_injection.py` injects every fault mode into a real sensord and checks the
Sensor status and chassis `HealthRollup` through Redfish, then that `clear_faults` restores all
of it. The stuck case starts out looking healthy and is flagged only after `STUCK_TICKS`.
The conformance crawl runs once with a flagged stuck sensor so `Conditions` are validated.

### DMTF Redfish Service Validator (Week 3)

`scripts/service_validator.sh` runs `redfish_service_validator==3.1.8` (own venv, not a
redfishd dependency) against a running Compose stack, in the CI `docker` job after the smoke
test. It first injects overtemp / disconnected / stuck faults and a firmware update that fails
its self-test, so the crawl sees Critical, null-Reading and Conditions sensors and an
`Exception` Task (the smoke test leaves a `Completed` one); faults are cleared on exit. CSDL
comes from `tests/redfish_schemas/vendor.py --csdl DIR`: the same pinned, checksummed
Redfish-Publications release as the JSON schemas, extracted at run time (not committed), and the
validator runs with `--skipschema` so it never downloads from dmtf.org. It exits non-zero only
on FAIL (WARN does not fail it); the HTML report is uploaded as the `service-validator-report`
artifact. Baseline: 0 WARN, 0 FAIL.

### DMTF Redfish Protocol Validator

`scripts/protocol_validator.sh` runs `redfish-protocol-validator==1.3.2` (same CI venv
`/tmp/dmtf`, after the Service Validator) and ignores its exit code: it fails on anything,
including what this service accepts. `scripts/check_protocol_report.py` compares the TSV report
with `KNOWN_FAILURES` (assertion ID -> reason): empty now. The roles entry went when
AccountService landed and the two TLS entries when HTTPS did (each time the check flagged them as
passing); the mechanism stays for the next accepted gap, tested with a sample list. A FAIL or WARN outside the list fails CI, and so does a
listed one that passes (remove it). Reports are uploaded with the Service Validator's as the
`dmtf-validator-reports` artifact. Its first run (2026-11) found 7 real gaps, all fixed.

### TLS

HTTPS only, on the one port (8000 in the container). `redfishd/start.sh` is the image's CMD: on
first start it makes a self-signed EC P-256 certificate (SAN `localhost`, `127.0.0.1`, 825 days)
in the `tls` named volume (`/var/lib/redfishd/tls`, key 0600, cert written last so its presence
means both are complete), then execs uvicorn with `--ssl-keyfile/--ssl-certfile`. It survives
restarts. The HEALTHCHECK verifies against that cert. Clients copy it out with
`docker compose cp redfishd:/var/lib/redfishd/tls/cert.pem mini-bmc-ca.pem` and verify against
it, never `-k`: the scripts take `REDFISH_CA` (curl `--cacert`, Protocol Validator
`--ca-bundle`); the Service Validator itself never verifies certificates (hard-coded in its
client). CI sets `REDFISH_CA` for the whole docker job. No HTTP listener, so the Protocol
Validator's HTTP probes on the same port fail to connect and the two HTTPS-only checks are
NOT_TESTED. Python code is unchanged; TestClient tests do not use TLS.

### Known limitations (by design, for now)

Self-signed certificate, no CertificateService to replace it; a single account from env vars, published read-only in AccountService with
the predefined roles (not enforced); no lockout or rate limiting on login;
sessions and Tasks live in memory and vanish on restart (installed firmware persists).
The image SHA-256 gives integrity, not authenticity: no signature check, so any logged-in
user can build an installable image. One updatable component (the BMC); the "reboot" is a
timed wait and the self-test result is a flag in the image.

## Nightly regression (Week 4)

`ops/coworkify/nightly.py` holds a Coworkify workflow and installs it over Coworkify's API
(`--print` shows it, `--run-now` starts one; `COWORKIFY_URL`, `COWORKIFY_USERNAME`,
`COWORKIFY_PASSWORD`). Each `python` step is a file in `steps/`, inlined at install time, so
`tests/test_nightly_workflow.py` can run it. Cron `0 2 * * *` (Asia/Taipei). Flow: remember the
time -> dispatch `ci.yml` with `repeat=5` -> check for 204 (`http_request` does not fail on an
error status, so an expired token gets its own notify) -> `wait_for` the run created since then
(the dispatch API returns no run id) -> green: notify; red: list jobs -> `digest` -> notify red
-> `agent_step` -> notify analysis. Red facts never depend on the model. Green also notifies, so
silence means the pipeline broke (a `wait_for` timeout notifies nobody; it fails the run in
Coworkify). Needs the Coworkify connection `github`: a fine-grained token, Actions read/write on
this repo; polling must send it (30 s polling exceeds the anonymous 60/hour). `digest` reads
annotations itself, anonymously, rather than with `for_each`: Coworkify's cascade follows
`depends_on` only, so a reduce under a branch not taken stays pending forever.

CI side: `workflow_dispatch` input `repeat` loops the integration suite; each run writes
`junit/run-N.xml`. On failure `scripts/annotate_failures.py` prints `::error` annotations, with
"failed in K of N runs" when repeated (max 10 per step, GitHub's limit). The concurrency group
includes the event, so a push cannot cancel the nightly dispatch.

## Conventions

- Develop and test on Linux (WSL2 or a container), not native Windows.
- C: C11, `-Wall -Wextra -Werror`; unit tests with Unity; coverage with gcov/lcov;
  static analysis with cppcheck; run tests under AddressSanitizer and UBSan in CI.
- Python: 3.11+, FastAPI, pytest, ruff with an explicitly pinned rule set in
  `pyproject.toml` (`[tool.ruff.lint] select = [...]`) so results do not drift across ruff versions;
  pyrefly for types (configs in both `pyproject.toml` files; each resolves its own `conftest`).
- Redfish responses are plain dicts; correctness is checked against the official schemas,
  not against our own models. Unit tests still pin values schemas cannot (e.g. UCUM units).
- FastAPI dependencies that touch shared state are `async def` (plain `def` runs in a thread pool).
- Every feature lands together with its tests. CI must stay green on `main`.
- Validate Redfish responses against the official DMTF JSON schemas.
- Do not name anything `openbmc`; that is an unrelated Linux Foundation project.

## Roadmap & status

- [x] **Week 0** — GitHub Actions CI added to the author's existing repos (Coworkify, Codoctopus); both green
- [x] **Week 1** — `sensord`: C daemon, socket protocol above, Unity tests, Makefile, coverage, cppcheck, sanitizers, CI
- [x] **Week 2** — `redfishd` MVP endpoints, pytest suite, schema validation, Docker Compose, CI → **MVP done**
- [x] **Week 3** — firmware update + rollback (UpdateService, TaskService, A/B slots), fault-injection tests (stuck detection, issue #4), DMTF Redfish Service Validator in CI
- [x] **Week 4 (stretch)** — nightly regression run scheduled by Coworkify; failed-log triage via a Codoctopus agent step (live: green run and refused dispatch; the red path with the model has only run against fake GitHub and model)

## References

- DMTF Redfish specification (DSP0266) and Redfish JSON schemas
- DMTF Redfish-Service-Validator (open-source conformance tool)
- DMTF Redfish Mockup Server (reference behaviour for comparison)
