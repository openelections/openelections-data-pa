#!/usr/bin/env python3
"""
Parser for Fulton County, PA 2022 General Election precinct results.

Adapted from parsers/pa_fulton_general_2025_results_parser.py (same vendor
layout). Differences vs. 2025 handled here:

- Office headers are 2022 wording ("UNITED STATES SENATOR - Vote for One",
  "GOVERNOR AND LIEUTENANT GOVERNOR ...", "REPRESENTATIVE IN CONGRESS
  13th Congressional District ...", "SENATOR IN THE GENERAL ASSEMBLY 30th
  SENATORIAL DISTRICT ...", "REPRESENTATIVE IN THE GENERAL ASSEMBLY 78th
  LEGISLATIVE DISTRICT ...") rather than the 2025 "YEAR TERM"/"QUESTION"
  headers, so office detection is keyword based.
- Party codes include KEY (Keystone) in addition to DEM/REP/LIB/GRN.
- Governor candidate names wrap across two lines ("... Governor CARRIE" /
  "LEWIS DELROSSO Lieutenant Governor"); the fragments are rejoined into
  "NAME1 / NAME2" pairs.
- Individually named write-in rows ("Name (W)") are aggregated into a single
  party-less "Write Ins" row per contest per precinct (2022 repo convention).
- Output uses the 2022 10-column convention:
  county,precinct,office,district,party,candidate,votes,election_day,early_voting,provisional
  with early_voting = the source's Mail column.

Usage: uv run python parsers/pa_fulton_general_2022_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

from natural_pdf import PDF


# Candidate line with party: NAME PARTY mail pct% ed pct% prov pct% total pct%
CANDIDATE_RE = re.compile(
    r"^([A-Z][A-Za-z\s.'\-]*?)\s+"
    r"(DEM|REP|LIB|GRN|KEY|IND|CON|LBR|GRE)\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s*$"
)

# Write-in line: Name (W) mail pct% ed pct% prov pct% total pct%
WRITEIN_RE = re.compile(
    r"^(.+?)\s+\(W\)\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s+"
    r"(\d[\d,]*)\s+[\d.]+%\s*$"
)

# Precinct header: NAME N of N registered voters = P%
PRECINCT_RE = re.compile(
    r"^([A-Za-z][A-Za-z\s.'\-]+?)\s+(\d[\d,]*)\s+of\s+([\d,]+)\s+registered voters"
)

# Candidate-line lookalike guard: a continuation fragment never ends in a
# number+percent pair, so use a cheap check when joining Governor wraps.
LOOKS_LIKE_DATA_RE = re.compile(r"\d[\d,]*\s+[\d.]+%")


def normalize_office(line):
    """Map a printed office header to (standard office, district)."""
    office = line.strip()
    office = re.sub(r"\s*-\s*Vote for.*$", "", office, flags=re.IGNORECASE).strip()

    m = re.search(r"REPRESENTATIVE IN CONGRESS\s+(\d+)\w*\s+Congressional District", office, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.search(r"SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)\w*\s+SENATORIAL DISTRICT", office, re.I)
    if m:
        return "State Senate", m.group(1)
    m = re.search(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)\w*\s+LEGISLATIVE DISTRICT", office, re.I)
    if m:
        return "State House", m.group(1)
    if "UNITED STATES SENATOR" in office.upper():
        return "U.S. Senate", ""
    if "GOVERNOR AND LIEUTENANT GOVERNOR" in office.upper():
        return "Governor", ""
    return office.title(), ""


def is_office_header(line):
    # Every office header in this report ends with "- Vote for ..." (e.g.
    # "UNITED STATES SENATOR - Vote for One"); requiring "Vote for" keeps
    # wrapped governor name fragments ("LEWIS DELROSSO Lieutenant Governor")
    # from being mistaken for headers.
    if "VOTE FOR" not in line.upper():
        return False
    return bool(re.match(r"^[A-Z0-9]", line)) and not LOOKS_LIKE_DATA_RE.search(line)


def build_governor_name(candidate_text, continuation_lines):
    """Join wrapped governor name fragments into 'SURNAME1 / SURNAME2'."""
    text = candidate_text
    for frag in continuation_lines:
        text += " " + frag
    text = text.replace(" Governor ", " / ")
    text = re.sub(r"\s*Lieutenant Governor\s*$", "", text)
    text = re.sub(r"\s+/ Lieutenant Governor", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_fulton_results(pdf_path):
    pdf = PDF(pdf_path)
    results = []
    current_precinct = None
    current_office = None
    current_district = ""
    in_governor = False
    seen_precincts = set()
    # aggregated write-ins per (precinct, office, district): [mail, ed, prov, total]
    writeins = defaultdict(lambda: [0, 0, 0, 0])
    writein_order = []

    def flush_writeins():
        for key in writein_order:
            precinct, office, district = key
            mail, ed, prov, total = writeins[key]
            results.append(make_row(precinct, office, district, "", "Write Ins",
                                    total, ed, mail, prov))
        writeins.clear()
        writein_order.clear()

    for page in pdf.pages:
        text = page.extract_text()
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()
            i += 1

            if not line:
                continue

            # Skip page header/footer lines
            if any(x in line for x in (
                    "Official Results", "FULTON COUNTY", "Precinct Results Report",
                    "MUNICIPAL ELECTION", "GENERAL ELECTION,", "Precincts Reporting",
                    "Run Time", "Run Date", "*** End", "End of report")):
                continue
            if re.match(r"^Page \d+$", line):
                continue
            if re.match(r"^\d[\d,]* of \d[\d,]* = \d+\.\d+%", line):
                continue  # county-wide turnout block in the page header
            if line in ("Registered Voters", "General Election"):
                continue

            # Precinct header (repeats on each page of a precinct's block)
            m = PRECINCT_RE.match(line)
            if m:
                precinct_name = m.group(1).strip().title()
                precinct_name = precinct_name.replace("Mcconnellsburg", "McConnellsburg")
                if precinct_name != current_precinct:
                    flush_writeins()
                    current_precinct = precinct_name
                    current_office = None
                    in_governor = False
                    if precinct_name not in seen_precincts:
                        seen_precincts.add(precinct_name)
                        results.append(make_row(current_precinct, "Registered Voters",
                                                "", "", "", m.group(3).replace(",", "")))
                        results.append(make_row(current_precinct, "Ballots Cast",
                                                "", "", "", m.group(2).replace(",", "")))
                continue

            # Office header
            if is_office_header(line):
                office, district = normalize_office(line)
                current_office = office
                current_district = district
                in_governor = office == "Governor"
                continue

            # Column header
            if line.startswith("Choice") and "Party" in line:
                continue

            # Summary lines. "Cast Votes:" marks the end of a contest block,
            # so any pending aggregated write-ins belong to it.
            if line.startswith(("Cast Votes:", "Undervotes:", "Overvotes:")):
                if line.startswith("Cast Votes:"):
                    flush_writeins()
                continue

            if not current_precinct or not current_office:
                continue

            # Candidate with party
            cm = CANDIDATE_RE.match(line)
            if cm:
                candidate = cm.group(1).strip()
                party = cm.group(2)
                mail = cm.group(3).replace(",", "")
                ed = cm.group(4).replace(",", "")
                prov = cm.group(5).replace(",", "")
                total = cm.group(6).replace(",", "")
                if in_governor:
                    # Collect wrapped name fragments until the next data line
                    frags = []
                    while i < len(lines):
                        nxt = lines[i].strip()
                        if (not nxt or CANDIDATE_RE.match(nxt) or WRITEIN_RE.match(nxt)
                                or is_office_header(nxt) or nxt.startswith(("Cast Votes:", "Undervotes:", "Overvotes:"))
                                or nxt.startswith("Choice") or PRECINCT_RE.match(nxt)):
                            break
                        frags.append(nxt)
                        i += 1
                    candidate = build_governor_name(candidate, frags)
                results.append(make_row(current_precinct, current_office, current_district,
                                        party, candidate, total, ed, mail, prov))
                continue

            # Write-in row -> aggregate
            wm = WRITEIN_RE.match(line)
            if wm:
                mail = int(wm.group(2).replace(",", ""))
                ed = int(wm.group(3).replace(",", ""))
                prov = int(wm.group(4).replace(",", ""))
                total = int(wm.group(5).replace(",", ""))
                key = (current_precinct, current_office, current_district)
                if key not in writeins:
                    writein_order.append(key)
                agg = writeins[key]
                agg[0] += mail
                agg[1] += ed
                agg[2] += prov
                agg[3] += total
                continue

    flush_writeins()
    return results


def make_row(precinct, office, district, party, candidate, votes,
             election_day="", early_voting="", provisional=""):
    return {
        "county": "Fulton",
        "precinct": precinct,
        "office": office,
        "district": district,
        "party": party,
        "candidate": candidate,
        "votes": votes,
        "election_day": election_day,
        "early_voting": early_voting,
        "provisional": provisional,
    }


def write_csv(results, output_path):
    fieldnames = ["county", "precinct", "office", "district", "party",
                  "candidate", "votes", "election_day", "early_voting", "provisional"]

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage: uv run python parsers/pa_fulton_general_2022_results_parser.py <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2]

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_fulton_results(pdf_path)
    write_csv(results, output_path)


if __name__ == "__main__":
    main()