# Invoice capture and unit-of-measure conversion for point-of-sale stock entry


The system reads photographed supplier invoices and matches each line to a Pilot POS stock item. It converts the quantity into that item's base unit, or refuses and says why. It flags likely keying errors, and a person reviews every line before exporting rows to key into Pilot. Nothing is written to Pilot automatically.

## Environments

| Environment | Python | Used for |
|---|---|---|
| `.venv` | 3.12 | Everything except TrOCR. PaddleOCR (paddlepaddle has no Python 3.14 wheel) and Tesseract. oneDNN is switched off because paddlepaddle 3.3.1 crashes with it on Windows. |
| `.venv-hwr` | 3.12 | TrOCR only: CPU PyTorch and Transformers, kept apart so they cannot disturb PaddleOCR. |

Tesseract is expected at `C:\Program Files\Tesseract-OCR\tesseract.exe`; set `TESSERACT` to override.

## Data

The data is confidential: negotiated supplier prices and a trading business's stock records. It never leaves this machine and is not committed anywhere.

| Folder | Holds |
|---|---|
| `data/pages` | One image per page of the August receiving binder (`ingest.extract`). |
| `data/ocr_cache` | Recognition results per page, keyed by image hash. |
| `data/pilot` | Pilot exports. **Stock Valuation** for Eastgate and Emperors (base units live in the item names). **Purchasing Report** for Eastgate, 1 June to 13 September 2026 (what managers keyed, with dates and invoice totals). **Purchases by Supplier**, August. |
| `data/out` | Pipeline outputs: `manifest.json`, `invoices.json`, `normalised.json`, `flags.json`, `review/`, `results/`. |
| `data/gt` | `pilot_match.json` (scans tied to Pilot entries) and `handwriting.json` (the handwriting trial's answer key). |
| `data/hwr` | Handwriting regions, their crops, and TrOCR's readings. |
| `data/app` | The laptop server's own data: database, phone photographs, reports (see "Laptop server and portal"). |

## Running

```bash
.venv/Scripts/python pipeline.py            # ingest, parse, normalise, check, tie to Pilot, report
.venv/Scripts/python pipeline.py --ocr      # first extract pages and recognise any not yet cached (slow)
.venv/Scripts/python pipeline.py --hwr      # include the handwriting trial
.venv/Scripts/python -m app.serve           # laptop server: portal, phone API and background readers (below)
.venv/Scripts/python -m app.server          # the web pages alone, no readers: portal at /, binder review at /binder
.venv/Scripts/python -m pytest              # tests
```

Every figure for the results chapter is in `data/out/results/results.md`, with the underlying numbers in `metrics.json` and `hwr_trial.json`.

## Laptop server and portal

The always-on store laptop receives photographs from the Android app (`../android`) over the office Wi-Fi, reads them in the background, and serves the office portal. The phone API is `docs/phone-api.md`, the contract both sides implement.

```bash
.venv/Scripts/python -m app.serve                        # this laptop only: http://127.0.0.1:5000
INVOICE_HOST=0.0.0.0 .venv/Scripts/python -m app.serve   # reachable from the phones on the office Wi-Fi
.venv/Scripts/python -m app.serve --workers 1            # one reader on a slower laptop
```

In PowerShell, set the variable first: `$env:INVOICE_HOST = "0.0.0.0"; .venv\Scripts\python -m app.serve`. `python -m app.server` still starts the web pages alone; uploaded photographs then wait in the queue until `app.serve` runs.

| Variable | Default | Meaning |
|---|---|---|
| `INVOICE_HOST` | `127.0.0.1` | Listen address. `0.0.0.0` lets the phones on the office Wi-Fi reach it. |
| `INVOICE_PORT` | `5000` | Port. |
| `INVOICE_WORKERS` | `2` | Reader processes. Each loads PaddleOCR once and keeps it. |
| `INVOICE_PUBLIC_URL` | detected LAN address | The server address put in the pairing QR code, when the detected one is wrong (e.g. `http://192.168.1.20:5000`). |
| `INVOICE_APP_DATA` | `data/app` | Where the server keeps everything it writes. |
| `OCR_ENGINE` | `paddle` | `tesseract` for the comparison engine. |

**Where the data lives.** Everything the server writes is under `data/app`. The rest of `data/` is only read: each shop's key comes from its latest Pilot exports in `data/pilot`.
- **`invoices.db`:** SQLite (WAL, versioned schema). It holds shops, devices (token hashes only), pair codes, batches, photographs, reading jobs, invoices and lines (printed values kept apart from the manager's), learned aliases and reports. Its append-only `events` table is evaluation evidence: uploads, receipts, reading attempts, tab changes, every manager edit with before and after, deletions, submits.
- **`photos/<batch>/`:** each photograph exactly as received, replaced ones included.
- **`ocr_cache/`:** the words recognised on each photograph.
- **`reports/<batch>/`:** input reports (`report_v<n>.pdf` to print, `.csv`), built from a frozen snapshot, with their SHA-256 in the database.
- **`thumbs/`, `tmp/`:** downscaled copies, and uploads still arriving.
- **`shops.json` (optional):** replaces the built-in shops, Eastgate (Stock Valuation and Purchasing Report) and Emperors (Stock Valuation only).

**Pairing a phone.** Open `/pair` on the laptop, choose the shop, and scan the QR code with the app, or type the six-character code and the address shown beside it. A code works once, for 10 minutes. The phone receives a long random token, of which the server keeps only a hash. A lost phone is revoked on `/devices`.

**Reading.** Every photograph becomes a job in the database, and reading starts with the first upload.
- **Claiming:** a reader claims a job with a 90-second lease and renews it while reading. If the reader dies, the lease runs out and the job goes back in the queue.
- **After each page:** the batch's pages are regrouped into invoices by supplier and invoice number, then parsed and normalised with the shop's key (stock export, purchase history where the shop has one, learned aliases). Prices and duplicates are checked, and each invoice gets a tab: Duplicates, Retake, Corrections (shown red) or Good, with its reasons.
- **Failures:** a photograph that cannot be read is tried three times, after pauses of 5 and 30 seconds; then its invoice goes to Retake.
- **Start and stop:** on start the server puts back jobs a crashed run was holding and removes half-written files. Ctrl+C stops the web server and the readers, and a page being read goes back in the queue.

**Portal pages.** `/` lists batches. `/batches/<id>` shows invoices by tab with photographs and lines, and the input report once submitted. `/unresolved` lists Retake and Duplicates invoices across batches. `/key` shows each shop's stock items and learned aliases, and removes a bad alias. `/pair` pairs a phone and `/devices` revokes one. The binder review pages moved to `/binder`; `/adjudicate` is unchanged.

**Security: office network only.**
- **No logins yet.** Anyone who can reach the laptop can use every portal page: see invoices, pair or revoke a phone, remove an alias. Only the phone API needs a device token. Owner, admin and manager logins are future work.
- **Plain HTTP on the LAN.** Photographs, prices and device tokens cross the Wi-Fi unencrypted. Use only the dedicated, password-protected office network, never a guest or public one.
- **Future work:** a cloud server with HTTPS and logins.

## Review app (phone first)

The review screens follow a design handoff.
- **Design:** they use the Industry design system (`app/static/industry.css`, `app/static/app.css`), one column, 48px touch targets.
- **Photograph:** it opens as a full-screen sheet with native pinch-zoom.
- **Camera:** it opens straight from the invoice list.
- **Offline:** no web fonts, scripts or icons are fetched, so the app works with the network unplugged.

**The refusal guard.** Choosing Accept on a line the normaliser refused, or the checks flagged, reveals an "I checked the photograph" tick.
- **Before the tick:** the line posts as a correction, so one stray tap cannot send a refused line to the export.
- **Recording:** each saved line records whether the tick was given (`photo_checked` in `data/out/review/corrections.jsonl`).

**The adjudication screens** (`/adjudicate`) were added in the same style. They are not part of the handoff.

**Using it from a phone.** The binder review pages are now under `/binder`. The app listens on this machine only. To open it from a phone on the same trusted network, start it with the variable set, then browse to `http://<laptop-ip>:5000/binder`:

```bash
INVOICE_HOST=0.0.0.0 .venv/Scripts/python -m app.server
```

Anyone who can reach it can see the invoice photographs.

**Open design decision:** the Industry system's Barlow and Barlow Condensed faces are not bundled, so headings fall back to the system font. To use them, add the two `.woff2` files under `app/static/fonts/` with `@font-face` rules.

## Modules

| Module | Does |
|---|---|
| `ingest/` | Extracts page images, identifies supplier, document number and date, and groups pages into invoices on positive evidence only. |
| `ocr/` | PaddleOCR and Tesseract behind one interface returning word boxes, with a disk cache and a parallel runner. |
| `parser/` | One table engine. Supplier layouts are data (`layouts.py`), not code. It reconciles quantity Ãƒ— price against the line total, and records rather than corrects invoices that do not add up. |
| `normaliser/` | Reads base units out of Pilot item names, scores stock-item matches (alias, supplier history, text), and converts or refuses with a category and a reason. |
| `checks/` | Duplicate invoices, and price anomalies against each item's median, classified (unit error, case keyed as units, transposed, placeholder item). |
| `tools/` | Loads Pilot exports, and ties scanned invoices to Pilot entries by reference, falling back to total and date. |
| `hwr/` | Handwriting trial: finds pen ink by colour, crops it, reads it with PaddleOCR and TrOCR, and scores both. |
| `eval/` | The results report, and a replay of August showing whether matching improves as invoices are reviewed. |
| `app/` | The laptop server: phone API (`api`), reading queue (`worker`, `reading`), input reports (`report`) and portal (`portal`), started by `serve`. Also the binder review pages (`/binder`: accept, correct or skip each line, export CSV) and `/adjudicate`. Every change is recorded. |

## Known limitations

- **Overlapping photos:** binder pages were photographed overlapping. A strip of the sheet behind shows at the top of a photo, and parts of some tables are hidden, especially on Vassco invoices.
- **Pilot is not ground truth:** it is what managers keyed, and is itself fallible. Agreement with it is reported as agreement, and disagreements are classified by what they look like, not scored as system errors.
- **Handwriting detection:** colour catches coloured print and stamps as well as pen, so detection precision is reported alongside reading accuracy.
- **Alias learning:** learning supplier wording from reviewed invoices raises agreement, but also raises the rate of wrong picks slightly. Both are reported.

## Library trials

Two open-source libraries were tried against the real binder in a separate environment (`.venv-trial`). Neither was adopted. Scripts are in `eval/trials/`; results are in `data/trials/` and in the results report.

**page-dewarp** (MIT, <https://github.com/lmmx/page-dewarp>) flattens a curved page photograph before recognition.
- **With Tesseract:** it rescued two near-unreadable pages (p080: 5 words recognised Ã¢â€ â€™ 159). It lowered confidence on three of the eight worst pages by 17Ã¢â‚¬â€œ21 points and on all three control pages by 2Ã¢â‚¬â€œ8 points, and failed on one page.
- **With PaddleOCR, the engine the pipeline uses:** every flattened page read worse. p080 went from 130 words at 77.9 confidence to 92 words at 59.5; p151 from 37 at 70.8 to 28 at 54.0; p101 from 34 at 52.5 to 35 at 50.7.
- **Why:** flattening also thresholds the image to black and white, which discards detail that PaddleOCR uses.

**img2table** (MIT, <https://github.com/xavctn/img2table>) detects table structure from ruled lines and text alignment.
- **Result:** on 12 invoices whose parsed line count differs from Pilot's, its largest table was never closer to Pilot's line count than our parser was.
- **Why:** most of these tables have no ruled borders and the photographs overlap, so it found only fragments of 2Ã¢â‚¬â€œ24 rows.

Also reviewed and cited as related work rather than used:
- **invoice2data:** regex templates over PDF text, the closest existing approach to layouts-as-data.
- **splink** and **recordlinkage:** probabilistic record linkage.
- **docTR:** a deep-learning OCR engine.
- **Table Transformer:** deep-learning table detection.
- **quantulum3** and **ingredient-parser:** extracting quantities and units from text.

## Windows launcher and a clean Eastgate trial

Install Python 3.12 with Tcl/Tk and the Python launcher. Copy the source folders to the independent laptop; create its environment there rather than copying `.venv`. Run `setup.ps1` once. It installs the core requirements and the pinned PaddleOCR/Paddle versions in `requirements-ocr.txt`. First OCR startup needs internet to download the named models; later startups use the model cache. A successful dependency install is not a completed invoice trial.

Double-click `Launch Invoice Server.cmd` or run `launch.ps1`. The desktop window starts the portal/API and readers together, opens the browser after the readers load, and provides Stop, Start, Open portal and Open log. Close the window to stop its server and readers. Stop then Start restarts them. Logs are under the selected app-data folder in `logs/server.log`. If the chosen port is occupied, choose another; the launcher does not stop the other program.

The server listens on the laptop's local network. Pair from `/pair` using the laptop's Wi-Fi address shown there; the laptop browser uses `127.0.0.1`. The phone and laptop must share the office network, and Windows must permit the server on that private network. This launcher does not change firewall rules. Do not close the laptop lid or allow it to sleep during processing.

An isolated case-study run can set `INVOICE_DATA` to a folder containing `pilot/`, and `INVOICE_APP_DATA` to a new app folder. Put `shops.json` in that app folder with only Eastgate and exact baseline export filenames. Keep the trial-week reference report outside `pilot/`. No previous database, device pairing or learned aliases should be copied into the baseline run.

`INVOICE_SUPPLIERS_FILE` optionally points to a JSON list of additional supplier identities (`key`, `pilot_name`, `aliases`, optional `goods`). Empty aliases mean manual supplier identification; they do not teach OCR to recognise the supplier. Duplicate identities and empty aliases are rejected. Set this variable before starting the launcher so the server and readers load the same registry.

A shop's optional `history_days` limits Pilot prices to that many days ending at the latest date in its selected purchase export. Supplier/product matching still uses the full export. The Eastgate trial preparation uses 90 days. Broad supplier history is not permission to infer missing pack sizes.

Stock units retain their actual export scale: 10 kg for an item held in GRAM exports 10,000 g. Fixed-size stock units reject incompatible invoice dimensions, convert explicit volume/count columns and refuse unknown container quantities. Cache publication is atomic; incomplete entries are re-read by the engine. Terminal stale jobs rebuild the batch into Retake.
