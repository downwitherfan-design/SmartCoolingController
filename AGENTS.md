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
  log tokens, keys or pairing codes directly.
