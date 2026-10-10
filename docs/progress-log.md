# Progress log

Short dated summaries of completed project work and the decisions that affected it.

## 20 September 2026

- Wrote the initial problem, aim and objectives for invoice capture and stock-unit conversion.
- Added separate `pipeline/` and `android/` workspaces.
- Kept implementation folders out until their first code commits.
## 21 September 2026

- Added machine paths and the initial Python test setup.
- Defined shared records for pages, OCR results, invoice lines, normalised lines, flags and export rows.
- Added reference normalisation tests for prefixes, leading zeroes and common OCR ambiguity.
## 22 September 2026

- Added lossless PDF page extraction with a rendering fallback.
- Collected supplier aliases and document-number patterns in one registry.
- Added header identification and tests for suppliers, invoice numbers, dates and page labels.

## 9–10 October 2026 — weekend publication

- Published the existing OCR, parsing, stock conversion, checks, evaluation and review code in stages.
- Added the Android capture, upload and review application with its build files.
- Pre-publication checks passed 252 Python tests and the Android debug build; Gradle reused the up-to-date Android unit-test result.
- Further integration and submission review remain for Sunday and Monday.
