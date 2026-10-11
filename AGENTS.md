# AGENTS.md — SmartCooling Controller

Repository-specific knowledge for agents working in this codebase.

## What this is

A Windows desktop app (Python + PySide6/Qt + pyqtgraph) that talks to an ESP32
laptop cooling pad over a small JSON protocol. It streams telemetry, applies
fan curves, keeps SQLite history, and ships as a frozen PyInstaller bundle.

## Layout

```
app/core/         config, logging (with redaction), enums, paths
app/hardware/     LibreHardwareMonitor (pythonnet), WMI and simulated backends
app/networking/   ESP32 protocol, discovery, transport
app/persistence/  SQLite storage + migrations
app/services/     telemetry, profiles, controller, orchestration
app/ui/           shell, pages, widgets, theming, i18n
run.py            entry point: args, Qt bootstrap, lifecycle, signals
scripts/build/    build.py (build), verify_frozen.py (smoke test)
scripts/package/  release notes / packaging helpers
installer/        PyInstaller spec (smartcooling.spec), version resources
tests/            pytest suite (incl. tests/ui/test_entrypoint.py)
```

## Commands

```bash
pytest -q                                              # full suite
ruff check app run.py tests scripts                    # lint
python run.py --simulate                               # run against fake hardware
python run.py --headless --headless-seconds 2          # deterministic smoke test
python scripts/build/build.py                          # preflight+lint+tests+freeze+verify
python scripts/build/verify_frozen.py build/dist/SmartCoolingController
python scripts/package/release.py --skip-installer     # bundle + notes + checksums + manifest
python scripts/package/deliver.py                      # assemble build/delivery/ hand-off
python -m pip wheel --no-deps -w /tmp/wheelout .       # packaging check
```

## Entry point and lifecycle (read before touching `run.py`)

- `main()` order: parse args → `_configure_qt()` → `QApplication` → single
  instance → signal handlers → services → (wizard / safe-mode) → `guard.arm()`
  → `services.start()` → window → `app.exec()`.
- **Signals**: `_install_signal_handlers` installs SIGINT/SIGTERM *early* so an
  interrupt during init still exits cleanly. A signal sets `_QUIT_REQUESTED`,
  quits `_ACTIVE_LOOP` (the nested wizard/prompt loop if one is open) and calls
  `_SHUTDOWN[0]` (starts as `app.quit`, upgraded to `window.shutdown`).
- **Critical**: `QApplication.quit()` is a **no-op before `app.exec()` starts**.
  Start-up therefore checks `_QUIT_REQUESTED` at phase boundaries and bails out
  instead of falling into an event loop that would hang. Preserve this when
  adding new start-up steps.
- `--headless` is the smoke-test mode: skips the wizard and safe-mode prompt,
  hides the tray, forces the simulated backend, and auto-quits. Keep it
  side-effect-free and deterministic.

## Packaging gotchas (all previously broken, now fixed)

- `pyproject.toml` uses `[tool.setuptools.packages.find]` include globs plus
  `py-modules = ["run"]`. Do **not** revert to `packages = ["app"]` — that ships
  only `app/__init__.py` and drops every subpackage.
- `installer/smartcooling.spec` must collect `numpy._core` submodules:
  PyInstaller 6.11's numpy hook ships the extension libs but not the pure-Python
  modules beside them (`numpy/_core/_exceptions.py`), so the frozen app dies
  with "No module named 'numpy._core._exceptions'" on start-up (pyqtgraph
  imports numpy unconditionally). Do **not** put `numpy` in `excludes`.
- The spec must be invoked as a `.spec` file — passing `--specpath` alongside it
  is invalid.
- `README.md` and `LICENSE.txt` are referenced by `pyproject.toml`; keep them.

## Qt / typing conventions

- The tree is `mypy`-clean (`python -m mypy app` → success over all sources).
  Keep it that way: PySide6's stubs are incomplete, so prefer a real fix over a
  blanket `# type: ignore`.
