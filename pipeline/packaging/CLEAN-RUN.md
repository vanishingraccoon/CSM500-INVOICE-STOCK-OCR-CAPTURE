# Clean Run — News Cafe Eastgate

Use `Clean Run.exe` on the laptop where Eastgate Invoice Server is already installed, signed into the same Windows account used for installation. The tool is separate from the installer and needs no Python installation or download.

1. Close the phone capture app and the entire Eastgate Invoice Server window. Clicking Stop alone leaves the launcher open.
2. Copy `Clean Run.exe` from the USB to the laptop and double-click it.
3. Click **Start clean run**. Keep the server closed until the tool says **Clean run ready**.
4. Click **Open Invoice Server**. Pair the Samsung with the new QR code and start a new batch.

Existing invoices, photos, reports, OCR cache, database, learned aliases and device pairings are moved together into a dated backup. A fresh app folder retains only `shops.json`; the server creates a new database when launched. Installed program files, Python, OCR models and `trial/data` (including Pilot exports and additional supplier configuration) stay in place. The baseline key is retained; learned matches from earlier trials are reset.

Backups are in `%LOCALAPPDATA%\EastgateInvoiceServer\clean-run-backups\<run-id>\app`. Keep these until the old trial has been evaluated. They contain private trial data. Nothing is automatically deleted, including backups from previous clean runs. The summary log is `%LOCALAPPDATA%\EastgateInvoiceServer\clean-run.log`.

This laptop tool cannot clear the phone's local upload queue. Do not resume old captures into a fresh trial. For a completely fresh phone state, first preserve any pending captures you need, then use Android Settings → Apps → Invoice Capture → Storage → Clear data, reopen and pair again. Clearing phone app data removes its pending captures and pairing. Clearing cache alone does not reset those records.

If the tool reports that the server is open, close its window and retry. If the installation is missing, run the server installer first. A failed reset keeps the original trial or its intact backup; read the displayed path before starting anything. Do not manually delete the Eastgate installation folder.

## Build and verification

Run `python packaging/build_clean_run.py` from `pipeline`. The build uses the Windows .NET Framework compiler and produces `../output/clean-run/Clean Run.exe` with the PowerShell reset script embedded. The executable uses Windows Forms and Windows PowerShell; it targets the same 64-bit Windows laptop as the server installer.

`tests/test_clean_run.py` exercises temporary installation folders: archive preservation, unchanged Pilot/program/runtime/models, repeated resets, open launcher/database/WAL/SHM refusal, setup lock refusal, invalid installation records, missing installations and absent app-folder seeding. It never resets the real installation. The executable's `--extract-only <directory>` option extracts its embedded script for build verification without performing a reset.
