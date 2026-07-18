#!/usr/bin/env python3
"""Parse Perry County PA 2026 Primary county-level summary results.

Source: Perry County Summary-05.19.2026-Preliminary-Certification-Results-
for-the-General-Primary.pdf — a SOVC "Statement of Votes Cast by Geography"
report whose only geography is "All Precincts" (countywide). Each contest
block looks like::

    (D) Governor (DEM) (Vote for 1)
    1915 ballots (3 over voted ballots, 3 overvotes, 50 undervotes)
        JOSH SHAPIRO                        1813        978       834         1
        Write-In1                             49         36        13         0
        Total                               1862       1014       847         1
        Overvotes                              3
        Undervotes                            50

Contest headers carry a "(D) "/"(R) " ballot-party prefix that this parser
strips before office normalization. Per-precinct county-committee races
("Democratic County Committee <precinct>") are skipped.

Usage:
    uv run python parsers/pa_perry_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

PARTY_ABBR = r"DEMOCR|DEM|REPUBL|REP|NONPARTISAN|NON|GP|GREEN|LBR|LIB|CON"
CONTEST_RE = re.compile(
    rf"^(.+?)\s*\(\s*({PARTY_ABBR})\s*\)\s*\(Vote for\s+(\d+)\)\s*$",
    re.IGNORECASE,
)

# Candidate data row: "JOSH SHAPIRO 1813 978 834 1" (Name + Total + ED + MI + PR).
DATA_LINE_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)
# Overvotes/Undervotes rows have only one number.
SINGLE_TAIL_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s*$"
)

PARTY_NORMALIZE = {
    "DEMOCR": "DEM", "DEM": "DEM",
    "REPUBL": "REP", "REP": "REP",
    "NONPARTISAN": "NON", "NON": "NON",
}

STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?"
    r"DIS(?:TRICT|T)?\b",
    re.IGNORECASE,
)

# Per-precinct committee race: "Democratic County Committee Blain (DEM) (Vote for 2)".
# Skip these entirely.
COUNTY_COMMITTEE_RE = re.compile(
    r"(?:DEMOCRATIC|REPUBLICAN)\s+COUNTY\s+COMMITTEE\b",
    re.IGNORECASE,
)

BALLOT_PARTY_PREFIX_RE = re.compile(r"^\([DRIGB]\)\s+", re.IGNORECASE)

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
    if s.upper().startswith("WRITE-IN"):
        return "Write-In Totals"
    out = []
    for w in s.split():
        if _ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif w.upper() in ("JR", "SR"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif len(w) >= 3 and w[:2].lower() == "mc":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _normalize_office(raw: str) -> tuple[str, str]:
    upper = raw.upper()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "")
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "")
    return (raw.title(), district)


def parse_summary(county: str, pdf_path: Path) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    rows: list[dict] = []
    # Aggregate write-in slots (Write-In1, Write-In2, ...) into a single
    # "Write-In Totals" row per (office, district, party).
    writein_agg: dict[tuple, dict] = {}
    current_office = ""
    current_district = ""
    current_party = ""
    skip_block = False
    for line in proc.stdout.split("\n"):
        s = line.strip()
        if not s:
            continue
        cm = CONTEST_RE.match(s)
        if cm:
            raw_office = cm.group(1).strip()
            if COUNTY_COMMITTEE_RE.search(raw_office):
                skip_block = True
                current_office = ""
                continue
            skip_block = False
            current_party = PARTY_NORMALIZE.get(cm.group(2).upper(), cm.group(2).upper())
            raw_office = BALLOT_PARTY_PREFIX_RE.sub("", raw_office).strip()
            current_office, current_district = _normalize_office(raw_office)
            continue
        if skip_block or not current_office:
            continue
        # Ballots line / "Choice ... Votes ED MI PR" header / page banners.
        if re.match(r"^\d+\s+ballots\b", s, re.IGNORECASE):
            continue
        if re.match(r"^Choice\s+Votes\b", s, re.IGNORECASE):
            continue
        if re.match(r"^Statement of Votes Cast", s, re.IGNORECASE):
            continue
        if re.match(r"^Perry County, PA", s, re.IGNORECASE):
            continue
        if "All Precincts" in s or "Total Ballots Cast" in s:
            continue
        if re.match(r"^\d+\s+precincts reported", s, re.IGNORECASE):
            continue
        dm = DATA_LINE_RE.match(s)
        if dm:
            name = dm.group(1).strip()
            total = int(dm.group(2).replace(",", ""))
            ed = int(dm.group(3).replace(",", ""))
            mi = int(dm.group(4).replace(",", ""))
            pr = int(dm.group(5).replace(",", ""))
            if name == "Total":
                continue
            if name.upper().startswith("WRITE-IN"):
                key = (current_office, current_district, current_party)
                agg = writein_agg.setdefault(key, {
                    "county": county, "office": current_office,
                    "district": current_district, "party": current_party,
                    "candidate": "Write-In Totals",
                    "votes": 0, "election_day": 0, "mail": 0, "provisional": 0,
                })
                agg["votes"] += total
                agg["election_day"] += ed
                agg["mail"] += mi
                agg["provisional"] += pr
                continue
            candidate = _finalize_candidate(name)
            rows.append({
                "county": county,
                "office": current_office,
                "district": current_district,
                "party": current_party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "mail": mi,
                "provisional": pr,
            })
            continue
        sm = SINGLE_TAIL_RE.match(s)
        if sm:
            name = sm.group(1).strip()
            if name in ("Overvotes", "Undervotes"):
                rows.append({
                    "county": county,
                    "office": current_office,
                    "district": current_district,
                    "party": current_party,
                    "candidate": name,
                    "votes": int(sm.group(2).replace(",", "")),
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                })
            continue
    rows.extend(writein_agg.values())
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_summary("Perry", pdf_path)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in rows})
    print(f"Wrote {len(rows)} rows across {offices} contests to {out_path}")


if __name__ == "__main__":
    main(sys.argv)