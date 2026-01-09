# Electionware PDF Parser Development Guide

## Overview

This guide documents the development of parsers for Pennsylvania's Electionware election reporting system PDFs, including common issues encountered and solutions implemented. The Electionware system is used by many Pennsylvania counties to generate both precinct-level and county-level summary reports.

## Parser Files

- **`parsers/electionware_precinct.py`** - Precinct-level results parser (includes precinct column)
- **`parsers/electionware_county.py`** - County-level summary parser (no precinct column)

Both parsers share similar structure and logic but operate on different PDF formats.

## PDF Structure

### Precinct-Level PDFs
- Multiple pages, one or more pages per precinct
- Each precinct section begins with precinct name, followed by "STATISTICS"
- Statistics include: Registered Voters, Ballots Cast, Ballots Cast Blank
- Offices follow statistics, each with "Vote For N" header
- Candidate lines include party prefix, name, and vote columns (total, election_day, mail, provisional)
- Special rows: Write-In Totals, Not Assigned, Overvotes, Undervotes

### County-Level PDFs
- Same structure but without precinct breakdown
- Aggregated totals across entire county
- County name appears in header: "ADAMS COUNTY, PENNSYLVANIA"

## Common Issues and Solutions

### 1. Hard-Coded Values (County/Precinct Names)

**Problem:** Initial versions hard-coded county and precinct names, making the parser inflexible.

**Solution:** Extract dynamically from PDF:
```python
# County extraction - look for date pattern with county name
date_match = re.search(r'(?:November|October|...)\\s+\\d{1,2},\\s+\\d{4}\\s+(\\w+)', line)
if date_match:
    county = date_match.group(1).title()

# Precinct extraction - appears before STATISTICS marker
if line == "STATISTICS" and i > 0:
    # Look backwards for precinct name
    for j in range(i-1, max(0, i-5), -1):
        prev_line = lines[j].strip()
        # Filter out common headers, dates, page numbers
        if prev_line and is_valid_precinct_name(prev_line):
            precinct = prev_line
            break
```

**Lesson:** Always prefer dynamic extraction over hard-coding. Makes parsers reusable across counties and elections.

### 2. Format Variations Between Years

**Problem:** 2024 PDFs used ALL-CAPS office headers ("PRESIDENTIAL ELECTORS"), while 2025 PDFs used mixed-case ("Judge of the Superior Court").

**Solution:** Support both formats in office header detection:
```python
# 2024 format (all-caps)
if line == "PRESIDENTIAL ELECTORS":
    current_office = "President"

# 2025 format (mixed-case)
elif line == "Judge of the Superior Court":
    current_office = "Judge of the Superior Court"
```

**Lesson:** PDF formats can change between election years. Build parsers to handle multiple format variations. Test with data from multiple years.

### 3. Incomplete Party Code Coverage

**Problem:** Initially only included common party codes (DEM, REP, LIB, GRN). Missing DAR (cross-filed) and LBR (Libertarian) resulted in skipped candidates.

**Solution:** Expand party regex to include all observed codes:
```python
party_match = re.match(r'^(DEM|REP|LIB|LBR|GRN|CST|FWD|ASP|DAR)\\s+(.+)', line)
```

**Lesson:** 
- Scan entire PDF for all party prefixes before finalizing regex
- Pennsylvania allows cross-filing (DAR = Democratic and Republican)
- Minor parties may appear: LBR (Libertarian), GRN (Green), CST (Constitution), etc.
- Update regex as new parties appear in future elections

### 4. Missing Office Types

**Problem:** Initially only captured major statewide offices. Local offices (Tax Collector, Supervisor, Auditor, etc.) were missed.

**Solution:** Systematically discover all offices:
```python
# Search entire PDF for all office headers (appear before "Vote For N")
for page in pdf.pages:
    lines = page.extract_text().split('\\n')
    for i in range(len(lines) - 1):
        if re.match(r'Vote For \\d+', lines[i+1]):
            office = lines[i].strip()
            # Collect unique offices
```

Then add detection for each pattern:
- Exact matches: `"Clerk of Courts"`, `"Sheriff"`
- Prefix matches: `line.startswith("Tax Collector ")` (includes municipality)
- Retention elections: `"Retention" in line`

**Lesson:** Don't assume you know all possible offices. Extract them systematically from sample PDFs first.

### 5. Retention Elections Format

**Problem:** Initially stored judge name in district field. OpenElections standard requires judge name in office field.

**Incorrect:**
```
office="Supreme Court Retention", district="Christine Donohue"
```

**Correct:**
```
office="Supreme Court Retention - Christine Donohue", district=""
```

**Solution:**
```python
elif "Retention" in line:
    # Full line includes judge name: "Supreme Court Retention - Christine Donohue"
    current_office = line  # Keep entire line as office
    current_district = ""  # District remains blank
```

**Lesson:** Understand the target data format before parsing. Retention elections have unique structure (Yes/No votes, no party affiliation, judge name in office).

