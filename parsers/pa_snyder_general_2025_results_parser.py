#!/usr/bin/env python3
"""
Parse Snyder County PA 2025 General (Municipal) Election precinct results.

Source: Snyder PA Precinct-Summary-Official-11.24.2025.pdf (Electionware format,
title-case "Statistics" label, ALL-CAPS precinct names, prefix-style local
office headers, "RETAIN" (not "RETENTION") retention format).

Usage:
    python parsers/pa_snyder_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Output columns: county, precinct, office, district, party, candidate,
votes, election_day, mail, provisional

Based on the Huntingdon parser, adapted for Snyder's quirks:

  - Retention uses "SUPREME COURT - RETAIN CHRISTINE DONOHUE" (not
    "SUPREME COURT RETENTION - ..."). The RETAIN regex handles all three
    PA appellate courts and title-cases the judge name.
  - Local offices are prefix-style with the municipality at the end,
    sometimes with a term-length token in between:
      "TAX COLLECTOR ADAMS TWP"
      "TOWNSHIP AUDITOR 2YR PENN TWP"
      "BOROUGH COUNCIL 4YR SELINSGROVE BORO"
    The parser strips a NNYR token if present and normalizes TWP/BORO.
  - School director headers duplicate the district name at the tail:
      "MIDD-WEST SCHOOL DIRECTOR 4YR MIDD WEST SCHOOL DIRECTOR"
      "SELINSGROVE AREA SCHOOL DIRECTOR SELINSGROVE AREA SCHOOL DIRECTOR"
    The duplicate tail is stripped so the office becomes
    "School Director (4 Year)" with the district set to "Midd-West".
  - Has "Total Votes Cast" and "Contest Totals" summary rows which are
    silently ignored (not part of OpenElections schema).
  - Precinct names are ALL-CAPS and are title-cased on output.
"""

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf


COUNTY = "Snyder"

PARTY_CODES = ["DEM/REP", "DEM", "REP", "LBR", "LIB", "GRN", "CST", "FWD", "ASP", "DAR"]
PARTY_RE = re.compile(r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$")

VOTE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
SINGLE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)$")

SKIP_PREFIXES = (
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "Municipal Election",
    "November 4, 2025 SNYDER COUNTY",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL Election Mail Provision",
    "Day Votes al Votes",
    "Voter Turnout - Total",
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)

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
    "COUNTY REGISTER OF WILLS AND RECORDER OF DEEDS": (
        "Register of Wills and Recorder of Deeds", ""
    ),
}

