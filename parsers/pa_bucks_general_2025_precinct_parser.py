#!/usr/bin/env python3
"""
Bucks County, PA 2025 General Election PRECINCT-level results parser.

Source: "Bucks PA 11.25.25 - Second Signing EMS Report - By Precinct (All
Precincts Except Northampton Twp District 17) (1).pdf" -- the
precinct-level EMS report (one "Precinct <name>" block per precinct, with
per-contest candidate rows: Votes ED MI PR). Northampton Twp District 17
is excluded by the county from this report and comes from the separate
"(Northampton Twp District 17 Only)" file; parse both and concatenate
(run this script per file, then merge; see the run note below).

Office normalization reuses pa_bucks_general_2025_results_parser.py
(normalize_office / extract_district) so precinct office names match the
county-level file exactly (including its "Justice of the Supreme Court
Retention Kevin M, Dougherty" quirks).

Usage:
    python pa_bucks_general_2025_precinct_parser.py <input_pdf> <output_csv>

Notes:
  - The prelude ("Total Ballots Cast: 240110") is countywide and repeats
    on every page, so no per-precinct Registered Voters / Ballots Cast
    rows exist in this format; none are emitted.
  - No party column in this source; party stays empty (same as the
    county-level file).
"""

import csv
import re
import sys
import tempfile
import subprocess
from pathlib import Path

from pa_bucks_general_2025_results_parser import extract_district, normalize_office, extract_text_from_pdf


def parse_precinct_results(text, forced_label=None):
    results = []
    current_precinct = None
    current_office = None
    current_district = ''
    in_contest = False

    for line in text.split('\n'):
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Skip the every-page prelude (municipality list + report header)
        if ('Page:' in line or 'Jurisdiction Wide' in line
                or 'All Districts, All Counter Groups' in line
                or line_stripped.startswith('Choice') and 'Votes' in line
                or 'Total Ballots Cast:' in line_stripped):
            continue
        if line_stripped.startswith('Second Signing EMS Report') or '11.25.25' in line or '11.24.25' in line:
            continue
        # The municipality list is one comma-heavy block ending with "All Boxes"
        if line_stripped.count(',') >= 3 or line_stripped.endswith('All Boxes'):
            continue
        if line_stripped == 'All Precincts':
            if forced_label:
                # Single-district file: the real data follows this header
                if current_precinct is None:
                    current_precinct = forced_label
                continue
            if not results:
                # Single-contest file: "All Precincts" appears before the
                # per-precinct blocks -- ignore it
                continue
            # Countywide aggregate block at the end of the file -- stop
            current_precinct = None
            current_office = None
            in_contest = False
            continue

        m = re.match(r'^Precinct\s+(.+)$', line_stripped)
        if m and ',' not in m.group(1):
            name = m.group(1).strip()
            if not name.isdigit():
                if forced_label is None:
                    current_precinct = name
                current_office = None
                in_contest = False
            continue

        if current_precinct is None:
            continue

        office_match = re.match(
            r'^([A-Z][A-Z\s\-0-9\,\.\/]+?)\s*\(Vote for \d+\)\s*$', line_stripped)
        if office_match:
            office_text = office_match.group(1).strip()
            # EMS spellings like "- 4Y" mean "- 4 YEAR" for normalize_office
            office_text = re.sub(r'\s*-\s*4Y\s*$', ' - 4 YEAR', office_text)
            current_office = normalize_office(office_text)
            current_district = extract_district(office_text)
            in_contest = True
            continue

        if in_contest and current_office:
            candidate_match = re.match(
                r'^\s+([A-Z][A-Za-z\s\.\,\'\-]+?)\s+([\d,]+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$', line)
            if not candidate_match:
                # Single-contest files include a Vote % column between
                # Votes and ED (e.g. "431  50.23%  294  135  2")
                candidate_match = re.match(
                    r'^\s+([A-Z][A-Za-z\s\.\,\'\-]+?)\s+([\d,]+)\s+[\d.]+%\s+(\d+)\s+(\d+)\s+(\d+)\s*$', line)
            total_match = re.match(
                r'^\s*Total\s+([\d,]+)\s+(?:[\d.]+%\s+)?(\d+)\s+(\d+)\s+(\d+)\s*$', line_stripped)
            if total_match:
                in_contest = False
                continue
            if candidate_match:
                candidate = candidate_match.group(1).strip()
                if candidate.lower() == 'total':
                    in_contest = False
                    continue
                results.append({
                    'county': 'Bucks',
                    'precinct': current_precinct,
                    'office': current_office,
                    'district': current_district,
                    'party': '',
                    'candidate': candidate,
                    'votes': candidate_match.group(2).replace(',', ''),
                    'election_day': candidate_match.group(3),
                    'mail': candidate_match.group(4),
                    'provisional': candidate_match.group(5),
                })
    return results
    return results


def write_csv(results, output_path):
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'votes', 'election_day', 'mail', 'provisional']
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator='\n')
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) < 3:
        print("Usage: python pa_bucks_general_2025_precinct_parser.py <input_pdf> <output_csv> [--label PRECINCT_NAME]")
        sys.exit(1)
    label = None
    if '--label' in sys.argv:
        label = sys.argv[sys.argv.index('--label') + 1]
    text = extract_text_from_pdf(sys.argv[1])
    results = parse_precinct_results(text, forced_label=label)
    precincts = sorted({r['precinct'] for r in results})
    print(f"Parsed {len(results)} rows across {len(precincts)} precincts")
    write_csv(results, sys.argv[2])


if __name__ == "__main__":
    main()
