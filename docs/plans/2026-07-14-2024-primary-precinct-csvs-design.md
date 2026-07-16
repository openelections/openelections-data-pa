# 2024 PA Primary — Precinct CSV production plan

Date: 2026-07-14
Issue: openelections/openelections-data-pa#153

## Goal

Produce a precinct-level results CSV for every Pennsylvania county that has a
precinct-level source PDF for the 2024-04-23 primary, in the standardized
OpenElections format. As of 2026-07-14, 10 of 67 counties are done (Adams,
Beaver, Berks, Blair, Carbon, Centre, Chester, Clearfield, Cumberland, Elk).

## Output schema

```
county, precinct, office, district, party, candidate, votes
[, election_day, absentee, mail, provisional, military, extra]
```

- Filename: `20240423__pa__primary__<county>__precinct.csv`
- Location: `2024/counties/`
- Offices: President, U.S. Senate, U.S. House, State Senate, State House,
  Attorney General, Auditor General, State Treasurer, plus Registered Voters
  and Ballots Cast where the source provides them.
- `party`: candidate's party where the source provides it (PA primaries are
  party-specific; some reports carry one party per page).

## Tooling

- **All PDF extraction via `natural_pdf`** (deterministic: layout analysis,
  text extraction, OCR via Tesseract, table extraction). No LLM / Claude API
  calls for extraction.
- For text-layer PDFs: `natural_pdf`'s text/layout extraction.
- For image-only / scanned PDFs: `natural_pdf` OCR. If OCR quality is too poor
  to produce a verifiable CSV (totals don't match the county summary, or
  precinct/office names are garbled), **skip that county and move on** —
  leave a note in the status log.
- Routing / format detection: `uv run oepa detect <pdf>` where useful.

## Per-county workflow

1. Download source PDF(s) from `openelections/openelections-sources-pa/2024/primary`
   to a local cache dir (gitignored).
2. `uv run oepa detect <pdf>` to fingerprint the format family.
3. Route to a parser strategy:
   - **electionware_np / electionware_regex** (config-driven) — add a
     2024-primary config alongside the existing 2025-general one; same engine,
     adjust office/candidate regexes and page header signatures for the primary.
   - **sovc_geo / sovc_crosstab** — reuse existing engines (Wayne/Lycoming/Fulton
     shape; Bedford/Jefferson shape).
   - **electionware_text_precinct / csv_converter** — generic text families.
   - **custom** — write a `natural_pdf`-based parser for that county's format.
4. Write CSV to `2024/counties/20240423__pa__primary__<county>__precinct.csv`.
5. Verify against the county-level summary file (where one exists) via
   `uv run oepa verify 20240423__pa___primary <county> -d 2024/counties`.
6. Commit per format-family batch.

## Non-goals

- No LLM/Claude API extraction (user preference).
- No rewriting of existing 2025-general parsers; 2024-primary configs are
  added alongside.
- No CSVs committed that fail `oepa verify` without a recorded reason.
- Counties whose only source is a county-wide summary (no precinct breakdown)
  cannot be produced; they'll be noted as blocked-no-precinct-source.

## Status log

A running per-county status will be kept in this file's "Status" section as
work progresses: done / blocked-no-precinct-source / blocked-ocr / in-progress.