# Bedford County General Election 2025 Parser

This parser converts Bedford County's election summary PDF reports into OpenElections CSV format.

## Overview

Bedford County provides county-level summary reports in PDF format with the following structure:
- Election summary statistics (registered voters, ballots cast, turnout)
- Contest results organized by office
- Candidate vote breakdowns by method: Election Day, Mail-In, Provisional
- Write-in candidate details

## Files

- `extract_bedford_pdf.py` - Extracts text from PDF
- `pa_bedford_general_2025_results_parser.py` - Parses extracted text to CSV

## Requirements

```bash
# Install pdfplumber for PDF extraction
pip install pdfplumber

# Or with uv
uv pip install pdfplumber
```

## Usage

### Step 1: Extract PDF Text

```bash
python parsers/extract_bedford_pdf.py input.pdf bedford_text.txt
```

### Step 2: Parse to CSV

```bash
python parsers/pa_bedford_general_2025_results_parser.py bedford_text.txt output.csv
```

### Example

```bash
# Full workflow
python parsers/extract_bedford_pdf.py ~/Downloads/bedford_example.pdf bedford_2025.txt
python parsers/pa_bedford_general_2025_results_parser.py bedford_2025.txt 20251104__pa__general__bedford.csv
```

## Output Format

The parser produces CSV files in OpenElections format with these columns:

| Column | Description |
|--------|-------------|
| county | "Bedford" |
| precinct | Empty (county-level results) |
| office | Office name (includes municipality for local races) |
| district | District number (for magisterial district judges) |
| party | Party code (DEM, REP, LBR, DEMREP, etc.) |
| candidate | Candidate name or special values |
| votes | Total votes |
| election_day | Election day votes |
| mail | Mail-in votes |
| provisional | Provisional votes |

### Special Candidates

The parser includes these special candidate rows:
- `Registered Voters` - Total registered voters (office is empty)
- `Ballots Cast` - Total ballots cast for each contest
- `Undervotes` - Undervotes for each contest
- `Overvotes` - Overvotes for each contest

## Office Name Handling

The parser preserves full office names as they appear in the PDF:

- Countywide: `"Judge of the Superior Court"`
- With municipality: `"Mayor (4 Year Term) Bedford Borough"`
- With district: `"Magisterial District Judge Magisterial District 57-03-01"`

District numbers are extracted to the `district` column when present.

## Notes

- This parser handles **county-level summary data only** (no precinct breakdowns)
- Write-in candidates are included with their vote totals
- "Unresolved Write-In" rows are excluded
- Party codes like "DEMREP" indicate cross-party nomination
- The registered voters count (33,011 for 2025) is hardcoded but can be updated

## Troubleshooting

**PDF extraction issues:**
- Ensure pdfplumber is installed: `pip install pdfplumber`
- Some PDFs may have image-based text requiring OCR
- Check that the PDF is not password-protected

**Parsing issues:**
- Verify the text file has proper formatting (tables aligned)
- Check that office headers end with "(Vote for X)"
- Ensure candidate rows have numeric vote columns

## Future Improvements

- Auto-detect registered voters from PDF
- Handle precinct-level reports if Bedford County provides them
- Add validation against known totals
- Support for special elections format