### 6. Special Candidate Rows

**Problem:** Missing "Not Assigned" rows caused data discrepancies between output and source PDFs.

**Solution:** Capture all special row types:
```python
# Regular candidates with party prefix
if party_match:
    # ... process candidate

# Yes/No votes (retention elections only)
elif line.startswith("Yes ") or line.startswith("No "):
    # ... process Yes/No vote

# Write-in totals
elif line.startswith("Write-In Totals"):
    # ... process write-ins

# Not assigned (write-ins attributed to candidate)
elif line.startswith("Not Assigned"):
    # ... process not assigned

# Overvotes/Undervotes
elif line.startswith("Overvotes") or line.startswith("Undervotes"):
    # ... process over/under votes
```

**Lesson:** Election data includes more than just candidates. Capture all row types for complete, verifiable results.

### 7. Loop Control Flow Bug

**Problem:** The STATISTICS section inner loop wasn't breaking when it encountered office headers, causing candidates from the next office to be attributed to the previous office.

**Symptom:** "MARIA BATTISTA" (from Judge of the Superior Court) appeared under "Court of Common Pleas Retention - Shawn C Wagner" for the same precinct.

**Root Cause:**
```python
# STATISTICS section
while i < len(lines):
    line = lines[i].strip()
    if not line or line.startswith("Vote For") or line == "PRESIDENTIAL ELECTORS":
        break  # Only broke on these conditions
    # ... process statistics
    i += 1
```

When the loop encountered "Judge of the Superior Court", it didn't match the break conditions, so it continued iterating without processing it as an office header.

**Solution:** Add comprehensive break conditions for all office header patterns:
```python
if not line or line.startswith("Vote For"):
    break
# Break on any office header
if (line == "PRESIDENTIAL ELECTORS" or 
    line == "Judge of the Superior Court" or
    line == "Judge of the Commonwealth Court" or
    "Retention" in line or
    line.startswith("REP CONGRESS") or
    # ... all other office patterns):
    break
```

**Lesson:** 
- Inner loops that scan for specific content must have comprehensive exit conditions
- When adding new office types, update ALL relevant break conditions
- Test with data where offices span page boundaries
- Misattributed data is hard to spot - verify output against source systematically

### 8. Numeric Formatting

**Problem:** Vote totals in PDFs use comma separators ("1,891") which should be removed for CSV output.

**Solution:** Strip commas when extracting vote values:
```python
votes = [v.replace(',', '') for v in parts[-4:]]
```

**Lesson:** PDFs format numbers for readability. CSV output should use plain integers. Apply consistently to all numeric columns (total, election_day, mail, provisional).

### 9. Office Names with Districts

**Problem:** Local offices include municipality/district in the office line itself:
- "Tax Collector Abbottstown"
- "School Director Conewago Valley Region I"
- "Mayor Littlestown"

**Solution:** Extract office type and district separately:
```python
elif line.startswith("Tax Collector "):
    current_office = "Tax Collector"
    current_district = line.replace("Tax Collector ", "")
```

**Lesson:** Office headers aren't always simple. Some embed district/municipality information that needs separate extraction.

### 10. Candidate Name Formatting

**Problem:** Presidential tickets include both presidential and VP candidates: "KAMALA D HARRIS - TIM WALTZ"

**Solution:** Trim to presidential candidate only for consistency:
```python
if current_office == "President" and " - " in candidate:
    candidate = candidate.split(" - ")[0]
```

**Lesson:** Standardize candidate names based on office type. Different offices may have different formatting requirements.

## Development Workflow

### 1. Reconnaissance Phase
```python
# Extract first 2000 chars to understand structure
import pdfplumber
pdf = pdfplumber.open('sample.pdf')
print(pdf.pages[0].extract_text()[:2000])

# Search for all unique office headers
offices = set()
for page in pdf.pages:
    lines = page.extract_text().split('\\n')
    for i in range(len(lines) - 1):
        if re.match(r'Vote For \\d+', lines[i+1]):
            offices.add(lines[i].strip())
print(sorted(offices))

# Search for all party prefixes
for page in pdf.pages:
    text = page.extract_text()
    # Look for 3-letter codes at line start
    for match in re.finditer(r'^([A-Z]{3})\\s+[A-Z]', text, re.MULTILINE):
        print(match.group(1))
```

### 2. Incremental Development
1. Start with basic structure (county, statistics)
2. Add major statewide offices (President, Senate, Governor)
3. Add congressional/legislative offices with district extraction
4. Add judicial offices (both 2024 and 2025 formats)
5. Add county offices (Sheriff, Clerk of Courts, etc.)
6. Add local offices (Tax Collector, Mayor, Council, etc.)
7. Add retention elections
8. Test with complete PDF, verify row counts match

