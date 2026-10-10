# V2.1 — Progress Log

Chronological, evidence-based log. Every entry records the actual command and
the actual result. No hours are claimed; timestamps are UTC.

---

## 2026-10-10 ~18:58 — Session start, environment

- Branch created: `git checkout -b v2.1/production-readiness` (from `master`
  @ `eea73fe`).
- `ruff check app run.py tests scripts` → `All checks passed!`
- `mypy app` → `Success: no issues found in 93 source files`
- `pytest` initially failed to start: `ImportError: libEGL.so.1`. Installed
  `libegl1 libgl1` via apt (needs `apt-get update` first). After that:
  `211 passed in 42.34s`.

## Phase 1 — V2.0.0 release audit (actual evidence)

- `git rev-list -n1 v2.0.0` → `4f5c6d3e74265095299087444fe24ff1fec468d7`.
- `git log v1.0.0..master` shows the V2 merge and feature commits; `master`
  head `eea73fe` differs from the tag by exactly the AGENTS.md docs commit.
- `gh release view v2.0.0` assets (with server-computed sha256 digests):

  | asset | bytes | sha256 (from GitHub) |
  | --- | --- | --- |
  | `checksums.sha256` | 546 | `4653fc90…` |
  | `installer-manifest.json` | 290 | `6600d020…` |
  | `SmartCoolingController-2.0.0-setup.exe` | 53707018 | `f6426bba…` |
  | `SmartCoolingController-2.0.0-source.zip` | 349022 | `54fdbd6c…` |
  | `SmartCoolingController-2.0.0-windows-x86_64.zip` | 76287643 | `0600eb82…` |

- Downloaded `checksums.sha256`, `installer-manifest.json` and the source zip
  into `/tmp/sc_audit`, then `sha256sum` the source zip:
  `54fdbd6c…` == the published value → PASS.
- `installer-manifest.json` → `version 2.0.0`, `git_sha 4f5c6d3`,
  `installer_sha256 f6426bba…` (matches the exe on the release page).
- Inspected the source zip with `zipfile`: 135 entries, includes `app/`,
  `docs/`, `tests/`, `installer/`; `app/__init__.py` shows `APP_VERSION = "2.0.0"`.

### DEFECT-1 (confirmed, high) — installer is not in `checksums.sha256`

`checksums.sha256` lists `windows-x86_64.zip`, `source.zip`, `INSTALL.md`,
`RELEASE_NOTES.md`, `manifest.json`, `checksums.txt` — but **not** the
`...-setup.exe` its own `installer-manifest.json` describes. `RELEASE_NOTES.md`
tells the user to verify `SmartCoolingController-2.0.0-setup.exe` against
`checksums.sha256`, which cannot work:

```
Get-FileHash .\SmartCoolingController-2.0.0-setup.exe -Algorithm SHA256
Compare the result with checksums.sha256 on this release.   # <- no such entry
```

Root cause: the CI job `Collect artefacts` copies the exe into `out/`, but
`deliver.py` builds `checksums.sha256` only from its *own* `build/delivery/`
directory, which never contains the installer. The two defence-in-depth
mechanisms (release-page server digests, and a cross-checked checksums file)
disagree by omission.

- [BLOCKED] Actually launching `setup.exe` / portable: this is a Linux
  container; Windows executables cannot be executed here. Asset *integrity*
  is verified; asset *execution* is not.

## Phase 0 docs created

- `docs/V2_1_ENGINEERING_PLAN.md`, `docs/V2_1_PROGRESS.md`,
  `docs/V2_1_DECISIONS.md`.

## Next steps

1. Commit the docs + branch scaffold.
2. Phase 2: write failing regression tests for the targeted connection
   scenarios, then fix real defects.
3. Phase 3: extend the mock harness.
4. Phase 4: diagram truth tests (esp. "connected but telemetry unavailable").