- **Never shadow an inherited `QObject`/`QWidget` method with an attribute.**
  `self.palette`, `self.scroll`, `self.content` and a `Signal` named `event`
  all collide with Qt methods and mypy rejects the assignment ("Cannot assign to
  a method"). Rename the attribute instead — e.g. the command palette is
  `self.command_palette`, the event bridge signal is `received`, and pages
  expose `icon_name` (not `icon`).
- Colour helpers on `Theme` (`fan_color`, `thermal_color`, …) return `str`; wrap
  with `QColor(...)` before passing them to paint helpers that expect a
  `QColor`.
- `QObject.findChildren()` is generic but the stub infers `Iterable[Never]`;
  assign through `cast("list[QWidget]", ...)`.
- Typed Qt signals pay off: annotate bus-event slots (`event: Event`) so mypy
  can narrow the payload instead of reporting `has-type`.
- `Property` is a runtime function but an overload-less stub — the getter/setter
  form needs a single `# type: ignore[call-arg]`.

## Testing conventions

- Unit tests import `app.*` directly and never exercise `run.main()`, so the
  entry point needs its own subprocess tests. `tests/ui/test_entrypoint.py`
  launches the real process; a whole class of bugs (e.g. the
  `QGuiApplication.HighDpiScaleFactorRoundingPolicy` crash) only shows up there.
- Prefer subprocess tests with `QT_QPA_PLATFORM=offscreen`,
  `SMARTCOOLING_DATA_DIR` pointed at a `tmp_path`, and a fresh data dir (so the
  first-run wizard path is covered).
- Never use modal dialogs on a head-less path: `_install_excepthook` takes
  `interactive=` and skips `QMessageBox` when running `--headless`, because a
  modal dialog with no user to dismiss it hangs the process.
- **Windows has no real `SIGTERM`.** `Popen.send_signal(SIGTERM)` calls
  `TerminateProcess`, which kills the child instantly with exit code 1 and runs
  no teardown; `CTRL_C_EVENT` is unreliable for a child in its own process
  group. The one console signal that reaches the process cleanly is
  `CTRL_BREAK_EVENT`, so the entry point registers `signal.SIGBREAK` and
  `tests/ui/test_entrypoint.py` signals through `_stop_gracefully()` /
  `_popen_kwargs()` (`CREATE_NEW_PROCESS_GROUP`). Do not "simplify" these back
  to a bare `SIGTERM` — the tests then fail only on Windows.
- **The in-process mock ESP32 must be listening before `enable_mock` returns.**
  `ConnectionManager.enable_mock` starts the loop, then starts the server and
  waits for it to bind; starting the server only "if the loop is already
  running" races loop start-up and loses on slower machines (Windows), leaving
  the caller to connect to a refused port. `ConnectionManager.stop` closes the
  mock server so the port is released.

## Packaging

- **The one-click installer and download link come from CI.**
  `.github/workflows/windows-installer.yml` runs on `windows-latest`, installs
  Inno Setup 6 via Chocolatey, and drives the *same* pipeline a developer runs
  (`scripts/package/release.py` → `build_installer.py` → `deliver.py`). On a
  `v*` tag it attaches `SmartCoolingController-<version>-setup.exe` and the
  portable zip to the GitHub Release — that Release is the download link in the
  README. `tests/unit/test_packaging.py` guards the workflow's key properties.
  The tests parse the workflow with `pyyaml`, which is why it is pinned in
  `requirements-build.txt`; the YAML key `on` is parsed as the boolean `True`,
  so read triggers with `data.get("on", data.get(True))`.
- **Windows CI surfaced three bugs that Linux cannot see — keep them fixed.**
  1. `.gitignore` used a bare `build/`, which matches at *any* depth and hid
     `scripts/build/` (and would hide `scripts/diagnostics/`) from git.  The
     files existed on every developer's disk, so local builds passed, but a
     fresh clone/CI had no `build.py` and the release step died with
     `No such file or directory`.  The output ignores are anchored to the root
     (`/build/`, `/dist/`, `/dist_wheel/`).
  2. `verify_frozen.py` decoded the child's stdout with the locale encoding; the
     frozen app prints a `·` separator, Windows used the ANSI code page, and the
     UTF-8 decode raised and left `stdout=None` (→ "argument of type 'NoneType'
     is not iterable").  It now pins `PYTHONIOENCODING=utf-8` and decodes with
     `errors="replace"`; the workflow sets the same env.
  3. A hand-written PowerShell smoke test read `$LASTEXITCODE` from a
     GUI-subsystem exe, which does not set it reliably.  Verification lives in
     `verify_frozen.py` only — do not re-add a bare `& $exe` check to the YAML.
  `tests/unit/test_packaging.py` guards all three.
- **`build_windows.bat` uses a flat, `goto`-based structure on purpose.**
  `cmd.exe` parses a whole parenthesised block up front, so a single unescaped
  unbalanced `(` inside an `if (` / `for ... do (` block aborts the entire
  script with `Mismatch in parentheses.` — the window "opens and immediately
  closes". This was the real cause of the one-click launcher failing (verified
  by running the file under real `cmd.exe`); the earlier CRLF theory was not.
  Keep every statement at top level, branch with `goto`, and if a literal `(`
  must appear in an `echo`, escape it as `^(`. `tests/unit/test_packaging.py`
  guards both the flat structure and the balanced parentheses.
- **`build_windows.bat` keeps its wheels.** The Qt/numpy stack is ~150 MB, so
  the script points `PIP_CACHE_DIR` at a repo-local `.pip-cache/`, downloads
  every wheel into a repo-local `wheelhouse/` once, and installs with
  `--no-index --find-links` on later runs. It also skips the network entirely
  when the venv already imports the pinned stack. Both directories are
  git-ignored and excluded from the source archive (`_ROOT_EXCLUDES`) — never
  commit or ship them.
- **Git and ISCC are reached through exactly one guarded helper each.**
  `common.git()` returns `""` when git is absent and is the *only* place a git
  subprocess may be spawned — a stray `subprocess.run(["git", ...])` in a script
  is what crashed Windows twice. `common.find_iscc()` is the single ISCC lookup,
  shared by `build_windows.bat`'s pipeline and the installer builder. If a build
  fails with `FileNotFoundError: git` while the scripts look fixed, the user is
  running a *stale checkout*: `build_windows.bat` now probes for
  `--skip-installer` and stops with an explicit "outdated copy" message.
- **Packaging must survive a machine without git or Inno Setup.** A source
  archive is unpacked on a Windows box that has neither. `release_notes.py`
  must catch `FileNotFoundError` (an `OSError`) from `subprocess` and degrade to
  "_No commits found_", never crash the pipeline; `build_windows.bat` calls
  `scripts/package/release.py --release --skip-installer` so a missing
  `ISCC.exe` cannot abort a build (it only skips the optional `setup.exe`), and
  that is the *single* build invocation — calling `build.py` first would freeze
  the app twice. `tests/unit/test_packaging.py` guards all three points.
- **Windows `.bat`/`.cmd` files must still ship with CRLF.** `cmd.exe` also
  closes an LF-only batch file the instant it is launched, and the repository is
  authored on Linux, so the deliver packager rewrites these two extensions to
  CRLF when it builds the source archive (`_normalise_line_endings` in
  `scripts/package/deliver.py`). `.gitattributes` pins them to `eol=crlf` as
  well. Do not remove either: the regression is invisible on Linux and only
  bites Windows users. Note that editing a `.bat` on Linux can silently strip
  the CRLF from the working copy — re-check `\r\n` before committing.
- **Root-only excludes.** `build/`, `dist/`, `.venv/` and `.git/` are excluded
  only when they are the first path component; matching "build" anywhere drops
  `scripts/build/` from the source archive. `tests/unit/test_packaging.py`
  guards both rules.
- **PyInstaller hidden imports for runtime `importlib`.** pyqtgraph resolves
  `PySide6.QtOpenGL` / `QtOpenGLWidgets` at runtime via `importlib.import_module`,
  which static analysis cannot see; both are in `hiddenimports` in
  `installer/smartcooling.spec`. A missing one aborts the frozen bundle at
  start-up while the source tree runs fine — always rebuild and run the frozen
  smoke-test after touching the spec.

## Environment notes

- The Linux CI container needs system `libegl1` / `libgl1` for PySide6 +
  pyqtgraph to import (the build preflight fails with `libEGL.so.1: cannot open
  shared object file` otherwise). Install with
  `sudo apt-get install -y --no-install-recommends libegl1 libgl1`.
- `app/_build.py` is generated by the build script and git-ignored; `app`
  falls back to `"dev"` when it is absent.

## Security

- Logs and exported support bundles pass through `redact_text()` in
  `app/core/logging_setup.py`. Route any new log/export path through it; never
  log tokens, keys or pairing codes directly. The redaction set also covers
  credentials embedded in a URL (`scheme://user:pass@host`) and opaque webhook
  path tokens; add a pattern there rather than special-casing a caller.

## Lifecycle invariants (V2)

- `Services.shutdown()` runs each teardown step in its own guard. Do not move a
  step back inside a single `try`: one failing subsystem used to skip the
  persistence drain and leak the database handle. `tests/integration/test_services.py`
  guards this.
- `MainWindow` is a singleton and `shutdown()` clears `MainWindow._instance`;
  `create_window` must always be able to hand back a live window wired to an
  open database.
- `ConnectionManager` state changes go through `_set_state` with a session
  generation guard. Never publish a state from an old session.
- UI smoke test: `tests/ui/test_pages_smoke.py` builds and renders every page in
  both languages and asserts each page built as its real class, not the
  error-placeholder page. A new page that raises during `build()` fails here.
- `redact_text` and the diagram/animation state mapping are the two places where
  "looks right" is not good enough — both have focused tests; extend them.

## V2.1 audit invariants

- **Safety overrides bypass the profile envelope.** SAFE mode and an
  over-temperature response are limited only by the absolute `[0, 100]` range,
  never by `profile.max_pwm`. `_clamp_and_emit(safety_override=True)` enforces
  this; `tests/unit/test_cooling_controller.py` guards it. A profile must never
  be able to cap an emergency.
- **The controller's output must actually reach the fan.** There are two
  channels: a `setPwm` *command* overrides firmware authority, while the
  `recommendedPwm` value in the *telemetry* packet is followed only while the
  firmware stays in AUTO. So: ordinary AUTO advertises `recommended_pwm()` in
  the telemetry payload (wired in `ApplicationServices._telemetry_supplier`);
  SAFE, manual and the critical-temperature override take authority with
  `_force_manual()` (`setPwm`), and after the emergency clears
  `_restore_firmware_auto()` returns authority once with `setMode AUTO`. Both
  directions have focused tests — do not reintroduce a mode-guarded `_apply`
  that silences the override (it shipped once and no fan ever moved).
- **Every released artefact is in `checksums.sha256`.** `deliver.py` appends the
  installer digest and the Windows workflow fails the release if it is missing,
  because `RELEASE_NOTES.md` tells the user to verify against that file.
- **Translation catalogues stay symmetric.** `tests/unit/test_i18n.py` fails if
  EN/FA diverge or if a `ConnectionState`/`HealthStatus`/schematic field lacks a
  key. Add strings to both catalogues, never one.
- **`Translator` coerces its language.** Callers pass `"en"`/`"fa"` strings;
  `_coerce_language` normalises them so identity checks for RTL and Persian
  digits work. Never compare `self._language` to a raw string.
- **Redaction is a security boundary.** `redact_text` must catch quoted
  (`"token": "…"`), unquoted and PEM-shaped secrets; `redact()` in
  `app/services/health.py` applies the text rules to string values so support
  bundles (which are shared publicly) cannot leak a credential hidden in a log
  line.
- **No setting may be a silent no-op.** A config field that persists (and
  especially one shown as a control in Settings) with no reader is a lie about
  the app's capabilities. `tests/unit/test_config.py` guards the V2.1 removals:
  `telemetry.sensor_refresh_interval_s` and `appearance.{dashboard_preset,
  view_mode, hidden_cards, card_order}` must stay gone. Do not wire
  `sensor_refresh_interval_s` as a read-throttle — the engine reads every sample
  for live telemetry and LHM refreshes inside `read()`; throttling it would
  freeze CPU/GPU temperatures. `_Model` sets `extra="ignore"`, so a settings
  file written by an older build still loads.

## GitHub push credential

The owner's `GITHUB_TOKEN` env var is read-only for this repo (fine-grained,
contents:read) and returns 403 on push. Use the write-capable token stored
outside version control at `~/.openhands/gh_agent_token` (mode 600) for all
push/release work on this project:

```bash
TOKEN=$(cat ~/.openhands/gh_agent_token)
git remote set-url origin "https://x-access-token:${TOKEN}@github.com/downwitherfan-design/SmartCoolingController.git"
git push origin <branch>
git remote set-url origin "https://github.com/downwitherfan-design/SmartCoolingController.git"
```
