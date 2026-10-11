# Invoice Capture and Unit Conversion for POS Stock Entry

**University of London, CSM500**

**Student number:** 230504895

**Module leader:** Harikala Kandel

## The problem

Supplier invoices use descriptions and quantities that differ from the stock catalogue used for Pilot entry. The application reads phone photographs or scanned PDFs, matches line items and converts supported quantities into stock units. A manager reviews uncertain results before generating a PDF or CSV input report; nothing is written directly to Pilot.

## Objectives

- Capture invoice pages and extract supplier details, invoice fields and line items.
- Match each line to a stock item and convert its quantity to the base unit used for stock entry.
- Allow a manager to review and correct uncertain results before export.

## Run the laptop application

Use Python 3.12. From `pipeline`, run `setup.ps1` on Windows, then `Launch Invoice Server.cmd`. For a manual environment, install `requirements.txt` and `requirements-ocr.txt` with pip. OCR models need an internet connection on first use; the application also needs authorised stock-reference exports, which are not included here.

The launcher starts the server and OCR reader. Open the portal to upload a scanned PDF, or pair the Android app using the laptop's reachable address on a trusted private network. Review the results before creating an input report. See [Python setup and operation](pipeline/README.md), [Android build and capture](android/README.md) and [phone API](pipeline/docs/phone-api.md).

## Current features

- Phone document scanning with a normal-camera fallback when the scanner is unavailable.
- Ordered PDF import into the existing OCR queue and scanned-page quality checks.
- Supplier-layout parsing, stock matching, unit conversion and explicit refusal reasons.
- Duplicate, price and total checks with manager corrections and retakes.
- Windows launcher, installer recovery and a [Clean Run utility](pipeline/packaging/CLEAN-RUN.md) that archives prior application state. Review its data and re-pairing instructions before use.

## Checks and limitations

Run `python -m pytest` from `pipeline`. Windows installer tests use controlled native stages; real OCR, independent installation and physical phone behavior need separate tests. The Android project includes its unit tests and Gradle wrapper.

Field trials exposed invoice grouping, faint-print, stock-matching and quantity problems. Fixes and regression tests address specific failures, but outputs still require human review. A Good classification is not proof of correctness. Historical comparisons measure agreement with manager entries, not independently established accuracy.

The PDF web report action blocks unread, failed or unattached pages. Older phone submission and report-rebuild paths do not provide the same complete-processing guard. The portal currently has no login or CSRF protection: restrict it to a trusted network and do not expose it to the internet. These limitations remain part of the prototype.

## Project code and dependencies

Project-specific logic covers supplier layouts, catalogue matching, unit interpretation, refusals, checks and the review workflow. PaddleOCR and Tesseract provide printed recognition; TrOCR supports a separate handwriting experiment. Flask serves the portal, and ML Kit and Jetpack Compose support Android capture and interface components. This component summary does not establish individual authorship; the submitted declaration should describe contributions and assistance accurately.

## Repository history

This repository contains the project code submitted for assessment. Development took place locally; the Git history records publication of the code rather than its full development history.

Private invoice data, Pilot exports, trial databases and packaged APK/EXE installers are excluded. The installer packaging scripts require private baseline inputs and are not a self-contained public installer distribution.
