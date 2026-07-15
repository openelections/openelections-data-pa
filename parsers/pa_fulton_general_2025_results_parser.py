#!/usr/bin/env python3
"""
Parser for Fulton County, PA 2025 General Election Results
Uses NaturalPDF to extract precinct-level results from PDF
"""

import csv
import re
import sys
from pathlib import Path

from natural_pdf import PDF


# Regex for candidate lines with party: NAME PARTY number pct% number pct% number pct% number pct%
CANDIDATE_RE = re.compile(
    r'^([A-Z][A-Z\s.\'-]+?)\s+'
    r'((?:DEM|REP|LBR|IND|GRE|LIB)(?:,\s*(?:DEM|REP|LBR|IND|GRE|LIB))*,?)\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%'
)

# Regex for YES/NO lines (no party)
YESNO_RE = re.compile(
    r'^(YES|NO)\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%\s+'
    r'(\d[\d,]*)\s+[\d.]+%'
)

# Regex for precinct header with registered voters
PRECINCT_RE = re.compile(
    r'^([A-Z][A-Z\s]+?)\s+(\d[\d,]*)\s+of\s+([\d,]+)\s+registered voters'
)

SKIP_PATTERNS = [
    'Official Results', 'FULTON COUNTY', 'MUNICIPAL ELECTION',
    'Registered Voters', 'Precincts Reporting', '11/4/2025',
    'Run Time', 'Run Date', '*** End'
]


def parse_fulton_results(pdf_path):
    """Parse Fulton County election results PDF"""
    pdf = PDF(pdf_path)
    results = []
    current_precinct = None
    current_office = None
    seen_precincts = set()
    # The source prints each Supreme Court retention question with an
    # identical, judge-less header ("SUPREME COURT RETENTION ELECTION
    # QUESTION:") three times per precinct -- once per justice, in the same
    # fixed order used statewide for the 2025 general (Donohue, Dougherty,
    # Wecht). Track how many we've seen for the current precinct to append
    # the right name, since nothing in the source itself distinguishes them.
    supreme_retention_order = ["Donohue", "Dougherty", "Wecht"]
    retention_counts = {}

    for page in pdf.pages:
        text = page.extract_text()
        lines = text.split('\n')
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            i += 1

            if not line:
                continue

            # Skip page header/footer lines
            if any(x in line for x in SKIP_PATTERNS):
                continue
            if re.match(r'^Page \d+$', line):
                continue
            # Skip top-left precinct label (mixed case, e.g. "Ayr Township")
            if re.match(r'^[A-Z][a-z]+ (?:Township|Borough)$', line):
                continue

            # Detect precinct header
            m = PRECINCT_RE.match(line)
            if m:
                precinct_name = m.group(1).strip().title()
                ballots_cast = m.group(2).replace(',', '')
                reg_voters = m.group(3).replace(',', '')
                # Only emit Registered Voters / Ballots Cast once per precinct
                # Fix McConnellsburg casing
                precinct_name = precinct_name.replace('Mcconnellsburg', 'McConnellsburg')
                # This header line repeats on every page of a precinct's
                # (possibly multi-page) block, not just once -- only treat it
                # as a real precinct change when the name actually changes,
                # so retention_counts doesn't reset mid-precinct.
                is_new_precinct = precinct_name != current_precinct
                if precinct_name not in seen_precincts:
                    seen_precincts.add(precinct_name)
                    current_precinct = precinct_name
                    results.append(make_row(current_precinct, 'Registered Voters', '', '', '', reg_voters))
                    results.append(make_row(current_precinct, 'Ballots Cast', '', '', '', ballots_cast))
                else:
                    current_precinct = precinct_name
                if is_new_precinct:
                    retention_counts = {}
                continue

            # Detect office header (ALL CAPS with YEAR TERM or QUESTION)
            if line.isupper() and ('YEAR TERM' in line or 'QUESTION' in line or 'RETENTION ELECTION' in line):
                current_office = normalize_office(line)
                if current_office == 'Supreme Court Retention Election Question':
                    idx = retention_counts.get(current_office, 0)
                    if idx < len(supreme_retention_order):
                        current_office = f"Supreme Court Retention Election Question - {supreme_retention_order[idx]}"
                    retention_counts['Supreme Court Retention Election Question'] = idx + 1
                continue

            # Skip column header
            if line.startswith('Choice') and 'Party' in line:
                continue

            # Skip summary lines
            if line.startswith(('Cast Votes:', 'Undervotes:', 'Overvotes:')):
                continue

            # Skip standalone party continuation lines (e.g. "REP" after "DEM,")
            if re.match(r'^(?:REP|DEM|LBR|IND|GRE|LIB)$', line):
                continue

            if not current_precinct or not current_office:
                continue

            # Try candidate with party
            cm = CANDIDATE_RE.match(line)
            if cm:
                candidate = cm.group(1).strip()
                party = cm.group(2).strip().rstrip(',')
                # Check if next line is a party continuation (e.g. "REP")
                if party.endswith(',') or (i < len(lines) and re.match(r'^(?:REP|DEM|LBR|IND|GRE|LIB)$', lines[i].strip())):
                    if i < len(lines):
                        next_line = lines[i].strip()
                        if re.match(r'^(?:REP|DEM|LBR|IND|GRE|LIB)$', next_line):
                            party = party.rstrip(',') + '/' + next_line
                            i += 1
                # Normalize party separators
                party = party.replace(', ', '/').replace(',', '/')
                mail = cm.group(3).replace(',', '')
                election_day = cm.group(4).replace(',', '')
                provisional = cm.group(5).replace(',', '')
                total = cm.group(6).replace(',', '')
                results.append(make_row(current_precinct, current_office, '', party, candidate, total, mail, election_day, provisional))
                continue

            # Try YES/NO
            ym = YESNO_RE.match(line)
            if ym:
                candidate = ym.group(1)
                mail = ym.group(2).replace(',', '')
                election_day = ym.group(3).replace(',', '')
                provisional = ym.group(4).replace(',', '')
                total = ym.group(5).replace(',', '')
                results.append(make_row(current_precinct, current_office, '', '', candidate, total, mail, election_day, provisional))
                continue

    return results


