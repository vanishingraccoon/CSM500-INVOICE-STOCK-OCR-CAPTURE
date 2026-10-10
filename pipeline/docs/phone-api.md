# Phone ⇄ laptop server contract (v1)

The single source of truth for the Android app (`../invoice-app-android`) and the laptop server/portal
(`invoice-pipeline/app`). Both sides implement exactly this. Change it here first, then in both.

Settled with the user on 2026-09-15 (see the design summary in the session): native Android app, Google
ML Kit Document Scanner (basic mode, no image cleaning, one scanner session per batch), photos read in a
background queue on the always-on store laptop over the dedicated office Wi-Fi, SQLite on the laptop, tabs
Good / Corrections / Retake / Duplicates, one key per shop, PDF + CSV input report per submitted batch.
Logins (owner/admin/manager) are future work; devices pair by QR code instead.

## Conventions

- Base URL comes from pairing, e.g. `http://192.168.1.20:5000`. All API paths start with `/api/v1`.
- JSON bodies are UTF-8, `Content-Type: application/json`. Times are ISO 8601 local time strings.
- Every call except `POST /api/v1/pair` sends `Authorization: Bearer <device token>`. Missing/invalid token:
  `401 {"error": "unauthorised"}`. A revoked device: `403 {"error": "device_revoked"}`.
- Errors are `{"error": "<code>", "message": "<plain words>"}` with a 4xx/5xx status.
- Money is sent as numbers with two decimals (rands); quantities as numbers. Server stores money as integer cents.
- IDs are strings. Client keys (`client_batch_key`, `client_photo_key`) are UUIDs generated on the phone.

## 1. Pairing

The portal page `/pair` shows a QR code encoding this JSON (and the same values as text):

```json
{"v": 1, "server": "http://192.168.1.20:5000", "pair_code": "K7Q2M9", "shop": "eastgate"}
```

- `pair_code` is 6 characters from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, valid 10 minutes, single use.

`POST /api/v1/pair` `{"pair_code": "K7Q2M9", "device_name": "Galaxy A06 (store 1)"}` →
`201 {"device_id": "dev_…", "token": "<48+ random chars>", "shop": "eastgate", "shop_name": "News Cafe Eastgate"}`

- Wrong/expired/used code: `400 {"error": "pair_code_invalid"}`. The server stores only a hash of the token.

`GET /api/v1/ping` → `200 {"ok": true, "server_time": "...", "shop": "eastgate", "queue": {"waiting": 3, "reading": 2}}`

## 2. Batches and uploads

A batch is one scanner session: all the invoices the manager photographed in one go.

`POST /api/v1/batches` `{"client_batch_key": "<uuid>", "page_count": 14}` →
`201 {"batch_id": "b_…", "status": "uploading", "created_at": "..."}`; same `client_batch_key` from the same
device again → `200` with the same body (retry-safe).

`PUT /api/v1/batches/{batch_id}/photos/{client_photo_key}` — the raw JPEG as the body.

