#!/usr/bin/env python3
"""Parse Armstrong County PA 2026 Primary precinct results.

Source: Armstrong County Precinct Results.pdf — an "Election Summary
Report" PDF with one precinct per 5-page block. Each block opens with
``Summary for: All Contests, <Precinct>, All Tabulators, All Counting Groups``
followed by contest sections of the form::

    GOVERNOR (DEM) (Vote for 1)
    DEM
    Precincts Reported: 1 of 1 (100.00%)
    Election Day Mail Provisional Total
    Times Cast 43 17 0 60 / 273 21.98%
    Candidate Party Election Day Mail Provisional Total
    JOSH SHAPIRO DEM 39 17 0 56 100.00%
    Total Votes 39 17 0 56
    Election Day Mail Provisional Total
    Scatter WRITE-IN 0 0 0 0 0.00%
    Unresolved Write-In 0 0 0 0

Long contest headers wrap across two lines (office + party on one line,
``(Vote for N)`` on the next). Candidate rows are 7-column:
name, party, Election Day, Mail, Provisional, Total, percentage.

Usage:
    uv run python parsers/pa_armstrong_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import natural_pdf as npdf

from electionware_primary_np import (
    PRIMARY_FIELDNAMES,
    STATEWIDE_OFFICES,
    _finalize_candidate,
)

# Armstrong writes "15TH CONGRESSIONAL DISTRICT" — the shared
# DISTRICT_ORDINAL_RE only matches "<N>TH DISTRICT" without the
# CONGRESSIONAL/LEGISLATIVE/SENATORIAL word between, so use a local copy.
DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+"
    r"(?:LEGISLATIVE\s+|SENATORIAL\s+|CONGRESSIONAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

PARTY_ABBR = (
    "DEMOCR|DEM|REPUBL|REP|NONPARTISAN|NON|GP|GREEN|LBR|LIB|CON|CONSTITUTIONAL"
)

# Contest header: "GOVERNOR (DEM) (Vote for 1)" or wrapped with (Vote for N)
# on a separate line. Capture office text + party.
CONTEST_RE = re.compile(
    rf"^(.+?)\s+\(\s*({PARTY_ABBR})\s*\)(?:\s*\(Vote for\s+\d+\))?\s*$",
    re.IGNORECASE,
)
# Fallback for contests with no party parens but a "(Vote for N)" suffix:
# "MEMBER OF DEMOCRATIC STATE COMMITTEE (Vote for 1)" — party is embedded in
# the office name (resolved by _normalize_office fallback below).
CONTEST_NO_PARTY_RE = re.compile(
    r"^(.+?)\s+\(Vote for\s+\d+\)\s*$", re.IGNORECASE,
)
VOTE_FOR_LINE_RE = re.compile(r"^\(Vote for\s+\d+\)\s*$", re.IGNORECASE)

# Detect party embedded in office text for Member-of-State-Committee contests
# (Armstrong doesn't put DEM/REP in parens for those).
EMBEDDED_PARTY_RE = re.compile(
    r"\b(DEMOCRATIC|REPUBLICAN)\b", re.IGNORECASE,
)

# Precinct header: "Summary for: All Contests, <Precinct>, All Tabulators, ..."
# "All Counting Groups" may wrap to the next line, so match only up to
# "All Tabulators" (non-greedy capture of the precinct name).
SUMMARY_RE = re.compile(
    r"^Summary for: All Contests, (.+?), All Tabulators"
)

# Candidate row: "JOSH SHAPIRO DEM 39 17 0 56 100.00%" — name, party, 4 ints, %.
CANDIDATE_RE = re.compile(
    rf"^(.+?)\s+(DEM|REP|WRITE-IN|GP|GRN|GREEN|LBR|LIB|IND|WEP|WFP|CON|NON|NONPARTISAN)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+[\d.]+%\s*$"
)

PARTY_NORMALIZE = {
    "DEMOCR": "DEM",
    "DEM": "DEM",
    "REPUBL": "REP",
    "REP": "REP",
    "NONPARTISAN": "NON",
    "NON": "NON",
    "GP": "GRN",
    "GREEN": "GRN",
    "LBR": "LBR",
    "LIB": "LBR",
    "CON": "CON",
    "CONSTITUTIONAL": "CON",
}

# Per-precinct committee races — Armstrong uses bare "COMMITTEEPERSON".
SKIP_OFFICE_PREFIXES = ("COMMITTEEPERSON",)


def _normalize_party(raw: str) -> str:
    return PARTY_NORMALIZE.get(raw.upper(), raw.upper())


def _normalize_office(raw_office: str) -> tuple[str, str]:
    """Return (office, district) for a raw contest office text (party parens
    already stripped by CONTEST_RE). Falls back to title-case for local
    offices not in the statewide table."""
    upper = raw_office.upper().strip()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    office_key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    if office_key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[office_key]
        return (norm, district if extract else "")
    for key, (norm, extract) in STATEWIDE_OFFICES.items():
        if office_key == key or office_key.startswith(key + " "):
            return (norm, district if extract else "")
    # Local office fallback: title-case.
    words = []
    for w in raw_office.split():
        if re.match(r"^[IVX]+$", w.upper()):
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return (" ".join(words), district)


def parse_armstrong_pdf(pdf_path: Path) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    current_precinct: str | None = None
    current_office = ""
    current_district = ""
    current_party = ""
    skip_contest = False

    for page in pdf.pages:
        text = page.extract_text() or ""
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            sm = SUMMARY_RE.match(line)
            if sm:
                current_precinct = sm.group(1).strip()
                current_office = ""
                skip_contest = False
                continue
            if current_precinct is None:
                continue
            # Wrapped (Vote for N) continuation — ignore.
            if VOTE_FOR_LINE_RE.match(line):
                continue
            cm = CONTEST_RE.match(line)
            if cm:
                raw_office = cm.group(1).strip()
                if any(raw_office.upper().startswith(p) for p in SKIP_OFFICE_PREFIXES):
                    current_office = ""
                    skip_contest = True
                    continue
                skip_contest = False
                current_party = _normalize_party(cm.group(2))
                current_office, current_district = _normalize_office(raw_office)
                continue
            cn = CONTEST_NO_PARTY_RE.match(line)
            if cn:
                raw_office = cn.group(1).strip()
                if any(raw_office.upper().startswith(p) for p in SKIP_OFFICE_PREFIXES):
                    current_office = ""
                    skip_contest = True
                    continue
                skip_contest = False
                # No party in parens — try to extract from office text
                # (e.g. "MEMBER OF DEMOCRATIC STATE COMMITTEE").
                ep = EMBEDDED_PARTY_RE.search(raw_office)
                current_party = ("DEM" if ep and ep.group(1).upper() == "DEMOCRATIC"
                                 else "REP" if ep else "")
                current_office, current_district = _normalize_office(raw_office)
                continue
            if skip_contest or not current_office:
                continue
            dm = CANDIDATE_RE.match(line)
            if not dm:
                continue
            name = dm.group(1).strip()
            party_token = dm.group(2).upper()
            ed = int(dm.group(3).replace(",", ""))
            mail = int(dm.group(4).replace(",", ""))
            prov = int(dm.group(5).replace(",", ""))
            total = int(dm.group(6).replace(",", ""))
            if name.upper() in ("SCATTER", "SCATTERED"):
                # Armstrong emits both "Scatter WRITE-IN" and "Scattered
                # WRITE-IN" rows for the same contest; collapse to the OE
                # convention name.
                candidate = "Scattered"
            elif name.upper() in ("TOTAL VOTES", "UNRESOLVED WRITE-IN",
                                  "TIMES CAST"):
                continue
            else:
                candidate = _finalize_candidate(name)
            rows.append({
                "county": "Armstrong",
                "precinct": current_precinct,
                "office": current_office,
                "district": current_district,
                "party": current_party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "provisional": prov,
                "absentee": mail,
            })
    # Collapse duplicate (precinct, office, district, party, candidate) rows
    # that arise from "Scatter WRITE-IN" + "Scattered WRITE-IN" being emitted as
    # two separate lines for the same contest. Sum votes and breakdown columns.
    merged: dict[tuple, dict] = {}
    order: list[tuple] = []
    for r in rows:
        key = (r["precinct"], r["office"], r["district"], r["party"], r["candidate"])
        if key not in merged:
            merged[key] = dict(r)
            order.append(key)
        else:
            merged[key]["votes"] += r["votes"]
            merged[key]["election_day"] += r["election_day"]
            merged[key]["provisional"] += r["provisional"]
            merged[key]["absentee"] += r["absentee"]
    return [merged[k] for k in order]


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PRIMARY_FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in PRIMARY_FIELDNAMES})


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_armstrong_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    offices = len({(r["office"], r["district"]) for r in rows})
    print(
        f"Wrote {len(rows)} rows across {offices} contests / "
        f"{precincts} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)