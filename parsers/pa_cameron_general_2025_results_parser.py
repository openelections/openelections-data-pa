#!/usr/bin/env python3
"""
Parse Cameron County PA 2025 General (Municipal) Election precinct results.

Source: Cameron PA Nov 2025 Municipal Precinct Summary.pdf (Electionware format,
title-case "Statistics" label, "TOTAL / Election Day / Mail / Provisional"
column layout, municipality-prefixed local office names).

Usage:
    python parsers/pa_cameron_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Output columns: county, precinct, office, district, party, candidate,
votes, election_day, mail, provisional

Pattern is derived from the Huntingdon parser (same repo, same natural-pdf
approach) but adapted for Cameron's format quirks:

  - Precinct names appear mixed-case in the PDF ("Driftwood Borough"),
    so no title-casing is applied on output.
  - Local offices are ALL-CAPS with the municipality as a prefix
    ("DRIFTWOOD BOROUGH MAYOR", "SHIPPEN TOWNSHIP AUDITOR"), not a suffix.
    Detection works by matching a known office suffix and treating
    everything before it as the municipality/district.
  - Retention offices expand to full judge names
    ("SUPREME COURT RETENTION - CHRISTINE DONOHUE"), so the retention
    regex accepts all three courts and title-cases the name.
  - No "Not Assigned" / "Total Votes Cast" / "Contest Totals" rows.
"""

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf


COUNTY = "Cameron"

PARTY_CODES = ["DEM/REP", "DEM", "REP", "LBR", "LIB", "GRN", "CST", "FWD", "ASP", "DAR"]
PARTY_RE = re.compile(r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$")

VOTE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
SINGLE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)$")

SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election PRECINCT SUMMARY",
    "November 4, 2025 CAMERON COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day al",
    "Voter Turnout - Total",
    "Vote For ",
)

# Known statewide/countywide office headers → canonical names.
EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "SHERIFF": ("Sheriff", ""),
    "PROTHONOTARY": ("Prothonotary", ""),
    "PROTHONOTARY AND CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER AND RECORDER": ("Register and Recorder", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}

# Local office suffixes, longest first so "TOWNSHIP SUPERVISOR" matches
# before "SUPERVISOR" and "BOROUGH COUNCIL" before "COUNCIL".
LOCAL_OFFICE_SUFFIXES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTION", "Judge of Election"),
    ("INSPECTOR OF ELECTION", "Inspector of Election"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
    ("TREASURER", "Treasurer"),
]

RETENTION_RE = re.compile(
    r"^(SUPREME|SUPERIOR|COMMONWEALTH) COURT RETENTION\s*-\s*(.+)$"
)

SMALL_WORDS = {"of", "the", "and", "for", "in", "to", "a", "at", "on"}
ROMAN_RE = re.compile(r"^[IVX]+$")


def title_case(s: str) -> str:
    out = []
    for i, w in enumerate(s.split()):
        if ROMAN_RE.match(w.upper()):
            out.append(w.upper())  # Preserve Roman numerals (I, II, III, IV…).
        elif i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def normalize_office(raw: str) -> tuple[str, str]:
    """
    Return (office, district) for a raw office header line.

    Cameron-specific cases handled:
      "DRIFTWOOD BOROUGH MAYOR"            -> ("Mayor", "Driftwood Borough")
      "SHIPPEN TOWNSHIP AUDITOR"           -> ("Township Auditor", "Shippen")
      "WEST SHIPPEN TOWNSHIP CONSTABLE"    -> ("Constable", "West Shippen Township")
      "SUPREME COURT RETENTION - CHRISTINE DONOHUE"
                                           -> ("Supreme Court Retention - Christine Donohue", "")
      "SCHOOL DIRECTOR AT LARGE 2YR"       -> ("School Director At Large (2 Year)", "")
      "SCHOOL DIRECTOR AT LARGE 4YR"       -> ("School Director At Large (4 Year)", "")
      "SCHOOL DIRECTOR REGION II"          -> ("School Director Region II", "")
    """
    line = raw.strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    # Retention — match any of the three PA appellate courts.
    m = RETENTION_RE.match(line)
    if m:
        court = m.group(1).capitalize()
        name = title_case(m.group(2).strip())
        return (f"{court} Court Retention - {name}", "")

    # Court of Common Pleas (district suffix).
    if line.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
        return ("Judge of the Court of Common Pleas", "")

    # Magisterial District Judge.
    m = re.match(r"MAGISTERIAL DISTRICT JUDGE DISTRICT\s+(.+)$", line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())

    # School Director: keep term information in the office name, no district.
    if line.startswith("SCHOOL DIRECTOR"):
        core = line[len("SCHOOL DIRECTOR"):].strip()
        # Replace "NNYR" with "(NN Year)" for readability.
        core = re.sub(r"\b(\d+)YR\b", r"(\1 Year)", core)
        core_tc = title_case(core) if core else ""
        office = "School Director" + (f" {core_tc}" if core_tc else "")
        return (office, "")

    # Local office: match by longest known suffix, municipality = prefix.
    for suffix, norm in LOCAL_OFFICE_SUFFIXES:
        if line == suffix:
            return (norm, "")
        if line.endswith(" " + suffix):
            prefix = line[: -len(suffix)].strip()
            return (norm, title_case(prefix))

    # Fallback: title-case everything and leave district blank.
    return (title_case(line), "")


