#!/usr/bin/env python3
"""
Parser for Wyoming County 2025 General Election Results

Processes PDF file from Wyoming County to produce precinct-level results
in OpenElections standardized format.

The PDF structure has:
- "Candidates Name" as a delimiter between contests
- Contest title spanning multiple lines
- Candidate names (YES/NO for retentions, or candidate names with party for regular races)
- Precinct names followed by vote counts for each candidate

Usage:
    python parsers/pa_wyoming_general_2025_results_parser.py

Input:
    /Users/dwillis/code/openelections-sources-pa/2025/Wyoming PA 2025-Municipal-Totals-v2.pdf

Output:
    2025/counties/20251104__pa__general__wyoming__precinct.csv
"""

import subprocess
import csv
import re
import os


def extract_pdf_text(pdf_path):
    """Extract text from PDF using pdftotext."""
    result = subprocess.run(
        ['pdftotext', pdf_path, '-'],
        capture_output=True,
        text=True
    )
    return result.stdout


def parse_office_title(lines, start_idx):
    """
    Parse a multi-line office title starting from start_idx.
    Returns (office_name, district, next_line_idx)
    """
    office_lines = []
    idx = start_idx

    # Read lines until we hit a line that looks like a candidate name or precinct
    while idx < len(lines):
        line = lines[idx].strip()

        # Stop if we hit YES/NO (retention), or a name with party affiliation, or empty line after content
        if line in ['YES', 'NO', 'MUNICIPAL ELECTION November 4, 2025']:
            break
        if line and ('DEM' in line or 'REP' in line or ';' in line):
            break
        if line == '':
            idx += 1
            continue

        if line:
            office_lines.append(line)

        idx += 1

    # Combine office lines
    office_text = ' '.join(office_lines)

    # Extract district if present
    district = None
    district_match = re.search(r'(\d+)(?:TH|ST|ND|RD) JUDICIAL DISTRICT', office_text, re.IGNORECASE)
    if district_match:
        district = district_match.group(1)

    # Normalize office name
    office_text = re.sub(r'\s*-\s*VOTE FOR \w+\s*$', '', office_text, flags=re.IGNORECASE)
    office_text = office_text.strip()

    return (office_text, district, idx)


def parse_candidates(lines, start_idx):
    """
    Parse candidate names starting from start_idx.
    Returns (list of (candidate, party) tuples, next_line_idx)
    """
    candidates = []
    idx = start_idx

    # Look for candidate names - they come before precinct names
    # Candidates will be on consecutive lines before we hit a precinct name
    while idx < len(lines):
        line = lines[idx].strip()

        # Skip "MUNICIPAL ELECTION" line
        if line == 'MUNICIPAL ELECTION November 4, 2025':
            idx += 1
            continue

        # Empty line - skip
        if not line:
            idx += 1
            continue

        # Check if this looks like a precinct name (has "Township", "Borough", "Ward", etc.)
        if any(keyword in line for keyword in ['Township', 'Borough', 'Ward', 'Indep', 'REG']):
            break

        # Check if it's a number (vote count) - means we've moved past candidates
        if line.isdigit():
            break

        # Check for special candidate markers
        if line in ['YES', 'NO', 'Write in', 'Scattered', 'invalid']:
            party = None
            if line in ['YES', 'NO']:
                candidates.append((line, party))
            elif line == 'Write in':
                candidates.append(('Write-In', party))
            idx += 1
            continue

        # Parse candidate with party (format: "NAME; PARTY" or "NAME DEM/REP")
        party = None
        candidate = line

        # Check for party after semicolon
        if ';' in line:
            parts = line.split(';')
            candidate = parts[0].strip()
            if len(parts) > 1:
                party_text = parts[1].strip()
                # Extract party (may be like "DEM/REP" or just "DEM")
                if '/' in party_text:
                    party = party_text.split('/')[0].strip()
                else:
                    party = party_text

        # Check for party at end without semicolon
        elif ' DEM' in line or ' REP' in line:
            # Extract party from end
            if line.endswith(' DEM'):
                candidate = line[:-4].strip()
                party = 'DEM'
            elif line.endswith(' REP'):
                candidate = line[:-4].strip()
                party = 'REP'
            elif ' DEM/' in line or ' REP/' in line:
                match = re.search(r'\s+(DEM|REP)/\w+\s*$', line)
                if match:
                    party = match.group(1)
                    candidate = line[:match.start()].strip()

        candidates.append((candidate, party))
        idx += 1

    return (candidates, idx)


