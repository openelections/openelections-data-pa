"""Parser for Schuylkill County PA 2025 Municipal Election precinct PDFs.

The source is a directory of Electionware summary-report PDFs, one per
precinct/municipality (filename = precinct name). Each PDF contains multiple
contests laid out as:

    <Office Name>
    Vote For <N>
    Election
    TOTAL  Mail-In  Provisional
    Day
    <PARTY> <Candidate>  <total>  <ed>  <mail>  <prov>
    ...
    Write-In Totals  <total>  <ed>  <mail>  <prov>
    Write-In: <name>  <total>  <ed>  <mail>  <prov>   (skipped)
    Not Assigned ...                                    (skipped)
    Total Votes Cast ...                                (skipped)
    Overvotes ...                                       (skipped)
    Undervotes ...                                      (skipped)

Usage:
    uv run python parsers/pa_schuylkill_general_2025_results_parser.py \\
        <pdf_directory> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf

COUNTY = "Schuylkill"

PARTY_CODES = [
    "DEM/REP", "D/R", "DEM", "REP", "LBR", "LIB", "GRN", "CST", "IND",
]
PARTY_RE = re.compile(
    r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$"
)
VOTE_TAIL_RE = re.compile(
    r"^(.*?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)", re.IGNORECASE)

SKIP_PREFIXES = (
    "Summary Results Report",
    "4 November 2025",
    "November 25, 2025",
    "November",
    "Election Summary",
    "Report generated",
    "Election",
    "TOTAL",
    "Day",
)

SMALL_WORDS = {"of", "the", "and", "for", "a", "an", "in", "on", "to"}
ROMAN_RE = re.compile(r"^[IVX]+$")


def title_case(s):
    out = []
    for i, w in enumerate(s.split()):
        if ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def parse_votes(m):
    return tuple(int(m.group(i).replace(",", "")) for i in (2, 3, 4, 5))


def parse_pdf(pdf_path, precinct):
    """Parse a single precinct PDF, yielding row dicts."""
    pdf = npdf.PDF(str(pdf_path))
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    lines = [ln.strip() for ln in text.split("\n")]

    # Identify office-header lines: those followed (next non-empty) by "Vote For N".
    office_at = {}
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            vf = VOTE_FOR_RE.match(nxt)
            if vf:
                office_at[i] = int(vf.group(1))
            break

    current_office = None
    current_district = ""
    current_vote_for = 1

    for idx, line in enumerate(lines):
        if not line:
            continue
        if line.startswith(SKIP_PREFIXES):
            continue

        if idx in office_at:
            current_office = title_case(line)
            current_district = ""
            current_vote_for = office_at[idx]
            continue

        vm = VOTE_TAIL_RE.match(line)
        if vm is None:
            continue

        head = vm.group(1).strip()
        total, ed, mail, prov = parse_votes(vm)

        if current_office is None:
            continue

        # Skip meta rows.
        if head in ("Not Assigned", "Total Votes Cast", "Overvotes", "Undervotes"):
            continue
        if head.startswith("Write-In:"):
            continue

        if head == "Write-In Totals":
            yield {
                "county": COUNTY, "precinct": precinct,
                "office": current_office, "district": current_district,
                "party": "", "candidate": "Write-ins",
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov, "vote_for": current_vote_for,
            }
            continue

        if head.upper() in ("YES", "NO"):
            yield {
                "county": COUNTY, "precinct": precinct,
                "office": current_office, "district": current_district,
                "party": "", "candidate": head.capitalize(),
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov, "vote_for": current_vote_for,
            }
            continue

        pm = PARTY_RE.match(head)
        if pm:
            yield {
                "county": COUNTY, "precinct": precinct,
                "office": current_office, "district": current_district,
                "party": pm.group(1), "candidate": pm.group(2).strip(),
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov, "vote_for": current_vote_for,
            }


FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional", "vote_for",
]


def main(pdf_dir, output_csv):
    pdf_dir = Path(pdf_dir)
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"No PDF files found in {pdf_dir}")

    all_rows = []
    for pdf_path in pdfs:
        precinct = pdf_path.stem  # filename minus .pdf
        rows = list(parse_pdf(pdf_path, precinct))
        all_rows.extend(rows)
        print(f"  {precinct}: {len(rows)} rows")

    with open(output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows from {len(pdfs)} precincts to {output_csv}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: pa_schuylkill_general_2025_results_parser.py <pdf_dir> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
