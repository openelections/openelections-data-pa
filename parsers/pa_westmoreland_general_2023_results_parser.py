#!/usr/bin/env python3
"""
Parse Westmoreland County PA 2023 General (Municipal) Election results.

Source: Westmoreland County Official Results 2023 General.pdf (Electionware
"Summary Results Report", COUNTY-LEVEL ONLY -- the county did not publish a
precinct-level report for 2023, so this parser produces county-level rows
with an empty precinct column; the layout is vertical, 4 columns TOTAL /
Election Day / Absentee-Mail (merged) / Provisional, candidate rows carry NO
party codes, and contests repeat their header on every continuation page).
Only the last page of a contest carries its "Write-In Totals" row (the four
retention questions end with their "No" row instead), which is what
separates a genuine contest from a page continuation.

Usage:
    python parsers/pa_westmoreland_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Westmoreland-2023-specific quirks:
  - Retention questions: "Retention of Judge Panella" and "Retntion of
    Judge Stabile" (source typo) are the two Superior Court questions;
    "Retention of Judge Bilik-DeFazio" and "Retention of Judge Feliciani"
    are the local Court of Common Pleas questions. Candidate rows are
    plain Yes/No. Recorded as "<Court> Retention - <Name>".
  - Local headers carry an explicit term suffix ("COUNCIL ARONA BOROUGH -
    2 Year", "SUPERVISOR WASHINGTON TOWNSHIP - 6 Year"). Two-year contests
    get a "(2 Year)" office suffix (matching the county family's
    convention); auditors and supervisors keep the year on every contest
    (several townships elect auditors/supervisors to 2-, 4- and 6-year
    terms in the same election).
  - School director: "SCHOOL DIRECTOR [AT LARGE] <DIST> - N Year" and
    "SCHOOL DIRECTOR <DIST> REGION I/II/III - N Year"; the source
    misspells "LIGIONIER VALLEY" -> normalized "Ligonier Valley".
  - "MAGISTERIAL DISTRICT JUDGE 10-3-10 10-3-10" (doubled district number)
    -> district "10-3-10".
  - Municipal type is kept in district names where boroughs and townships
    share a name (Derry, Donegal, Ligonier, Mount Pleasant).
"""

import csv
import re
import sys
from pathlib import Path

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

COUNTY = "Westmoreland"

# ---------------------------------------------------------------------------
# Header normalization.
# ---------------------------------------------------------------------------

_EXACT = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY CONTROLLER": ("County Controller", ""),
    "COUNTY TREASURER": ("County Treasurer", ""),
    "SHERIFF": ("Sheriff", ""),
    "RECORDER OF DEEDS": ("Recorder of Deeds", ""),
    "REGISTER OF WILLS AND CLERK OF THE ORPHANS' COURT": (
        "Register of Wills and Clerk of the Orphans' Court",
        "",
    ),
}

_RETENTION_NAMES = {
    "PANELLA": "Superior Court Retention - Jack Panella",
    "STABILE": "Superior Court Retention - Victor P. Stabile",
    "BILIK-DEFAZIO": "Court of Common Pleas Retention - Bilik-DeFazio",
    "FELICIANI": "Court of Common Pleas Retention - Feliciani",
}
_RETENTION_RE = re.compile(r"^Ret\w*tion of Judge\s+(.+)$", re.IGNORECASE)

_MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(.+)$", re.IGNORECASE)

# School director: "SCHOOL DIRECTOR [AT LARGE] <DIST> [REGION N] - N Year"
_SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR\s+(AT LARGE\s+)?(.+?)\s*(?:REGION\s+([IVX]+))?\s*"
    r"-\s*(\d+)\s*Year$",
    re.IGNORECASE,
)

# Local offices: "<OFFICE> <muni> [Nth WARD] - N Year"
_LOCAL_RE = re.compile(
    r"^(MAYOR|TREASURER|CONTROLLER|COUNCIL|AUDITOR|SUPERVISOR|COMMISSIONER"
    r"|TAX COLLECTOR)\s+(.+?)\s*-\s*(\d+)\s*Year$",
    re.IGNORECASE,
)
_ORDINAL_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH))\s+WARD$", re.IGNORECASE)
_MUNI_TYPES = ("CITY OF ", "BOROUGH", "TOWNSHIP", "MUNICIPALITY OF ")

