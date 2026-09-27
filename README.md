# mini-bmc

A small, self-contained simulator of a server BMC (Baseboard Management Controller):
a C sensor daemon, a DMTF Redfish REST API on top of it, and a CI pipeline with
sanitizers, coverage, static analysis and conformance tests. No hardware required.

| Component | Status |
|---|---|
| `sensord` — simulated sensors + fault injection over a Unix socket (C11) | done |
| `redfishd` — Redfish API (Python / FastAPI), validated against DMTF schemas | MVP done |

## Quick start (Linux / WSL2)

```sh
make -C sensord                      # build
make -C sensord test                 # Unity unit tests
make -C sensord sanitize             # same, under ASan + UBSan
python3 -m venv .venv && .venv/bin/pip install -e "./redfishd[dev]"
.venv/bin/pytest                     # integration + DMTF conformance tests

./sensord/build/sensord -s /tmp/sensord.sock &
printf '{"cmd":"read_all"}\n' | nc -U -q1 /tmp/sensord.sock
printf '{"cmd":"inject_fault","sensor":"cpu_temp","mode":"overtemp"}\n' | nc -U -q1 /tmp/sensord.sock
```

The wire protocol, fault semantics and roadmap are documented in [CLAUDE.md](CLAUDE.md).

## License

MIT. `sensord/third_party/unity` is [Unity](https://github.com/ThrowTheSwitch/Unity) (MIT).