# Local office prefixes (office-first, municipality-last).
# Order matters: longer prefixes first so "TOWNSHIP SUPERVISOR" beats
# "SUPERVISOR" and "BOROUGH COUNCIL" beats "COUNCIL".
LOCAL_OFFICE_PREFIXES = [
    ("BOROUGH COUNCIL", "Borough Council"),
    ("BOROUGH MAYOR", "Mayor"),
    ("TOWNSHIP SUPERVISOR", "Township Supervisor"),
    ("TOWNSHIP AUDITOR", "Township Auditor"),
    ("JUDGE OF ELECTIONS", "Judge of Elections"),
    ("INSPECTOR OF ELECTIONS", "Inspector of Elections"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("CONSTABLE", "Constable"),
    ("SUPERVISOR", "Supervisor"),
    ("AUDITOR", "Auditor"),
    ("MAYOR", "Mayor"),
]

RETAIN_RE = re.compile(
    r"^(SUPREME|SUPERIOR|COMMONWEALTH) COURT\s*-\s*RETAIN\s+(.+)$"
)

TERM_TOKEN_RE = re.compile(r"^\d+YR$")

SMALL_WORDS = {"of", "the", "and", "for", "in", "to", "a", "at", "on"}
ROMAN_RE = re.compile(r"^[IVX]+$")


def title_case(s: str) -> str:
    out = []
    for i, w in enumerate(s.split()):
        if ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def prettify_precinct(name: str) -> str:
    """ADAMS TOWNSHIP -> Adams Township; MCCLURE BOROUGH -> McClure Borough."""
    def fix(word: str) -> str:
        # Preserve # tokens (e.g. #1) and numeric parts.
        if word.startswith("#"):
            return word
        w = word.capitalize()
        # Fix "Mcclure" -> "McClure".
        if len(word) >= 3 and word.upper().startswith("MC"):
            w = "Mc" + word[2:].capitalize()
        return w
    return " ".join(fix(w) for w in name.split())


def normalize_municipality(raw: str) -> str:
    """Title-case a municipality, expanding TWP/BORO, fixing McClure."""
    toks = raw.split()
    out = []
    for t in toks:
        up = t.upper()
        if up == "TWP":
            out.append("Township")
        elif up == "BORO":
            out.append("Borough")
        elif t.startswith("#"):
            out.append(t)
        elif up.startswith("MC") and len(t) >= 3:
            out.append("Mc" + t[2:].capitalize())
        else:
            out.append(t.capitalize())
    return " ".join(out)


def normalize_office(raw: str) -> tuple[str, str]:
    """Return (office, district) for a raw office header line."""
    line = raw.strip()

    if line in EXACT_OFFICES:
        return EXACT_OFFICES[line]

    # Retention — "SUPREME COURT - RETAIN CHRISTINE DONOHUE".
    m = RETAIN_RE.match(line)
    if m:
        court = m.group(1).capitalize()
        name = title_case(m.group(2).strip())
        return (f"{court} Court Retention - {name}", "")

    # Court of Common Pleas.
    if line.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
        return ("Judge of the Court of Common Pleas", "")

    # Magisterial District Judge.
    m = re.match(r"MAGISTERIAL DISTRICT JUDGE DISTRICT\s+(.+)$", line)
    if m:
        return ("Magisterial District Judge", m.group(1).strip())

    # School Director: "MIDD-WEST SCHOOL DIRECTOR 4YR MIDD WEST SCHOOL DIRECTOR"
    # or "SELINSGROVE AREA SCHOOL DIRECTOR SELINSGROVE AREA SCHOOL DIRECTOR".
    sd = re.match(r"^(.+?)\s+SCHOOL DISTRICT?\s", line)  # unused guard
    if "SCHOOL DIRECTOR" in line:
        # Split on first "SCHOOL DIRECTOR" occurrence.
        head, _, tail = line.partition("SCHOOL DIRECTOR")
        district_raw = head.strip()
        tail = tail.strip()
        # Strip a leading term token ("4YR", "2YR") from tail.
        term_years = None
        tail_tokens = tail.split()
        if tail_tokens and TERM_TOKEN_RE.match(tail_tokens[0]):
            term_years = tail_tokens[0].rstrip("YR")
            tail_tokens = tail_tokens[1:]
        # Drop the duplicate "<district> SCHOOL DIRECTOR" tail if present.
        # Normalize by stripping all whitespace for comparison.
        tail_rest = " ".join(tail_tokens)
        # Remove the literal repeat of district (allowing for hyphen -> space).
        district_tokens = re.split(r"[-\s]+", district_raw)
        rest_tokens = re.split(r"[-\s]+", tail_rest)
        while rest_tokens and district_tokens and rest_tokens[0].upper() == district_tokens[0].upper():
            rest_tokens.pop(0)
            district_tokens.pop(0)
        # If what remains is "SCHOOL DIRECTOR", that's the duplicated label.
        # Any leftover after that goes back as part of the district.
        office_name = "School Director"
        if term_years:
            office_name = f"School Director ({term_years} Year)"
        district = title_case(district_raw.replace("-", "-"))
        # Nicer display: keep hyphen (MIDD-WEST -> Midd-West).
        district = "-".join(title_case(part) for part in district_raw.split("-"))
        return (office_name, district)

    # Local office: strip prefix, optional NNYR token, then municipality.
    for prefix, norm in LOCAL_OFFICE_PREFIXES:
        if line == prefix:
            return (norm, "")
        if line.startswith(prefix + " "):
            remainder = line[len(prefix):].strip().split()
            if remainder and TERM_TOKEN_RE.match(remainder[0]):
                years = remainder[0].rstrip("YR")
                remainder = remainder[1:]
                office_with_term = f"{norm} ({years} Year)"
            else:
                office_with_term = norm
            district = normalize_municipality(" ".join(remainder)) if remainder else ""
            return (office_with_term, district)

    # Fallback.
    return (title_case(line), "")


def parse_votes(tokens: list[str]) -> tuple[int, int, int, int]:
    return tuple(int(t.replace(",", "")) for t in tokens)  # type: ignore[return-value]


def extract_precinct_blocks(pdf):
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
            if line.endswith("SNYDER COUNTY"):
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
        sys.exit("Usage: pa_snyder_general_2025_results_parser.py <input.pdf> <output.csv>")

    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")

    pdf = npdf.PDF(str(pdf_path))
    all_rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf):
        precinct_count += 1
        pretty = prettify_precinct(precinct_name)
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
