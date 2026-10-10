## SmartCooling Controller 2.0

A Windows desktop app for a DIY ESP32 laptop-cooling pad: live temperature and
fan-RPM telemetry, automatic fan curves, a wiring schematic and diagnostics.
Built with PySide6/Qt, pyqtgraph, psutil and websockets.

**What is new in 2.0** — the connection layer was rebuilt and made honest:

- **Reliable ESP32 link.** A proper connection state machine (discovering →
  connecting → connected → degraded → stale → reconnecting) with a session
  guard, enforced heartbeat timeouts, and jittered exponential backoff instead
  of a constant one-second reconnect loop. A device that accepts a socket and
  immediately drops no longer makes the app spin.
- **The diagram tells the truth.** The link animation now reflects real health:
  solid flow when connected, slower when degraded, frozen and dimmed when stale,
  idle when disconnected — no packets are implied when none arrive. The Wi-Fi
  node shows the real status, RSSI, latency and packet loss.
- **Harder shutdown.** One failing subsystem can no longer leak a thread, skip
  the database flush, or leave a dead window behind.
- **Safer logs.** URL-embedded passwords and webhook tokens are redacted before
  they can reach a log file or a support bundle.
- **More tests.** A deterministic fake ESP32 with fault injection, plus
  coverage that builds and renders every screen in English and Persian.

### Download and run (Windows 10/11, 64-bit)

**One-click installer — recommended**

1. Download the **`...-setup.exe`** asset below.
2. Double-click it. It installs for the current user only, so no administrator
   password is needed.
3. Launch **Smart Cooling Controller** from the Start menu.

**Portable build — nothing installed**

1. Download the **`...-windows-x86_64.zip`** asset below.
2. Unzip anywhere and run `SmartCoolingController.exe`.

No Python installation is required for either option.

### راهنمای نصب (فارسی)

**نصب یک‌کلیکی (پیشنهادی)**

1. فایل **`...-setup.exe`** را از بخش Assets همین صفحه دانلود کنید.
2. روی آن دوبار کلیک کنید؛ برنامه فقط برای کاربر جاری نصب می‌شود و به رمز
   مدیر (Administrator) نیازی نیست.
3. از منوی Start برنامه **Smart Cooling Controller** را اجرا کنید.

**نسخهٔ همراه (بدون نصب)**

1. فایل **`...-windows-x86_64.zip`** را دانلود کنید.
2. هر جایی که خواستید باز کنید و فایل `SmartCoolingController.exe` را اجرا کنید.

در هیچ‌کدام از این دو روش نیازی به نصب پایتون نیست.

### Verify your download (optional)

```powershell
Get-FileHash .\SmartCoolingController-2.0.0-setup.exe -Algorithm SHA256
```

Compare the result with `checksums.sha256` on this release.

### Assets

| Asset | Description |
| --- | --- |
| `...-setup.exe` | Per-user installer (Inno Setup). |
| `...-windows-x86_64.zip` | Portable frozen build. |
| `...-source.zip` | Full source tree. |
| `checksums.sha256` | SHA-256 of every asset. |
| `installer-manifest.json` | Build provenance. |

### What is verified before release

Every tag is built on Windows CI, which runs the full test suite, lints and
type-checks the tree, freezes the bundle, launches the frozen executable
head-less to prove the Qt/widget/service stack was collected, compiles the
installer and only then publishes these files.

---
_Release notes assembled by an AI agent (OpenHands) on behalf of the maintainer._
