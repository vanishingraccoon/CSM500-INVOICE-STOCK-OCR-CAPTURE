# Invoice Capture and Unit Conversion for POS Stock Entry

## The problem

News Cafe Eastgate receives paper supplier invoices. A manager reads each one and types the lines into Pilot, the stock system. Two things go wrong: transcription errors, and units — the invoice says "2 x 12.5kg bags", while Pilot expects 25 kilograms.

## Aim

To help restaurants cut down on manual stock entry and avoid unit input mistakes.

## Objectives

- O1: Capture invoice pages and extract supplier details, invoice fields and line items.
- O2: Match each line to a stock item and convert its quantity to the base unit used for stock entry.
- O3: Allow a manager to review and correct uncertain results before export.

## Running it

Not yet.

## Status

Initial project setup. The implementation will be added in reviewed stages.

## Main areas

- `pipeline/` — Python ingestion, recognition, parsing, normalisation, checks and review portal.
- `android/` — Android capture application.
- `docs/` — project notes and dated progress summaries.
