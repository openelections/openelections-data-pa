# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

OpenElections Data Pennsylvania is a project to collect and standardize precinct-level election results for Pennsylvania elections from 2000 onwards. The repository contains CSV files with standardized election results and Python parsers that convert various source data formats into the standardized OpenElections format.

## Repository Structure

### Data Organization

- **Year directories** (`2000/`, `2002/`, `2004/`, etc.): Contain statewide consolidated CSV files with standardized results
  - Primary naming: `YYYYMMDD__pa__primary__precinct.csv`
  - General naming: `YYYYMMDD__pa__general__precinct.csv`
  - County-level naming: `YYYYMMDD__pa__general__county.csv`
  - Special elections: `YYYYMMDD__pa__special__general__[office]__[district].csv`

- **County subdirectories** (e.g., `2018/counties/`, `2024/counties/`): Contain per-county CSV files before consolidation
  - Format: `YYYYMMDD__pa__[election_type]__[county]__precinct.csv`

### Code Organization

- **`parsers/`**: County-specific parsers that convert raw election data into standardized CSV format
  - `el30_parser.py`, `el30a_parser.py`, `el30b_parser.py`: Parse EL30 format reports (common PA format)
  - `clarity_parser.py`: Parse Clarity Elections XML data
  - `csv_converter.py`: Generic text-to-CSV converter with precinct/office/candidate extraction
  - County-specific parsers: `pa_[county]_[election_type]_[year]_results_parser.py`

- **`statewide_generator.py`**: Consolidates county-level CSV files into single statewide file
  - Filters for specific statewide offices (President, Governor, U.S. House, State Senate, etc.)
  - Handles optional vote breakdown columns (election_day, absentee, mail, provisional, military, extra)

- **`utils.py`**: Utilities for converting older fixed-width format (2000-2016) to newer CSV format
  - Contains `COUNTIES` dict mapping codes (1-67) to county names
  - Contains `OFFICES` dict mapping PA office codes to standard office names

- **`validators/`**: Data validation scripts (e.g., `validator_2020_primaries.py`)

## Standardized Data Format

All output CSV files follow this schema:

| Column | Description | Required |
|--------|-------------|----------|
| `county` | County name (e.g., "Adams", "Allegheny") | Yes |
| `precinct` | Full precinct identifier (e.g., "Cumberland Township District 1") | Yes |
| `office` | Standardized office name | Yes |
| `district` | District number for congressional/legislative races | For district races |
| `party` | Party affiliation (3-letter code or full name) | When available |
| `candidate` | Candidate name or special values like "Registered Voters", "Ballots Cast" | Yes |
| `votes` | Total vote count | Yes |
| `election_day` | Election day votes | Optional |
| `absentee` | Absentee votes | Optional |
| `mail` | Mail-in votes | Optional |
| `provisional` | Provisional votes | Optional |
| `military` | Military votes | Optional |

### Special Candidate Values

- `"Registered Voters"`: Total registered voters for precinct (office=null)
- `"Ballots Cast"`: Total ballots cast for precinct (office=null)
- `"Ballots Cast - Blank"`: Blank ballots for precinct (office=null)

### Office Name Standardization

Parsers should normalize office names to these standard values:
- President → `"President"`
- U.S. Senate → `"U.S. Senate"`
- U.S. House → `"U.S. House"`
- Governor → `"Governor"`
- Attorney General → `"Attorney General"`
- Auditor General → `"Auditor General"`
- State Treasurer → `"State Treasurer"`
- State Senate → `"State Senate"`
- State House/State Representative → `"State House"` or `"State Representative"`

## Development Workflow

### Running Data Tests

The repository uses GitHub Actions to run data quality tests on all CSV files:

```bash
# Tests are defined in .github/workflows/data_tests.yml
# They use the openelections/openelections-data-tests repository (v2.2.0)
#
# Test types:
# - duplicate_entries: Checks for duplicate result rows
# - file_format: Validates CSV structure and required columns
# - missing_values: Ensures required fields are populated
# - vote_breakdown_totals: Verifies vote breakdowns sum to totals
```

Tests run automatically on push/pull request. View status via the Build Status badge in README.md.

### Creating a New Parser

1. **Identify the source format**: EL30 report, Clarity XML, PDF, Excel, or custom format
2. **Choose or create parser**: Use existing parser template if format matches known type
3. **Write parser script** in `parsers/` directory:
   - Name: `pa_[county]_[election_type]_[year]_results_parser.py`
   - Output: County CSV file in standardized format
4. **Place output** in appropriate year/counties directory
5. **Run `statewide_generator.py`** to create consolidated statewide file

### Generating Statewide Files

Edit and run `statewide_generator.py`:

```python
# Set these variables:
year = '2024'
election = '20241105'
path = election + '*precinct.csv'
output_file = election + '__pa__general__precinct.csv'

# Then run the appropriate function:
generate_consolidated_file(year, path, output_file)
```

This consolidates all county files matching the pattern into a single statewide CSV.

### Dependencies

Install via uv:

```bash
uv sync
```

Required packages (from pyproject.toml):
- `clarify`: Parse Clarity Elections XML
- `python-dateutil`: Date parsing
- `bs4`: HTML/XML parsing
- `requests`: HTTP requests
- `pandas`: Data manipulation

Python version: 3.9+

## Parser Development Patterns

### Text/PDF Parser Pattern (csv_converter.py)

The `ElectionResultsParser` class provides a state machine for parsing text-based reports:

1. **Precinct headers**: Identified by `is_precinct_header()` - single column, no office terms
2. **Office headers**: Identified by `is_office_header()` - capitalized, no digits in first word
3. **Candidate rows**: Tab-separated with vote counts (total, election_day, mail, provisional)
4. **Metadata rows**: "Registered Voters" and "Ballots Cast" lines

Usage:
```bash
python parsers/csv_converter.py input.txt output.csv CountyName
```

### EL30 Parser Pattern

Parse HTML-formatted EL30 reports by filtering lines and extracting structured data. See `parsers/el30_parser.py` for reference implementation.

### Clarity Parser Pattern

Use the `clarify` library to parse XML from Clarity Elections system. See `parsers/clarity_parser.py` for reference.

## Important Notes

- **Precinct naming**: Include municipality and breakdown type (District/Ward/Precinct) in precinct name
- **District extraction**: Parse district numbers from office names for congressional/legislative races
- **Party codes**: Use 3-letter codes (REP, DEM) or full names consistently
- **Vote totals**: When vote breakdowns are present, ensure they sum to the total
- **Data validation**: All CSV files must pass the four data test types before merging

## Historical Context

- **2000-2016**: Pennsylvania provided fixed-width format files from Bureau of Commissions, Elections and Legislation
- **2018+**: Most counties transitioned to different reporting systems (Clarity, EL30, custom formats)
- The repository structure reflects this transition with county-specific parsers for recent elections

## County Codes Reference

Pennsylvania has 67 counties numbered 1-67. Full mapping available in `utils.py` COUNTIES dictionary. Common counties:
- 02: Allegheny (Pittsburgh)
- 09: Bucks
- 15: Chester
- 23: Delaware
- 36: Lancaster
- 39: Lehigh
- 46: Montgomery
- 51: Philadelphia
- 67: York
