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

---

## Audit findings — later passes

Each item below was found by probing the running code (not by reading), fixed,
and pinned with a regression test.

### DEFECT-1 (fixed) — installer missing from `checksums.sha256`

`deliver.py` now discovers the built installer, copies it into the delivery
directory and appends its SHA-256 via `_installer_checksum_lines()`. The Windows
workflow fails the release if `checksums.sha256` omits the installer or lists the
wrong digest, so the documented verification can no longer silently break.
Tests: `tests/unit/test_packaging.py`.

### DEFECT-2 (fixed) — three connection states rendered as raw keys

`ConnectionState.DISCOVERING`, `RECONNECTING` and `STOPPING` had no `conn.*`
entry in either catalogue, so the UI showed `conn.discovering` etc. Added all
three in English and Persian, and added a catalogue-parity suite
(`tests/unit/test_i18n.py`) that fails if the two catalogues diverge or if any
enum state lacks a translation.

### DEFECT-3 (fixed) — `Translator` dropped RTL for string languages

`Translator` compared `self._language is Language.PERSIAN`. Callers pass the
plain strings `"en"`/`"fa"`; a `StrEnum` compares equal by value but not by
identity, so RTL layout and Persian digits silently did not apply. The
constructor and `set_language` now coerce through `_coerce_language`.

### DEFECT-4 (fixed, security) — structured and PEM secrets leaked

The redaction rule `key[:=]value` failed when a quote sat between the key and
the separator, so `"token": "…"`, `{"password": "…"}` and PEM private keys
reached log files and *public support bundles* verbatim. Added quoted-pair and
PEM rules, an expanded credential-name set, and made `redact()` apply the text
rules to string values. Tests: `tests/unit/test_logging_redaction.py`,
`tests/unit/test_support_bundle_redaction.py`.

### DEFECT-5 (fixed, safety) — a profile could cap a safety override

SAFE mode and the over-temperature response both routed through the profile
envelope, so a profile with `max_pwm=25` capped the emergency duty at 25% —
"safe mode" could command a quarter speed and a critical temperature could be
held at the profile ceiling. Safety overrides now bypass the profile envelope
and are limited only by the absolute `[0, 100]` range, after the ramp limiter.
Tests: `tests/unit/test_cooling_controller.py`.

### Still open

- Push the branch / open the PR — the token currently cannot create refs.
- Execute `setup.exe` on a real Windows host (not possible in this container).

