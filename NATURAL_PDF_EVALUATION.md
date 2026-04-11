# natural-pdf Evaluation for PA 2025 Precinct Parsing

Context: openelections/openelections-data-pa#168.
Source: `parsers/pa_huntingdon_general_2025_results_parser.py`, run against
`Huntingdon PA Precinct-Summary-with-Provisionals.pdf` (315 pages, 58 precincts,
Electionware format with title-case "Statistics" label).

## Verdict

**Mixed. Use natural-pdf for precinct boundary detection and region walking;
do NOT rely on its `extract_table()` for Electionware layouts.**

The Huntingdon parser produces 4,484 rows that match the existing
`20251104__pa__general__huntingdon__county.csv` summary exactly for every
statewide office (Judge of the Superior / Commonwealth Court, Judge of the
Court of Common Pleas, Sheriff, Prothonotary & Clerk of Courts, Registered
Voters, Ballots Cast, Ballots Cast Blank) — same totals for votes, election
day, mail-in, and provisional columns.

## What natural-pdf did well

- **`find_all('text:contains("Statistics")')`** cleanly enumerated all 58
  precincts in one call. Equivalent pdfplumber code would require iterating
  every page and regex-matching each line.
- **Regions spanning a precinct**: `page.region(top=0, bottom=el.top)` to read
  everything above the "Statistics" marker made precinct-name extraction a
  2-line loop. The existing `electionware_precinct.py` walks lines in reverse
  with a filter list to solve the same problem.
- **Page-range iteration**: knowing each precinct's starting page number
  (`el.page.number`) let the parser walk precincts as logical units across
  page boundaries without a global state machine.
- **Plain text extraction** via `page.extract_text()` produced clean,
  ordered lines — identical to pdfplumber because natural-pdf is built on it.

## What didn't work

- **`page.extract_table()`** returned garbage for Electionware layouts:
  columns were split mid-word ("ec inct S um mary", "llot s Cast - Total"),
  which made the default backend unusable. TATR could probably fix this at
  the cost of torch/transformers as a dependency, but there is no reason to
  pay that cost when line-based regex works perfectly on the text output.
- **Case-sensitivity of selectors**: `text:contains("STATISTICS")` returned
  zero hits because Huntingdon's PDF uses title-case "Statistics". Other
  Electionware PDFs (e.g. Adams, Elk) use ALL-CAPS. Any reusable template
  needs to try both forms.
- **Region `extract_text()` inclusivity**: `page.region(top=0, bottom=el.top)`
  still included the element at `el.top` itself. The precinct-name walker had
  to explicitly skip the "Statistics" string to avoid returning it as the
  precinct name. Minor gotcha but worth documenting.

## Lines of code

- `parsers/pa_huntingdon_general_2025_results_parser.py`: 399 lines (includes
  the full office-name normalization table and docstrings).
- `parsers/electionware_precinct.py`: 311 lines.

Net: natural-pdf did not save lines on this parser, because most of the
complexity is in PA-specific normalization (party codes, office-name
mappings, retention handling, cross-filing, local offices with embedded
municipality names). Those issues are orthogonal to which PDF library is
used. Where natural-pdf did help — precinct boundary detection, region
walking, office header location — the wins were clarity, not LOC.

## Bugs caught during development

1. **Mixed-case text in an ALL-CAPS office header** broke the ALL-CAPS
   heuristic for detecting office headers: "JUDGE OF THE COURT OF COMMON
   PLEAS 20th Judicial District (Huntingdon County)". Fix: detect office
   headers by "next non-empty line starts with 'Vote For'" — same trick the
   development guide already recommends.
2. **Walking the region above "Statistics"** included the "Statistics"
   string itself. Fix: explicitly skip it in the reverse walk.
3. **Retention judges identified only by initials** in Huntingdon
   ("SUPREME COURT RETENTION- D.W."). Kept as-is in the office name; other
   counties will likely have full names that need different handling.

## Recommendation for the remaining ~52 counties

1. **Default to natural-pdf for new Electionware precinct parsers.** The
   selector + region pattern is cleaner than the pdfplumber reverse-walk
   state machine.
2. **Skip `extract_table()`**; parse the raw text from
   `page.extract_text()` with regex for vote rows.
3. **Make the "Statistics" selector case-insensitive** or try both cases.
4. **Detect office headers by look-ahead for "Vote For"**, not by
   ALL-CAPS / Mixed-Case matching.
5. **Do not promote natural-pdf to replace the existing electionware_precinct.py
   right now.** That parser is stable and already targets a set of counties.
   Instead, build any new parser against natural-pdf and harvest common
   logic into a shared helper only after 2–3 counties are working.

## Concrete deliverable

`2025/counties/20251104__pa__general__huntingdon__precinct.csv` — 4,484 rows
across 58 precincts, verified to aggregate to the Huntingdon county summary
file for all statewide offices.
