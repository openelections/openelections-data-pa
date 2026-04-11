#!/usr/bin/env python3
"""
Parse Huntingdon County PA 2025 General (Municipal) Election precinct results.

Source: Huntingdon PA Precinct-Summary-with-Provisionals.pdf (Electionware format,
title-case "Statistics" label variant).

Usage:
    python parsers/pa_huntingdon_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Output columns: county, precinct, office, district, party, candidate,
votes, election_day, mail, provisional

This parser uses natural-pdf for precinct boundary iteration (finding
"Statistics" section markers and walking their regions) and simple
line-based regex parsing for the vote rows.
"""

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf


COUNTY = "Huntingdon"

# Party codes observed in the Huntingdon PDF (plus common PA codes).
# DEM/REP appears when a candidate cross-files on both major parties.
PARTY_CODES = ["DEM/REP", "DEM", "REP", "LBR", "LIB", "GRN", "CST", "FWD", "ASP", "DAR"]
PARTY_RE = re.compile(r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$")

# Row whose last 4 tokens are integers (total, election day, mail, provisional).
# e.g. "DEM BRANDON NEUMAN 40 34 6 0"
VOTE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
# Rows with a single total (e.g. "Registered Voters - Total 262")
SINGLE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)$")

# Non-office / junk lines to skip entirely.
SKIP_PREFIXES = (
    "Precinct Summary UNOFFICIAL RESULTS",
    "Precinct Summary OFFICIAL RESULTS",
    "Municipal 2025 Election Day",
    "November 4, 2025 Huntingdon County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Early/Abs Provision",
    "Day entee al Votes",
    "Total Votes Cast",
    "Contest Totals",
    "Voter Turnout - Total",
    "Vote For ",
)

