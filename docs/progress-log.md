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

## 11 October 2026 — PC fixes and setup

- Corrected stock-unit conversion and added regression checks for conflicting unit evidence.
- Improved failed OCR job recovery, cache writes and supplier configuration.
- Added the Windows launcher, pinned setup dependencies and installer recovery for incomplete attempts, missing runtime files and restart-required outcomes.
- The operator confirmed the PC app works through installer v7. Field review has found unresolved invoice parsing and quantity issues; installation success does not establish output accuracy or a completed phone-to-export trial.
- Published the locally developed fixes with their tests. The release replay passed 289 Python tests, including controlled Windows installer scenarios; those scenarios do not replace clean-machine testing.
- Clarified repository publication history and excluded local data and generated files from routine Git additions.

## 12 October 2026 — final integration

- Published field-driven parsing, quantity and zero-price fixes with regression tests.
- Added PDF import and scanned-page quality rechecks, camera fallback and an archive-first trial reset tool.
- Updated setup instructions and documented the remaining report-guard and network-access limitations.
- The final Python replay passed 331 tests. Android build and unit tests passed; automated checks do not establish independent invoice accuracy.
- Preserved the original trial evidence separately. The 15-invoice PDF trial recorded Retake results; the quality recheck on a copy is diagnostic, not a new trial.