Headers:
- `Content-Type: image/jpeg`
- `X-Photo-Ordinal: 3` (1-based position in the scanner result; page order is this, never upload order)
- `X-Content-SHA256: <hex sha256 of the body>`
- `X-Quality: {"sharpness": 142.3, "mean_luma": 171.0, "contrast_spread": 118, "glare_fraction": 0.004, "width": 2250, "height": 3000}` (phone's own checks, see §6; optional)
- `X-Replaces-Photo: p_…` (only for a retake, see §5)

Server behaviour, in order: stream to a temp file while hashing → reject if the hash differs from the header
(`422 {"error": "hash_mismatch"}`) → validate it is a single-frame JPEG/PNG under 25 MB and 12,000 × 12,000
pixels (`415 {"error": "unsupported_image"}`) → fsync, atomic rename into storage → in ONE SQLite transaction
insert the photo row and its queued job → respond. Only then is the photo safe.

Responses:
- `201` new, `200` exact retry (same key, same hash) — both with the **receipt**:
  `{"photo_id": "p_…", "batch_id": "b_…", "client_photo_key": "<uuid>", "sha256": "<hex>", "bytes": 812345, "state": "stored"}`
- `409 {"error": "key_reused"}` same key, different content.
- `404` unknown batch; `423 {"error": "batch_submitted"}` batch already submitted (retakes are the exception, §5).

**The phone deletes its copy only after a 200/201 receipt whose `batch_id`, `client_photo_key`, `sha256` and
`bytes` all match the local file.** Anything else: keep it and retry with backoff.

`POST /api/v1/batches/{batch_id}/complete` → `200 {"status": "processing"}`: every photo of the session is
uploaded. Reading already started with the first photo.

## 3. Reading the results

`GET /api/v1/batches` → `200 {"batches": [ {batch summary}, … ]}` newest first, this shop only.

`GET /api/v1/batches/{batch_id}` →

```json
{
  "batch_id": "b_…", "status": "uploading|processing|ready|submitted",
  "created_at": "...", "submitted_at": null,
  "progress": {"photos": 14, "waiting": 3, "reading": 2, "read": 9, "failed": 0},
  "counts": {"good": 4, "corrections": 2, "retake": 1, "duplicates": 1},
  "invoices": [ {invoice}, … ]
}
```

`invoice`:

```json
{
  "invoice_id": "i_…", "tab": "good|corrections|retake|duplicates|deleted",
  "tab_reasons": ["line 3: no stock item matched", "does not add up"],
  "supplier": "vassco", "supplier_name": "Vassco", "document_no": "1844378", "date": "2026-08-31",
  "pages": [{"photo_id": "p_…", "ordinal": 3, "page_no": 1, "state": "read", "quality": {"ok": true, "problems": []}}],
  "stated_total_excl": 3000.00, "stated_vat": 450.00, "stated_total_incl": 3450.00,
  "lines_sum_excl": 3000.00, "adds_up": true,
  "duplicate_of": null,
  "lines": [ {line}, … ],
  "updated_at": "..."
}
```

`line`:

```json
{
  "index": 0, "star": false, "entered_by": "system|manager",
  "printed": {"description": "Honey 5L", "quantity": 2, "unit": null, "pack": "5L", "unit_price": 100.00, "line_total": 200.00},
  "item": {"code": "1189", "name": "honey (LITRE)", "base_unit": "l"},
  "base_quantity": 10.0, "refusal": null, "reason": "matched 'honey (LITRE)' by history (score 106)",
  "flags": [{"kind": "price_outlier", "reason": "plain words"}],
  "candidates": [{"code": "1189", "name": "honey (LITRE)", "score": 106.0}]
}
```

- `star` is true when the line needs the manager: refused, flagged, or no quantity. Starred lines are the only
  lines the phone asks the manager to fix; the side-panel search can still change any line.
- `GET /api/v1/photos/{photo_id}/image` → the stored JPEG (`?w=1080` for a downscaled copy).
- `GET /api/v1/invoices/{invoice_id}` → `200 {invoice}` — one invoice on its own, in any batch of this
  shop, in the same shape as inside a batch. For the invoice a duplicate repeats, which may sit in a batch
  the phone has not loaded. `404` when it is unknown or belongs to another shop; `409 invoice_regrouped`
  when pages moved and the id no longer exists, which means: fetch the batch again.
- The phone polls `GET /api/v1/batches/{id}` every 3 s while `status` is `uploading` or `processing`, every
  30 s otherwise. `ETag`/`If-None-Match` supported (`304`).

## 4. Tabs (server decides; reasons always listed)

Evaluated per invoice, first match wins:

1. **duplicates** — a photo with the same sha256 as another photo in any batch; or same supplier and the same
   normalised invoice number (`refs.ref_keys`) as another invoice in this batch or any submitted batch.
   `duplicate_of` names the other invoice. The manager decides: delete, or keep (`not_duplicate`).
2. **retake** — the photo cannot be read: phone or server quality check failed (blur, too dark, faint, glare,
   cut off), fewer than 25 words recognised, recognition failed, or no line items found.
3. **corrections** (shown with a red background) — readable but needs the manager: any starred line, no
   supplier, no invoice number, no date, or the lines do not add up to the stated total.
4. **good** — everything read clearly: every line converted, adds up, nothing flagged.

Manager actions move invoices between tabs; the server recomputes and returns the new tab.

## 5. Manager actions

- `GET /api/v1/stock/search?q=honey&limit=20` → `{"items": [{"code", "name", "base_unit", "category"}]}` from
  this shop's key.
- `PUT /api/v1/invoices/{invoice_id}/lines/{index}` — the manager's entry wins; the line's flags clear:

  ```json
  {"item_code": "1189", "quantity": 2, "unit": null, "cost": 200.00}
  ```

  or free text when the item is not in the key: `{"free_text": "honey 5l tub", "quantity": 10, "cost": 200.00}`.
  With `item_code`, `quantity` is as printed on the invoice and the server converts it through the key (the
  response carries `base_quantity`, or a `refusal` if the key cannot convert, with the reason). With
  `free_text`, the quantity is taken as Pilot's base unit and the line prints under "not in key".
  → `200 {invoice}`
- `POST /api/v1/invoices/{invoice_id}/lines` — add a typed line (same body) → `201 {invoice}`.
- `DELETE /api/v1/invoices/{invoice_id}/lines/{index}` — remove a line the system invented → `200 {invoice}`.
- `PATCH /api/v1/invoices/{invoice_id}` `{"supplier": "vassco", "document_no": "1844378", "date": "2026-08-31"}` → `200 {invoice}`.
- `POST /api/v1/invoices/{invoice_id}/delete` — the manager deleted a duplicate → `200 {invoice}` (tab `deleted`;
  kept in the database for the record, never in a report).
- `POST /api/v1/invoices/{invoice_id}/not_duplicate` → `200 {invoice}`.
- **Retake**: the phone opens the scanner (page limit 1 per page to retake) and uploads with
  `X-Replaces-Photo: <photo_id>` to the invoice's batch. Allowed even after the batch is submitted, until the
  office files the paper on Sunday; a retake after submit joins the next report. The old photo stays stored,
  marked `replaced`.

## 6. Photo quality checks (phone, then server)

The phone computes on the scanner's JPEG, downscaled to 800 px on the long side, grayscale:

| check | measure | problem when |
|---|---|---|
| blur | variance of a 3×3 Laplacian | < 60 |
| too dark / washed out | mean luminance | < 70 or > 215 |
| faint print | 95th − 5th luminance percentile | < 80 |
| glare | fraction of pixels ≥ 250 in 16×16 blocks that are ≥ 25% saturated | > 0.02 |

Thresholds are starting values in one config object on each side, to be calibrated on real photos. The
server repeats the blur/brightness checks (it never trusts the phone alone) and adds recognition-based ones.
Before upload the phone downscales to 3000 px on the long side, JPEG quality 90, keeping EXIF orientation applied.

## 7. Submit and the input report

`POST /api/v1/batches/{batch_id}/submit` → `200 {"status": "submitted", "report": {"pdf": "/portal/batches/b_…/report.pdf", "csv": "/portal/batches/b_…/report.csv"}}`

- Allowed while photos are still reading? No: `409 {"error": "still_reading"}` until `progress.waiting + reading == 0`.
- Everything goes (option b): Good and Corrections invoices go into the report as they stand; Retake and
  unresolved invoices are listed at the end as unresolved; deleted duplicates never appear.
- A retake accepted after Submit (§5) does not change the report already issued. The laptop keeps report
  versions per batch: once the retaken page has been read, the portal's **Rebuild report** on that batch
  issues the next version, and the phone still shows the report links from its own Submit.
- Submitting teaches the key: every line with a stock item chosen by the manager or accepted becomes a learned
  alias (supplier wording → stock item) for this shop.
- The report (PDF to print, CSV with the same rows) is built from a frozen snapshot of the batch:
  header (shop, batch, submitted at, device); one section per invoice sorted by supplier then invoice number,
  titled `INV 1844378 (pages 1, 2)` with date and stated total vs lines sum; columns ITEM (UNIT), quantity in
  base unit, cost excl. VAT, VAT, ☐; a ✎ mark on lines the manager typed; then "not in key" lines, then
  unresolved invoices. CSV adds a `typed_by_manager` column.

## 8. Portal (browser, laptop)

`/` batches · `/batches/<id>` invoices by tab with photos · `/batches/<id>/report.pdf|csv` · `/unresolved` ·
`/key` (per shop: stock items, learned aliases, remove a bad alias) · `/pair` (QR) · `/devices` (revoke) ·
`/adjudicate` (research page, unchanged). No logins yet (future work); the portal listens on the office
network only.