# School-district spellings (title-cased, hyphens preserved; "LIGIONIER"
# is a source typo for "Ligonier").
_SCHOOL_FIX = {"LIGIONIER": "Ligonier"}


def _cap_word(w: str) -> str:
    return "-".join(p.capitalize() for p in w.split("-"))


def _tcase(s: str) -> str:
    return " ".join(_cap_word(w) for w in s.split())


def _muni_name(tail: str) -> str:
    """Strip the municipal-type filler from a tail; keep the type word when
    boroughs and townships share a base name."""
    up = tail.upper()
    for pref in ("CITY OF ", "MUNICIPALITY OF "):
        if up.startswith(pref):
            return _tcase(tail[len(pref):].strip())
    return _tcase(tail)


def _muni_type(tail: str) -> str:
    up = tail.upper()
    if "BOROUGH" in up:
        return "Borough"
    if "TOWNSHIP" in up:
        return "Township"
    if "MUNICIPALITY" in up:
        return "Municipality"
    if "CITY" in up:
        return "City"
    return ""


def _strip_type(tail: str) -> str:
    """Remove BOROUGH/TOWNSHIP/CITY OF tokens from a tail."""
    toks = [t for t in tail.split() if t.upper() not in ("BOROUGH", "TOWNSHIP", "CITY", "OF")]
    return " ".join(toks)