# Local offices where the municipality / district is appended to the
# ALL-CAPS office header. Values are the normalized office names; we strip
# any term-count token ("4YR/1", "6YR/2", etc.) that may appear between the
# office name and the municipality.
LOCAL_OFFICE_PREFIXES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("SCHOOL DIRECTOR", "School Director"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("TAX ASSESSOR", "Tax Assessor"),
    ("CONSTABLE", "Constable"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
    ("TREASURER", "Treasurer"),
]

TERM_TOKEN_RE = re.compile(r"^\d+YR/\d+$")


def normalize_office(raw: str) -> tuple[str, str]:
    """
    Return (office, district) for an ALL-CAPS office header line from the PDF.

    Examples:
        "SHERIFF"                                    -> ("Sheriff", "")
        "PROTHONOTARY AND CLERK OF COURTS"           -> ("Prothonotary And Clerk Of Courts", "")
        "JUDGE OF THE SUPERIOR COURT"                -> ("Judge of the Superior Court", "")
        "JUDGE OF THE COURT OF COMMON PLEAS 20th Judicial District (Huntingdon County)"
                                                     -> ("Judge of the Court of Common Pleas", "")
        "MAGISTERIAL DISTRICT JUDGE DISTRICT 20-3-01"-> ("Magisterial District Judge", "20-3-01")
        "MAYOR 4YR/1 ALEXANDRIA"                     -> ("Mayor", "Alexandria")
        "BOROUGH COUNCIL ALEXANDRIA"                 -> ("Borough Council", "Alexandria")
        "SUPREME COURT RETENTION- D.W."              -> ("Supreme Court Retention - D.W.", "")
        "SUPERIOR COURT RETENTION"                   -> ("Superior Court Retention", "")
    """
    line = raw.strip()

    # Hard-coded exact-match statewide offices (mixed-case canonical form).
    exact = {
        "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
        "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
        "SHERIFF": ("Sheriff", ""),
        "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
        "TREASURER": ("Treasurer", ""),
        "CORONER": ("Coroner", ""),
        "DISTRICT ATTORNEY": ("District Attorney", ""),
        "CONTROLLER": ("Controller", ""),
        "REGISTER AND RECORDER": ("Register and Recorder", ""),
        "COUNTY COMMISSIONER": ("County Commissioner", ""),
        "SUPERIOR COURT RETENTION": ("Superior Court Retention", ""),
        "COMMONWEALTH COURT RETENTION": ("Commonwealth Court Retention", ""),
    }
    if line in exact:
        return exact[line]

    # Court of Common Pleas: strip the "NNth Judicial District (..)" suffix.
    if line.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
        return ("Judge of the Court of Common Pleas", "")

    # Magisterial District Judge with district code.
    m = re.match(r"MAGISTERIAL DISTRICT JUDGE DISTRICT\s+(.+)$", line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())

    # Retention with candidate initials ("SUPREME COURT RETENTION- D.W.").
    m = re.match(r"^(SUPREME COURT RETENTION)\s*-?\s*(.*)$", line)
    if m:
        tail = m.group(2).strip()
        return (f"Supreme Court Retention - {tail}" if tail else "Supreme Court Retention", "")

    # Local offices: office + optional "NNYR/N" term token + municipality.
    for prefix, norm in LOCAL_OFFICE_PREFIXES:
        if line == prefix or line.startswith(prefix + " "):
            remainder = line[len(prefix):].strip().split()
            # Drop a leading term-count token if present (e.g. "4YR/1").
            if remainder and TERM_TOKEN_RE.match(remainder[0]):
                remainder = remainder[1:]
            district = " ".join(w.capitalize() for w in remainder) if remainder else ""
            # Keep numeric tokens (e.g. "Huntingdon 1") verbatim after capitalize.
            district = re.sub(r"(\d+)", r"\1", district)
            return (norm, district)

    # Fallback: title-case the whole line but keep "of"/"the"/"and" lowercase.
    small = {"of", "the", "and", "for", "in", "to", "a"}
    words = line.lower().split()
    out = [w.capitalize() if (i == 0 or w not in small) else w for i, w in enumerate(words)]
    return (" ".join(out), "")


def parse_votes(tokens: list[str]) -> tuple[int, int, int, int]:
    """Parse the last 4 tokens as ints, stripping commas."""
    return tuple(int(t.replace(",", "")) for t in tokens)  # type: ignore[return-value]


def extract_precinct_blocks(pdf):
    """
    Yield (precinct_name, text) tuples, one per precinct.

    Uses natural-pdf to find every "Statistics" marker (which begins a new
    precinct) and then collects text from that page through the page just
    before the next precinct's Statistics marker.
    """
    stat_hits = pdf.find_all('text:contains("Statistics")')
    # Keep only rows where the element text is exactly "Statistics" (avoids
    # false positives on other lines containing the word).
    stat_hits = [el for el in stat_hits if el.text.strip() == "Statistics"]

    if not stat_hits:
        raise RuntimeError("No 'Statistics' markers found; wrong PDF format?")

    # Precinct starting pages (1-indexed, matching natural-pdf page.number).
    start_pages = [el.page.number for el in stat_hits]

    # Precinct name = the non-empty line immediately above "Statistics".
    precinct_names = []
    for el in stat_hits:
        above_region = el.page.region(top=0, bottom=el.top)
        above_text = (above_region.extract_text() or "").strip().split("\n")
        # Walk upward past the page-header lines until we find a candidate.
        name = None
        for line in reversed(above_text):
            line = line.strip()
            if not line:
                continue
            if line.startswith(SKIP_PREFIXES):
                continue
            if line.endswith("Huntingdon County"):
                continue
            if line == "Statistics":
                continue
            name = line
            break
        if name is None:
            raise RuntimeError(f"Could not find precinct name above Statistics on page {el.page.number}")
        precinct_names.append(name)

    # Collect text per precinct, concatenating all pages in its range.
    total_pages = len(pdf.pages)
    for i, (start, name) in enumerate(zip(start_pages, precinct_names)):
        end = (start_pages[i + 1] - 1) if i + 1 < len(start_pages) else total_pages
        chunks = []
        for p in range(start, end + 1):
            chunks.append(pdf.pages[p - 1].extract_text() or "")
        yield name, "\n".join(chunks)


def parse_precinct_rows(precinct: str, text: str) -> list[dict]:
    """Parse one precinct's concatenated text into result row dicts."""
    rows: list[dict] = []
    current_office = None
    current_district = ""

    lines = [ln.strip() for ln in text.split("\n")]
    # Merge wrapped write-in continuation lines: if a line has no numeric
    # tokens and the previous line starts with "Write-In:", append it.
    merged: list[str] = []
    for ln in lines:
        if (
            merged
            and merged[-1].startswith("Write-In:")
            and ln
            and not re.search(r"\d", ln)
        ):
            merged[-1] = merged[-1] + " " + ln
        else:
            merged.append(ln)
    lines = merged

    # Mark which lines are office headers: a line is an office header iff
    # the next non-empty line starts with "Vote For". This is more reliable
    # than ALL-CAPS detection, because "Judge of the Court of Common Pleas
    # 20th Judicial District (Huntingdon County)" has mixed-case text.
    office_header_idx = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        # Look ahead for next non-empty line
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            if nxt.startswith("Vote For"):
                office_header_idx.add(i)
            break

    def add(office, district, party, candidate, vals):
        if vals is None:
            return
        total, ed, mail, prov = vals
        rows.append({
            "county": COUNTY,
            "precinct": precinct,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": total,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        })

    for idx, line in enumerate(lines):
        if not line:
            continue
        if line.startswith(SKIP_PREFIXES):
            continue
        if line == "Statistics":
            continue

        # Office header detected by next-line-is-"Vote For" heuristic.
        if idx in office_header_idx:
            office, district = normalize_office(line)
            current_office = office
            current_district = district
            continue

        # Statistics section rows.
        if line.startswith("Registered Voters - Total"):
            m = SINGLE_TAIL_RE.match(line)
            if m:
                rows.append({
                    "county": COUNTY,
                    "precinct": precinct,
                    "office": "Registered Voters",
                    "district": "",
                    "party": "",
                    "candidate": "",
                    "votes": int(m.group(2).replace(",", "")),
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                })
            continue
        if line.startswith("Ballots Cast - Total"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                total, ed, mail, prov = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                rows.append({
                    "county": COUNTY, "precinct": precinct, "office": "Ballots Cast",
                    "district": "", "party": "", "candidate": "",
                    "votes": total, "election_day": ed, "mail": mail, "provisional": prov,
                })
            continue
        if line.startswith("Ballots Cast - Blank"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                total, ed, mail, prov = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                rows.append({
                    "county": COUNTY, "precinct": precinct, "office": "Ballots Cast Blank",
                    "district": "", "party": "", "candidate": "",
                    "votes": total, "election_day": ed, "mail": mail, "provisional": prov,
                })
            continue

        # Data rows end in 4 integer tokens.
        vote_m = VOTE_TAIL_RE.match(line)
        if vote_m is None:
            continue

        # vote_m matched — it's a data row.
        head = vote_m.group(1).strip()
        vals = parse_votes([vote_m.group(i) for i in (2, 3, 4, 5)])

        if current_office is None:
            # Data row before any office — skip (shouldn't happen).
            continue

        # Retention rows: YES / NO.
        if head in ("YES", "NO"):
            add(current_office, current_district, "",
                head.capitalize(), vals)
            continue

        # Party-prefixed candidate.
        pm = PARTY_RE.match(head)
        if pm:
            party = pm.group(1)
            candidate = pm.group(2).strip()
            add(current_office, current_district, party, candidate, vals)
            continue

        # Special aggregate rows.
        if head == "Write-In Totals":
            add(current_office, current_district, "", "Write-ins", vals)
            continue
        if head.startswith("Write-In:"):
            # Named write-in breakout — skip per openelections convention.
            continue
        if head == "Not Assigned":
            # Skip — informational only, subset of Write-in Totals.
            continue
        if head == "Overvotes":
            add(current_office, current_district, "", "Overvotes", vals)
            continue
        if head == "Undervotes":
            add(current_office, current_district, "", "Undervotes", vals)
            continue

        # Unknown head — fall through silently for now.

    return rows


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: pa_huntingdon_general_2025_results_parser.py <input.pdf> <output.csv>")

    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")

    pdf = npdf.PDF(str(pdf_path))
    all_rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf):
        precinct_count += 1
        # "ALEXANDRIA" -> "Alexandria"; "HOPEWELL/PUTTSTOWN" -> "Hopewell/Puttstown";
        # "HUNTINGDON 1" -> "Huntingdon 1".
        pretty = re.sub(
            r"[A-Z]+",
            lambda m: m.group(0).capitalize(),
            precinct_name,
        )
        all_rows.extend(parse_precinct_rows(pretty, text))

    fieldnames = [
        "county", "precinct", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional",
    ]
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows across {precinct_count} precincts to {out_path}")


if __name__ == "__main__":
    main()