def parse_votes(tokens: list[str]) -> tuple[int, int, int, int]:
    return tuple(int(t.replace(",", "")) for t in tokens)  # type: ignore[return-value]


def extract_precinct_blocks(pdf):
    """Yield (precinct_name, text) tuples, one per precinct."""
    stat_hits = [
        el for el in pdf.find_all('text:contains("Statistics")')
        if el.text.strip() == "Statistics"
    ]
    if not stat_hits:
        raise RuntimeError("No 'Statistics' markers found; wrong PDF format?")

    start_pages = [el.page.number for el in stat_hits]
    precinct_names = []
    for el in stat_hits:
        above_region = el.page.region(top=0, bottom=el.top)
        above_text = (above_region.extract_text() or "").strip().split("\n")
        name = None
        for line in reversed(above_text):
            line = line.strip()
            if not line:
                continue
            if line == "Statistics":
                continue
            if line.startswith(SKIP_PREFIXES):
                continue
            # Skip the date/county header line.
            if line.endswith("CAMERON COUNTY"):
                continue
            name = line
            break
        if name is None:
            raise RuntimeError(
                f"Could not find precinct name above Statistics on page {el.page.number}"
            )
        precinct_names.append(name)

    total_pages = len(pdf.pages)
    for i, (start, name) in enumerate(zip(start_pages, precinct_names)):
        end = (start_pages[i + 1] - 1) if i + 1 < len(start_pages) else total_pages
        chunks = [pdf.pages[p - 1].extract_text() or "" for p in range(start, end + 1)]
        yield name, "\n".join(chunks)


def parse_precinct_rows(precinct: str, text: str) -> list[dict]:
    rows: list[dict] = []
    current_office = None
    current_district = ""

    lines = [ln.strip() for ln in text.split("\n")]

    # Merge wrapped write-in continuation lines (same as Huntingdon).
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

    # Office headers = lines whose next non-empty line starts with "Vote For".
    office_header_idx = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            if nxt.startswith("Vote For"):
                office_header_idx.add(i)
            break

    def add(office, district, party, candidate, vals):
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

        if idx in office_header_idx:
            office, district = normalize_office(line)
            current_office = office
            current_district = district
            continue

        # Statistics rows.
        if line.startswith("Registered Voters - Total"):
            m = SINGLE_TAIL_RE.match(line)
            if m:
                rows.append({
                    "county": COUNTY, "precinct": precinct,
                    "office": "Registered Voters", "district": "", "party": "",
                    "candidate": "",
                    "votes": int(m.group(2).replace(",", "")),
                    "election_day": "", "mail": "", "provisional": "",
                })
            continue
        if line.startswith("Ballots Cast - Total"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                vals = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                add("Ballots Cast", "", "", "", vals)
            continue
        if line.startswith("Ballots Cast - Blank"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                vals = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                add("Ballots Cast Blank", "", "", "", vals)
            continue

        vote_m = VOTE_TAIL_RE.match(line)
        if vote_m is None:
            continue

        head = vote_m.group(1).strip()
        vals = parse_votes([vote_m.group(i) for i in (2, 3, 4, 5)])

        if current_office is None:
            continue

        if head in ("YES", "NO"):
            add(current_office, current_district, "", head.capitalize(), vals)
            continue

        pm = PARTY_RE.match(head)
        if pm:
            add(current_office, current_district, pm.group(1), pm.group(2).strip(), vals)
            continue

        if head == "Write-In Totals":
            add(current_office, current_district, "", "Write-ins", vals)
            continue
        if head.startswith("Write-In:"):
            continue
        if head == "Not Assigned":
            continue
        if head == "Overvotes":
            add(current_office, current_district, "", "Overvotes", vals)
            continue
        if head == "Undervotes":
            add(current_office, current_district, "", "Undervotes", vals)
            continue

    return rows


def main():
    if len(sys.argv) != 3:
        sys.exit("Usage: pa_cameron_general_2025_results_parser.py <input.pdf> <output.csv>")

    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")

    pdf = npdf.PDF(str(pdf_path))
    all_rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf):
        precinct_count += 1
        all_rows.extend(parse_precinct_rows(precinct_name, text))

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
