#!/usr/bin/env python3
"""Convert Philadelphia County 2026 primary precinct CSV to OpenElections format.

Source: Philadelphia_County Precinct Results_primary_2026.csv (exported from
the county's election-management system with Excel-style ="..." text cells).

Output schema: county, precinct, office, district, party, candidate, votes,
election_day, provisional, absentee.

Usage:
    uv run python parsers/pa_philadelphia_primary_2026_csv_converter.py <input.csv> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path


RACE_RE = re.compile(
    r'^(.+?)\s+(Democratic|Republican|Nonpartisan)\s+\(VOTE FOR\s+\d+\)$'
)
DISTRICT_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\s+DISTR(?:ICT)?\b", re.IGNORECASE)

OFFICE_MAP = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF DEMOCRATIC STATE COMMITEE": ("Member of Democratic State Committee", False),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

PARTY_MAP = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Nonpartisan": "NON",
}

# Strip trailing party abbreviation: "JOSH SHAPIRO DEM" -> "Josh Shapiro".
_TRAILING_PARTY_RE = re.compile(r"\s+(DEM|REP|NON|GRN|LBR|CON|IND|WEP|WFP)$")


def _clean(cell: str) -> str:
    """Strip Excel-style ="..." wrapper."""
    s = cell.strip()
    if s.startswith('="') and s.endswith('"'):
        return s[2:-1]
    return s


def _finalize_candidate(raw: str) -> str:
    s = _TRAILING_PARTY_RE.sub("", raw).strip()
    out = []
    for w in s.split():
        if re.match(r"^[IVX]+$", w.upper()):
            out.append(w.upper())
        elif w.upper() in ("JR", "SR"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif len(w) >= 3 and w[:2].lower() == "mc":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _parse_race(race_cell: str) -> tuple[str, str, str]:
    """Return (office, district, party) from a RaceName cell."""
    m = RACE_RE.match(race_cell)
    if not m:
        return ("", "", "")
    office_raw, party_raw = m.group(1).strip(), m.group(2)
    party = PARTY_MAP[party_raw]
    # Skip per-precinct Ward Executive Committee races (precinct id embedded).
    if office_raw.upper().startswith("WARD EXECUTIVE COMMITTEE"):
        return ("", "", "")
    upper = office_raw.upper()
    # State Committee races: district is the state committee district, not a
    # legislative one. Map to the canonical name without extracting district.
    if "MEMBER OF DEMOCRATIC STATE COMM" in upper or "MEMBER OF DEMOCRATIC STATE COMMITTEE" in upper:
        return ("Member of Democratic State Committee", "", party)
    if "MEMBER OF REPUBLICAN STATE COMMITTEE" in upper:
        return ("Member of Republican State Committee", "", party)
    dm = DISTRICT_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    base = DISTRICT_RE.sub("", upper).strip(" -").strip() if dm else upper
    if base in OFFICE_MAP:
        norm, extract = OFFICE_MAP[base]
        return (norm, district if extract else "", party)
    # Fallback: keep title-cased office.
    return (office_raw.title(), district, party)


FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "provisional", "absentee",
]


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.csv> <output.csv>")
    in_path = Path(argv[1])
    out_path = Path(argv[2])
    if not in_path.exists():
        sys.exit(f"Missing CSV: {in_path}")

    rows: list[dict] = []
    with in_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            race = _clean(r.get("RaceName", ""))
            office, district, party = _parse_race(race)
            if not office:
                continue
            cand_raw = _clean(r.get("CandidateName", ""))
            if cand_raw.upper() in ("WRITE-IN", "WRITEIN", "WRITE IN"):
                cand = "Write-In Totals"
            elif cand_raw.upper() in ("OVERVOTES", "UNDERVOTES", "NOT ASSIGNED"):
                cand = cand_raw.title()
            else:
                cand = _finalize_candidate(cand_raw)
            votes = _clean(r.get("CandidateVotes", "")).replace(",", "")
            ed = _clean(r.get("Election Day", "")).replace(",", "")
            mail = _clean(r.get("Mail Votes", "")).replace(",", "")
            prov = _clean(r.get("Provisional", "")).replace(",", "")
            rows.append({
                "county": "Philadelphia",
                "precinct": _clean(r.get("PrecinctName", "")),
                "office": office,
                "district": district,
                "party": party,
                "candidate": cand,
                "votes": int(votes) if votes else 0,
                "election_day": int(ed) if ed else "",
                "provisional": int(prov) if prov else "",
                "absentee": int(mail) if mail else "",
            })

    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)