### 3. Validation
```bash
# Compare row counts
wc -l output.csv
# Should match: precincts × (offices × candidates + special rows + statistics)

# Check for missing offices (compare to PDF)
cut -d',' -f3 output.csv | sort -u

# Verify specific races
grep "Judge of the Superior Court" output.csv | head

# Check for candidate misattribution
grep "precinct_name,office_name" output.csv
```

## Best Practices

### 1. Dynamic Extraction
- Never hard-code county, precinct, or candidate names
- Extract from PDF structure whenever possible
- Use pattern matching for consistent elements

### 2. Comprehensive Pattern Coverage
- Document all possible office types before coding
- Include all known party codes
- Support format variations (ALL-CAPS and Mixed-Case)

### 3. Robust Loop Control
- Ensure inner loops have complete exit conditions
- Update break conditions when adding new patterns
- Test with edge cases (offices spanning pages)

### 4. Data Validation
- Compare output row counts to expected totals
- Verify candidate attribution (candidate appears under correct office/precinct)
- Check for missing offices or candidates
- Validate vote totals match PDF

### 5. Documentation
- Comment complex regex patterns
- Explain office header detection logic
- Document format variations between years
- Note Pennsylvania-specific quirks (cross-filing, retention elections)

## Future Enhancements

### 1. Flexible Office Detection
Instead of hard-coded if/elif chains, consider:
```python
OFFICE_PATTERNS = {
    'exact': {
        'PRESIDENTIAL ELECTORS': 'President',
        'Judge of the Superior Court': 'Judge of the Superior Court',
        # ...
    },
    'prefix': {
        'Tax Collector ': ('Tax Collector', lambda l: l.replace('Tax Collector ', '')),
        'Mayor ': ('Mayor', lambda l: l.replace('Mayor ', '')),
        # ...
    },
    'retention': {
        'pattern': lambda l: 'Retention' in l,
        'extract': lambda l: (l, '')  # (office, district)
    }
}
```

### 2. Party Code Registry
```python
PARTY_CODES = ['DEM', 'REP', 'LIB', 'LBR', 'GRN', 'CST', 'FWD', 'ASP', 'DAR']
party_pattern = r'^(' + '|'.join(PARTY_CODES) + r')\\s+(.+)'
```

### 3. Configuration Files
- Store office patterns in JSON/YAML
- Configure party codes per election
- Define expected offices per election type
- Version configurations by year/format

### 4. Testing Framework
- Unit tests for each parsing function
- Integration tests with sample PDFs
- Regression tests to catch format changes
- Validate output against known-good data

## Troubleshooting Guide

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| Missing candidates | Party code not in regex | Add party code to pattern |
| Wrong precinct attribution | STATISTICS break logic | Add office headers to break conditions |
| Missing office type | Office header not detected | Add office detection pattern |
| Candidate under wrong office | Loop control flow | Check STATISTICS section break conditions |
| Vote totals have commas | Missing comma removal | Add `.replace(',', '')` to vote extraction |
| Retention Yes/No missing | Yes/No pattern not checked | Add Yes/No candidate check |
| Row count mismatch | Missing special rows | Check for Not Assigned, Over/Under Votes |

## Pennsylvania-Specific Notes

### Cross-Filing (DAR)
Pennsylvania allows candidates to appear on multiple party ballots. The "DAR" code indicates a candidate filed as both Democrat and Republican.

### Retention Elections
Pennsylvania judges face retention elections where voters vote Yes/No rather than choosing between candidates. Format:
- Office includes judge name: "Supreme Court Retention - Christine Donohue"
- No party affiliation
- Candidates are "Yes" and "No"
- District field remains blank

### Local Office Variations
Local offices vary significantly by municipality type:
- Boroughs have Council Members and Mayors
- Townships have Supervisors (with varying term lengths: 2yr, 4yr, 6yr)
- All have Tax Collectors, Auditors, Judge of Elections, Inspector of Elections
- School Directors represent regions or serve at-large

### District Formats
Districts appear in various formats:
- Congressional: "15TH CONGRESSIONAL DISTRICT" or "REPRESENTATIVE IN CONGRESS 15TH CONGRESSIONAL DISTRICT"
- State Senate: "SENATOR IN THE GENERAL ASSEMBLY 25TH SENATORIAL DISTRICT"
- State House: "REP IN THE GENERAL ASSEMBLY 67TH LEGISLATIVE DISTRICT"

Use regex to extract district numbers: `r'(\\d+)(?:TH|ST|ND|RD)'`

## Conclusion

Building reliable election result parsers requires:
1. **Thorough reconnaissance** - Understand the PDF structure completely before coding
2. **Incremental development** - Build complexity gradually, testing at each stage
3. **Comprehensive coverage** - Account for all office types, party codes, and format variations
4. **Robust validation** - Verify output against source data systematically
5. **Documentation** - Record issues, solutions, and Pennsylvania-specific quirks

The Electionware parsers handle complex, real-world election data with multiple format variations. By following the patterns and lessons documented here, future parser development can be more efficient and produce more reliable results.
