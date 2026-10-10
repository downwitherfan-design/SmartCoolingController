# SmartCooling Controller

Windows-side telemetry and control application for an intelligent laptop
cooling pad built around an ESP32 and a 4-wire PWM fan.

The desktop app talks to the pad over a small JSON protocol (WebSocket or
length-prefixed TCP), streams temperature/RPM/power telemetry into live charts,
applies thermal profiles, and keeps a local history for later export.

## Features

- **Live telemetry** — CPU/GPU/system temperatures, fan RPM, PWM duty and power
  draw, sampled on a configurable interval and rendered with `pyqtgraph`.
- **Thermal profiles** — built-in `silent` / `balanced` / `performance` /
  `turbo` curves plus custom fan curves, with hysteresis and ramp limiting so
  the fan never hunts.
- **Reliable ESP32 link** — automatic discovery, a connection state machine with
  enforced heartbeat timeouts and jittered exponential backoff, and a versioned
  protocol so firmware and app can evolve independently.
- **Truthful diagnostics** — the wiring diagram, link animation and Wi-Fi node
  are driven by real connection health (state, RSSI, latency, packet loss), not
  by a fabricated "online".
- **Safety first** — a crash guard that clears its marker on graceful exit,
  safe-mode start-up, and hardware back-off when a sensor goes stale.
- **Privacy by default** — logs and exported support bundles pass through a
  redaction filter, so tokens and keys never leave the machine.
- **First-run wizard** — language, theme, data location and hardware backend,
  all reconfigurable later.

## Download & install (Windows)

The one-click installer and the portable build are produced on every tag by the
[Windows installer workflow](.github/workflows/windows-installer.yml) and
attached to this repository's **Releases** page. The latest release lives at:

- `https://github.com/downwitherfan-design/SmartCoolingController/releases/latest`

| File | What it is | Direct link |
| --- | --- | --- |
| `SmartCoolingController-2.0.0-setup.exe` | The **installer**. Download, double-click, done. No Python, no administrator rights (per-user install). | `.../releases/latest/download/SmartCoolingController-2.0.0-setup.exe` |
| `SmartCoolingController-2.0.0-windows-x86_64.zip` | The **portable** build. Unzip and run `SmartCoolingController.exe`; nothing is installed. | `.../releases/latest/download/SmartCoolingController-2.0.0-windows-x86_64.zip` |
| `SmartCoolingController-2.0.0-source.zip` | Full source, buildable with the pinned toolchain. | `.../releases/latest/download/SmartCoolingController-2.0.0-source.zip` |

Verify a download before running it with
`Get-FileHash <file> -Algorithm SHA256` and compare against `checksums.sha256`
on the release page.

## Requirements

- Windows 10/11 (the app also runs head-less on Linux for CI and packaging).
- Python 3.11–3.14.
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

On Windows, double-click **`build_windows.bat`** to do all of the above in one
step. It requires 64-bit Python 3.11–3.14. The frozen bundle is
platform-specific — PyInstaller cannot cross-compile, so build on the platform
you intend to ship.

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