def make_row(precinct, office, district, party, candidate, votes, mail='', election_day='', provisional=''):
    return {
        'county': 'Fulton',
        'precinct': precinct,
        'office': office,
        'district': district,
        'party': party,
        'candidate': candidate,
        'votes': votes,
        'mail': mail,
        'election_day': election_day,
        'provisional': provisional
    }


def normalize_office(office):
    """Normalize office names"""
    office = office.strip()
    office = re.sub(r'\s*-\s*\(VOTE FOR.*?\)', '', office)
    office = office.rstrip(':')

    office_map = {
        'JUDGE OF THE SUPERIOR COURT TEN YEAR TERM': 'Judge of the Superior Court',
        'JUDGE OF THE COMMONWEALTH COURT TEN YEAR TERM': 'Judge of the Commonwealth Court',
        'SUPREME COURT RETENTION ELECTION QUESTION': 'Supreme Court Retention Election Question',
        'SUPERIOR COURT RETENTION ELECTION QUESTION': 'Superior Court Retention Election Question',
        'COMMONWEALTH COURT RETENTION ELECTION QUESTION': 'Commonwealth Court Retention Election Question',
    }

    for key, value in office_map.items():
        if key in office:
            return value

    if 'COURT OF COMMON PLEAS' in office and 'RETENTION' in office:
        return 'Court of Common Pleas Retention Election Question'

    if 'PROTHONOTARY' in office:
        return 'Prothonotary'

    return office.title()


def write_csv(results, output_path):
    """Write results to OpenElections CSV format"""
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'votes', 'mail', 'election_day', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: uv run python parsers/pa_fulton_general_2025_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_fulton_results(pdf_path)
    write_csv(results, output_path)


if __name__ == '__main__':
    main()
