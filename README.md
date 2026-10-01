# SmartCooling Controller

Windows-side telemetry and control application for an intelligent laptop
cooling pad built around an ESP32 and a 4-wire PWM fan.

The desktop app talks to the pad over a small JSON protocol (WebSocket or
serial), streams temperature/RPM/power telemetry into live charts, applies
thermal profiles, and keeps a local history for later export.

## Features

- **Live telemetry** — CPU/GPU/system temperatures, fan RPM, PWM duty and power
  draw, sampled on a configurable interval and rendered with `pyqtgraph`.
- **Thermal profiles** — built-in `silent` / `balanced` / `performance` /
  `turbo` curves plus custom fan curves, with hysteresis and ramp limiting so
  the fan never hunts.
- **ESP32 link** — automatic discovery, reconnect with backoff, and a versioned
  protocol so firmware and app can evolve independently.
- **Safety first** — a crash guard that clears its marker on graceful exit,
  safe-mode start-up, and hardware back-off when a sensor goes stale.
- **Privacy by default** — logs and exported support bundles pass through a
  redaction filter, so tokens and keys never leave the machine.
- **First-run wizard** — language, theme, data location and hardware backend,
  all reconfigurable later.

## Requirements

- Windows 10/11 (the app also runs head-less on Linux for CI and packaging).
- Python 3.11–3.13.
- An ESP32 cooling pad running a compatible firmware, or the built-in
  simulated backend for a hardware-free demo.

## Install (development)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ".[build]"
python run.py                     # or: python -m run
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--simulate` | Run against the simulated hardware backend. |
| `--safe` | Start with safe defaults and no hardware probing. |
| `--portable` / `--standard` | Store data next to the executable / under the user profile. |
| `--page KEY` | Open a specific page on start (e.g. `diagnostics`). |
| `--no-tray` | Run without a system-tray icon. |
| `--headless` | Deterministic smoke-test mode: no wizard, no tray, auto-quit. |
| `--version` | Print version information and exit. |

## Build a frozen bundle

```bash
python scripts/build/build.py            # preflight, lint, tests, freeze, verify
python scripts/build/build.py --release  # stamp a release build id
```

The build writes to `build/dist/SmartCoolingController` and then runs
`scripts/build/verify_frozen.py`, which launches the real executable in
head-less mode and checks `--version`, a full UI start/stop and the data tree.
Run the verifier on its own with:

```bash
python scripts/build/verify_frozen.py build/dist/SmartCoolingController
```

## Deliver a package

```bash
python scripts/package/release.py --skip-installer   # bundle + notes + checksums + manifest
python scripts/package/deliver.py                    # assemble the hand-off
```

`deliver.py` writes a self-contained `build/delivery/` directory holding the
runtime bundle as a zip, the full source as a zip, `INSTALL.md`,
`RELEASE_NOTES.md`, the build manifest and `checksums.sha256` covering every
artefact. Verify a delivery with `sha256sum --check checksums.sha256` before
shipping it.

## Tests

```bash
pytest -q
```

The suite includes `tests/ui/test_entrypoint.py`, which launches the real
process to guard the start-up path (Qt bootstrap → services → window →
graceful shutdown) — the layer unit tests cannot reach.

## Project layout

```
app/
  core/        config, logging (with redaction), enums, paths
  hardware/    LibreHardwareMonitor, WMI and simulated backends
  networking/  ESP32 protocol, discovery and transport
  persistence/ SQLite storage and migrations
  services/    telemetry, profiles, controller and orchestration
  ui/          shell, pages, widgets, theming and i18n
run.py         entry point: argument parsing, Qt bootstrap, lifecycle
scripts/       build, packaging and release helpers
installer/     PyInstaller spec and version resources
```

## License

Released under the MIT License — see [LICENSE.txt](LICENSE.txt).