def normalize(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    m = _RETENTION_RE.match(line)
    if m:
        key = m.group(1).strip().upper().replace(" ", "")
        office = _RETENTION_NAMES.get(key)
        if office is None:
            office = "Judicial Retention - " + _tcase(m.group(1))
        return (office, "")
    if line.upper() in _EXACT:
        return _EXACT[line.upper()]
    m = _MDJ_RE.match(line)
    if m:
        toks = m.group(1).split()
        # "10-3-10 10-3-10" (doubled district number) -> "10-3-10"
        code = toks[0]
        for t in toks[1:]:
            if t != code:
                code += " " + t
        return ("Magisterial District Judge", code)
    m = _school(line)
    if m:
        return m
    m = _LOCAL_RE.match(line)
    if m:
        return _local(m)
    return None


def _school(line: str):
    m = _SCHOOL_RE.match(line)
    if not m:
        return None
    at_large, dist, region, years = m.groups()
    years = int(years)
    parts = dist.split()
    fixed = " ".join(_SCHOOL_FIX.get(t.upper(), _cap_word(t)) for t in parts)
    if region:
        office = f"School Director Region {region.upper()}"
    elif at_large:
        office = "School Director At Large"
    else:
        office = "School Director"
    if years == 2:
        office += " (2 Year)"
    return (office, fixed)


def _local(m):
    prefix, tail, years = m.groups()
    years = int(years)
    tail = tail.strip()
    om = _ORDINAL_RE.match(tail)
    ward = None
    if om:
        tail, ward = om.group(1).strip(), om.group(2)
        ward = re.sub(
            r"^(\d+)(ST|ND|RD|TH)$",
            lambda mm: mm.group(1) + mm.group(2).lower(),
            ward,
            flags=re.IGNORECASE,
        )
    mtype = _muni_type(tail)
    base = _tcase(_strip_type(tail))
    p = prefix.upper()
    if p == "MAYOR":
        return ("Mayor", _muni_name(tail) if " OF " in tail.upper() else base)
    if p == "TREASURER":
        return ("City Treasurer", base)
    if p == "CONTROLLER":
        return ("City Controller", base)
    if p == "COUNCIL":
        if mtype == "Municipality":
            name = re.sub(r"^MUNICIPALITY\s+OF\s+", "", tail, flags=re.IGNORECASE)
            return ("Council", "Municipality of " + _tcase(name))
        if mtype == "Borough":
            office = "Borough Council (2 Year)" if years == 2 else "Borough Council"
            district = f"{base} Borough"
        else:
            office = "City Council (2 Year)" if years == 2 else "City Council"
            district = base
        if ward:
            district += f" {ward} Ward"
        return (office, district)
    if p == "AUDITOR":
        office = "Township Auditor" if mtype == "Township" else "Borough Auditor"
        office += f" ({years} Year)"
        return (office, f"{base} {mtype}" if mtype in ("Borough", "Township") else base)
    if p == "SUPERVISOR":
        return (f"Township Supervisor ({years} Year)", f"{base} Township")
    if p == "COMMISSIONER":
        district = f"{base} Township"
        if ward:
            district += f" {ward} Ward"
        return ("Township Commissioner", district)
    if p == "TAX COLLECTOR":
        return ("Tax Collector", f"{base} {mtype}" if mtype in ("Borough", "Township") else base)
    return None


# ---------------------------------------------------------------------------
# Parsing (county-level; vertical layout, 4 numeric columns).
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^\d[\d,]*$")
_VOTE_FOR_RE = re.compile(r"^Vote For\s+\d+$", re.IGNORECASE)
_JUNK_EXACT = {
    "Green - Won by Votes",
    "Yellow - Won Tie Breaker",
    "Red - Lost Tie Breaker",
}
# Page furniture / title lines.
_JUNK_SUB = (
    "OFFICIAL SUMMARY RESULTS -",
    "Report generated with Electionware",
    "Summary Results Report",
    "OFFICIAL MUNICIPAL ELECTION BALLOT",
    "November 7, 2023",
    "Westmoreland County",
)
_STATS_SKIP = (
    "Election Day Precincts Reporting",
    "Precincts Complete",
    "Precincts Partially Reported",
    "Absentee/",
    "Voter Turnout",
)


def parse_input(path: str):
    lines = [l.strip() for l in open(path, errors="replace")]
    rows = []
    warnings = []
    office = None
    district = ""
    contest_done = False  # True once the contest's last aggregate row was seen

    def emit(office_, district_, candidate, nums):
        nums = [n.replace(",", "") for n in nums]
        if len(nums) == 1:
            nums = [nums[0], "", "", ""]
        rows.append(
            {
                "county": COUNTY,
                "precinct": "",
                "office": office_,
                "district": district_,
                "party": "",
                "candidate": candidate,
                "votes": nums[0],
                "election_day": nums[1],
                "mail": nums[2],
                "provisional": nums[3],
            }
        )

    for i, l in enumerate(lines):
        if not l:
            continue
        # Office headers: next non-empty line is "Vote For N".
        nxt = next((x for x in lines[i + 1:] if x), "")
        if not _NUM_RE.match(l) and _VOTE_FOR_RE.match(nxt):
            norm = normalize(l)
            if norm is None:
                warnings.append(f"unrecognized office header: {l!r}")
                office = None
                continue
            office, district = norm
            contest_done = False
            continue
        if _JUNK_EXACT & {l}:
            continue
        if any(sub in l for sub in _JUNK_SUB):
            continue
        if _VOTE_FOR_RE.match(l):
            continue
        if l.startswith("Not Assigned"):
            continue
        if office is None:
            # Statistics block
            if l == "Registered Voters - Total":
                nums = _trailing_nums(l)
                if nums:
                    emit("Registered Voters", "", "", nums)
                continue
            if l == "Ballots Cast - Total":
                nums = _trailing_nums(l)
                if nums:
                    emit("Ballots Cast", "", "", nums)
                continue
            if l == "Ballots Cast - Blank":
                nums = _trailing_nums(l)
                if nums:
                    emit("Ballots Cast - Blank", "", "", nums)
                continue
            continue
        if re.match(r"^Write-In\s*:", l, re.IGNORECASE):
            continue
        nums = _trailing_nums(l)
        if not nums:
            continue
        head = " ".join(l.split()[: len(l.split()) - len(nums)])
        if head == "Write-In Totals":
            emit(office, district, "Write-ins", nums)
            contest_done = True
        elif head in ("Yes", "YES", "yes"):
            emit(office, district, "Yes", nums)
        elif head in ("No", "NO"):
            emit(office, district, "No", nums)
            contest_done = True
        else:
            emit(office, district, head, nums)
    return rows, warnings


def _trailing_nums(l: str):
    toks = l.split()
    out = []
    for t in reversed(toks):
        if _NUM_RE.match(t):
            out.append(t)
        else:
            break
        if len(out) >= 4:
            break
    out.reverse()
    return out


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    rows, warnings = parse_input(argv[1])
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} county-level rows to {out_path}")
    if warnings:
        print(f"WARNING: {len(warnings)} unrecognized headers:")
        for wmsg in warnings[:20]:
            print("  " + wmsg)


if __name__ == "__main__":
    main(sys.argv)