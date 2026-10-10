## SmartCooling Controller

A Windows desktop app for a DIY ESP32 laptop-cooling pad: live temperature and
fan-RPM telemetry, automatic fan curves, a wiring schematic and diagnostics.
Built with PySide6/Qt, pyqtgraph, psutil and websockets.

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

### Verify your download (optional)

```powershell
Get-FileHash .\SmartCoolingController-1.0.0-setup.exe -Algorithm SHA256
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
