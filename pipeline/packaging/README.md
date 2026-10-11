# Eastgate Windows installer

The private trial installer is `output/eastgate-installer/Eastgate-Invoice-Server-Setup.exe` in the workspace. It includes confidential Eastgate stock and purchasing baseline files; share it only with authorised project operators.

## Install and run

1. Copy the setup EXE to the independent 64-bit Windows laptop and connect it to the internet.
2. Open the EXE and click **Install**. Keep the setup window open while Python, dependencies and OCR models are prepared. Setup checks the Microsoft Visual C++ OpenMP runtime required by Paddle. If missing, it downloads the signed Microsoft x64 runtime installer; approve the Windows permission prompt. If setup requests a restart, it is a successful prerequisite installation rather than an application failure: restart Windows and rerun this installer. Native runtime diagnostics are saved in `native-runtime-check.stderr.log` and `visual-c-runtime.log`. The installer creates its directories and logs for a first installation. If a previous Python registration remains after deleting its files, setup attempts repair and checks that the interpreter exists before proceeding. Installation failures appear in the window and `%LOCALAPPDATA%\EastgateInvoiceServer\installation.log`. A separate `setup.log` is also saved in the temporary `EastgateSetup-<id>` folder shown in the setup window, even when application-directory setup fails. Detailed dependency output is retained in `dependencies.log`, `dependencies.stdout.log` and `dependencies.stderr.log` in that same folder. Setup upgrades pip in the new environment before installing application dependencies.
3. Click **Finish**, then open the **Eastgate Invoice Server** desktop shortcut. The launcher starts the portal and OCR readers and opens the browser after readiness checks.
4. Copy `%LOCALAPPDATA%\EastgateInvoiceServer\Samsung-App.apk` to the Samsung and install it. Put the phone and laptop on the same Wi-Fi, then use the portal's pairing page and the laptop's reachable LAN address. If Windows asks about network access, allow the server on the trusted private network.

No development folder or existing `.venv` needs to be copied. Internet is required during setup; the local workflow runs using the installed OCR models. The APK remains a separate Android installation.

## Data and updates

The installer seeds a fresh Eastgate baseline only when each seed file is absent. It excludes previous databases, learned aliases, trial-week purchasing reference records and the closing catalogue. Python environments live in short `e/<id>` folders beside versioned source to avoid excessive dependency path lengths. The launcher reads the active source/environment pair from `current-install.json`. Code versions are identified by payload hash. Checked environments are identified by requirements hash and reused after `pip check`; retrying a later OCR/model failure does not reinstall successful dependencies. App state lives in `%LOCALAPPDATA%\EastgateInvoiceServer\trial`, separately from versioned code. Reinstalling preserves existing state and therefore does not start a new independent trial. Back up that state before organising a separate fresh run.

Close the server launcher before updating. The active version is switched after dependency and OCR startup checks succeed. Old version folders are retained. This per-user setup creates desktop and Start menu shortcuts; it does not register an uninstaller in Windows Settings.

Retry setup directly after a failed attempt; manual folder deletion is unnecessary. Setup verifies application files against the bundled manifest, including missing, altered or unexpected source files. Damaged folders are retained separately and a clean folder is extracted. Incomplete or unhealthy Python environments are also retained separately; dependencies are installed into an unused short folder rather than overlaid onto them. Verified replacement folders can be reused on subsequent retries. Normal Python bytecode caches for bundled source are allowed. The private Python runtime is checked for the launcher and environment-building modules and repaired if that check fails. Existing invoice databases, learned configuration and the previous active source/environment record are preserved when a later installation check fails. Retained folders use disk space; this recovery mechanism does not automatically delete them.

## Rebuild

From the workspace, on Windows with the .NET Framework 64-bit C# compiler:

```powershell
invoice-pipeline\.venv\Scripts\python.exe invoice-pipeline\packaging\build_installer.py --output output/eastgate-installer
```

The build uses explicit source folders and four baseline files. `build-manifest.json` records payload and EXE hashes. The installer verifies extracted payload hashes before seeding data. Source formatting uses four-space indentation, UTF-8, LF for Python/Markdown and CRLF for PowerShell.

## Verification limits

On 11 October 2026 the operator reported that the PC application works through v7 (assembly 1.0.6). This is an operator report, not a complete independent deployment certification or an invoice-accuracy result. Field review has identified unresolved parsing and quantity issues; invoice outputs still require careful checking.

The automated installer tests use controlled downloads and native stage outcomes. They cover restart-required handling, first installation, missing runtime files, repeated attempts, damaged source and environments, and preserving existing state. They do not replace physical Windows, GUI or phone testing. Preparation and payload checks preserve test data, while retained old folders consume disk space. No memory or throughput improvement has been measured.

The current source includes PDF upload and a scanned-page quality profile. Physical trial results remain separate from unit tests and controlled installer replays. The private installer build requires the external baseline files named by the build script; these are not distributed in this repository.