def parse_precinct_results(lines, start_idx, num_candidates):
    """
    Parse precinct names and vote counts.
    Returns list of dicts with precinct and votes for each candidate.
    """
    results = []
    idx = start_idx

    while idx < len(lines):
        line = lines[idx].strip()

        # Stop at "Candidates Name" (next contest) or TOTALS
        if line == 'Candidates Name' or line == 'TOTALS':
            break

        # Empty line - skip
        if not line:
            idx += 1
            continue

        # Check if this looks like a precinct name
        if any(keyword in line for keyword in ['Township', 'Borough', 'Ward', 'Indep', 'REG']):
            precinct = line
            votes = []

            # Read next num_candidates lines as vote counts
            for _ in range(num_candidates):
                idx += 1
                if idx >= len(lines):
                    break
                vote_line = lines[idx].strip()
                if vote_line.isdigit():
                    votes.append(int(vote_line))
                elif vote_line == '':
                    # Try next line
                    idx += 1
                    if idx < len(lines):
                        vote_line = lines[idx].strip()
                        if vote_line.isdigit():
                            votes.append(int(vote_line))
                        else:
                            votes.append(0)
                    else:
                        votes.append(0)
                else:
                    # Not a number, might be another precinct name or end
                    votes.append(0)
                    idx -= 1
                    break

            if len(votes) == num_candidates:
                results.append({
                    'precinct': precinct,
                    'votes': votes
                })

            idx += 1
        else:
            idx += 1

    return results


def parse_wyoming_pdf(text):
    """
    Parse Wyoming County PDF text and extract all results.
    Returns list of result dicts.
    """
    lines = text.split('\n')
    all_results = []

    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()

        # Look for "Candidates Name" which marks start of a contest
        if line == 'Candidates Name':
            # Special case: check if "NO" appears before "Candidates Name"
            # This indicates a retention question where NO/YES are the candidates
            pre_candidates = []
            if idx > 0 and lines[idx-2].strip() == 'NO':
                pre_candidates.append(('NO', None))

            idx += 1

            # Skip empty lines
            while idx < len(lines) and not lines[idx].strip():
                idx += 1

            # Parse office title
            office, district, idx = parse_office_title(lines, idx)

            # Parse candidates (may include YES if this is a retention question)
            candidates, idx = parse_candidates(lines, idx)

            # If we found "NO" before, and found "YES" after, combine them
            if pre_candidates and candidates:
                # Check if first candidate is YES (retention question)
                if candidates[0][0] == 'YES':
                    candidates = pre_candidates + candidates

            if not candidates:
                idx += 1
                continue

            # Parse precinct results
            precinct_results = parse_precinct_results(lines, idx, len(candidates))

            # Create output records
            for precinct_data in precinct_results:
                precinct = precinct_data['precinct']
                votes_list = precinct_data['votes']

                for i, (candidate, party) in enumerate(candidates):
                    if i < len(votes_list):
                        all_results.append({
                            'county': 'Wyoming',
                            'precinct': precinct,
                            'office': office,
                            'district': district,
                            'party': party,
                            'candidate': candidate,
                            'votes': votes_list[i]
                        })

        idx += 1

    return all_results


def main():
    """Main function to parse Wyoming County PDF and output CSV."""
    pdf_path = '/Users/dwillis/code/openelections-sources-pa/2025/Wyoming PA 2025-Municipal-Totals-v2.pdf'
    output_dir = '2025/counties'
    output_file = '20251104__pa__general__wyoming__precinct.csv'

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    print("Extracting text from PDF...")
    text = extract_pdf_text(pdf_path)

    print("Parsing results...")
    results = parse_wyoming_pdf(text)

    print(f"Total results parsed: {len(results)}")

    # Write to CSV
    output_path = os.path.join(output_dir, output_file)
    fieldnames = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result)

    print(f"\nOutput written to: {output_path}")


if __name__ == '__main__':
    main()
