#!/usr/bin/env python3
"""Parse Clarity Elections ``detail.xml`` for a PA 2026 primary county.

Clarity exports per-county precinct results as ``detailxml.zip`` containing
a single ``detail.xml``. Each contest has a name like ``DEM GOVERNOR`` or
``REP REPRESENTATIVE IN CONGRESS 12TH DISTRICT`` (party prefix on the
office). Each choice (candidate) has a ``party`` attribute and one
``VoteType`` per method (Election Day, Absentee/Mail, Provisional), and
each ``VoteType`` has one ``Precinct`` element per precinct with a vote
count.

This script reads a ``detail.xml`` and emits one row per
(county, precinct, office, district, party, candidate) with the total
vote plus the per-method breakdown (election_day, mail, provisional).
``Registered Voters`` and ``Ballots Cast`` contests are emitted as
metadata rows (candidate empty, party empty for Registered Voters).

Usage:
    uv run python parsers/clarity_primary_np.py <County> <detail.xml> <output.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import clarify


PARTY_PREFIX_RE = re.compile(
    r"^(DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON|NONPARTISAN|NON)\s+(.+)$",
    re.IGNORECASE,
)

# York uses a trailing "-DEM"/"-REP" suffix instead of a leading prefix:
# "Governor 4 Year Term-DEM", "Representative in Congress (District 10) 2 Year Term-DEM".
PARTY_SUFFIX_RE = re.compile(
    r"-(DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON|NONPARTISAN|NON)\s*$",
    re.IGNORECASE,
)

DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|SENATORIAL\s+|CONGRESSIONAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

# York writes districts in parentheses: "Representative in Congress (District 10)".
DISTRICT_PARENS_RE = re.compile(
    r"\(\s*DISTRICT\s+(\d+)\s*\)",
    re.IGNORECASE,
)

# Strip trailing "N YEAR TERM" suffix (Luzerne): "GOVERNOR 4 YEAR TERM".
TERM_SUFFIX_RE = re.compile(r"\s+\d+\s+YEAR\s+TERM\s*$", re.IGNORECASE)

# York's special election contest: "Special Election - Representative in
# the General Assembly (District 196)". Strip the prefix so the underlying
# office normalizes correctly; the special-election marker is preserved
# in the returned office name.
SPECIAL_ELECTION_PREFIX_RE = re.compile(
    r"^SPECIAL\s+ELECTION\s*-\s*",
    re.IGNORECASE,
)

STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "LT. GOVERNOR": ("Lieutenant Governor", False),
    "LT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "REP. IN CONGRESS": ("U.S. House", True),
    "REP IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "SENATOR IN GENERAL ASSEMBLY": ("State Senate", True),
    "SEN. IN THE GEN. ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "REPRESENTATIVE IN GENERAL ASSEMBLY": ("State House", True),
    "REP. IN GEN. ASSEMBLY": ("State House", True),
    "REP IN GEN ASSEMBLY": ("State House", True),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    # Allegheny uses bare "MEMBER OF STATE COMMITTEE" with party in the
    # contest prefix ("DEM Member of State Committee 37th District"). Resolve
    # to the party-specific name below.
    "MEMBER OF STATE COMMITTEE": ("__STATE_COMMITTEE__", False),
}

# Per-precinct committee race patterns to skip (precinct name embedded).
# Clarity's naming is wildly inconsistent across precincts: "<p> Republic
# County Committee", "<p> Republican County County Committee",
# "<p> Republican County Committe" (truncated), etc. Match any office name
# containing "Republic(an)? County Committe(e)?" or "Democratic County
# Committe(e)?" — but NOT "Member of (Democratic|Republican) State Committee"
# (which has "State Committee", not "County Committee").
PER_PRECINCT_COMMITTEE_RE = re.compile(
    r"COUNTY[^a-z]*COMMITTE"  # "County Committee" with optional words between
    r"|(?:DEMOCRAT|DEMOCRATIC|REPUBLIC|REPUBLICAN)\s+COUNTY\s+COMMITTE"
    r"|(?:COUNTY|PRECINCT|BOROUGH|TOWNSHIP|WARD|DISTRICT)\s+"
    r"COMMITTEE\s*(?:MAN|WOMAN|PERSON|MEMBER)S?\b"
    r"|COMMITTEE(?:MAN|WOMAN|PERSON)S?\b"
    # Delaware: "Committee Member, <precinct>" — bare office name with
    # a comma before the precinct, no party prefix.
    r"|COMMITTEE\s+MEMBER\b",
    re.IGNORECASE,
)

_ROMAN_RE = re.compile(r"^[IVX]+$")

# Leading ballot position prefix: "(11) Josh Shapiro" -> "Josh Shapiro".
_BALLOT_POS_RE = re.compile(r"^\(\d+\)\s*")


def _finalize_candidate(raw: str) -> str:
    s = raw.strip()
    if s.lower() in ("write-in", "writein", "write in"):
        return "Write-In Totals"
    if s.upper() == "SCATTER":
        return "Scattered"
    s = _BALLOT_POS_RE.sub("", s)
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


def _normalize_office(raw: str) -> tuple[str, str, str]:
    """Return (office, district, party) for a Clarity contest name.

    Party is the leading DEM/REP/etc. prefix (most counties) OR a trailing
    "-DEM"/"-REP" suffix (York). Office is the canonical name from
    STATEWIDE_OFFICES (with district extracted when applicable — either
    "Nth DISTRICT" or "(District N)" form). Local offices fall through to
    title-case with the district kept. "Special Election - " prefix is
    preserved on the office name.
    """
    upper = raw.upper()
    is_special = bool(SPECIAL_ELECTION_PREFIX_RE.match(upper))
    upper = SPECIAL_ELECTION_PREFIX_RE.sub("", upper).strip()
    # Trailing "-DEM"/"-REP" party suffix (York) — strip before term suffix
    # so "Governor 4 Year Term-DEM" strips party first, then "4 Year Term".
    party = ""
    psm = PARTY_SUFFIX_RE.search(upper)
    if psm:
        party = psm.group(1).upper()
        if party == "NONPARTISAN":
            party = "NON"
        upper = PARTY_SUFFIX_RE.sub("", upper).strip()
    # Strip trailing "N YEAR TERM" suffix (Luzerne/York naming convention).
    upper = TERM_SUFFIX_RE.sub("", upper).strip()
    pm = PARTY_PREFIX_RE.match(upper)
    if pm:
        # Leading prefix wins if both forms somehow present.
        party = pm.group(1).upper()
        if party == "NONPARTISAN":
            party = "NON"
        rest = pm.group(2).strip()
    else:
        rest = upper
    dm = DISTRICT_ORDINAL_RE.search(rest)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_ORDINAL_RE.sub("", rest).strip() if dm else rest
    if not dm:
        dpm = DISTRICT_PARENS_RE.search(rest)
        if dpm:
            district = str(int(dpm.group(1)))
            key = DISTRICT_PARENS_RE.sub("", rest).strip()
    # Normalize internal whitespace — Delaware's 159th District contest
    # has a typo: "Representative in the  General Assembly" (double space).
    key = re.sub(r"\s+", " ", key).strip()
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        office = norm
        out_district = district if extract else ""
    else:
        matched = False
        for k, (norm, extract) in STATEWIDE_OFFICES.items():
            if key == k or key.startswith(k + " "):
                office = norm
                out_district = district if extract else ""
                matched = True
                break
        if not matched:
            if PER_PRECINCT_COMMITTEE_RE.search(rest):
                return ("", "", "")
            # Unknown local office: title-case the raw text (minus party
            # prefix/suffix and term suffix). Strip the "(District N)" parens
            # from the display text too so it isn't duplicated with district.
            display = DISTRICT_PARENS_RE.sub("", rest).strip()
            display = re.sub(r"\s+", " ", display)
            return (("Special Election - " if is_special else "") + display.title(),
                    district, party)
    return (("Special Election - " if is_special else "") + office,
            out_district, party)


FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "provisional", "absentee",
]

# Map Clarity vote-type names to our breakdown columns. PA 2026 primary
# counties use three mail-vote spellings — "Absentee/Mail" (Westmoreland),
# "Mail In" (Luzerne), "Absentee" (Allegheny) — and they all flow into the
# ``absentee`` column to match the existing 2026/counties convention.
VOTE_TYPE_MAP = {
    "Election Day": "election_day",
    "Election Day Votes": "election_day",
    "Absentee/Mail": "absentee",
    "Mail In": "absentee",
    "Mail-In/Absentee Votes": "absentee",
    "Mail-in / Absentee Votes": "absentee",
    "Absentee": "absentee",
    "Mail": "absentee",
    "Mail Voting": "absentee",
    "Provisional": "provisional",
    "Provisional Votes": "provisional",
    "Provisional Voting": "provisional",
}


def parse_detail_xml(county: str, xml_path: Path) -> list[dict]:
    p = clarify.Parser()
    p.parse(str(xml_path))

    # Aggregation key: (precinct, office, district, party, candidate).
    # Accumulate votes per vote-type into the breakdown columns.
    rows: dict[tuple, dict] = {}
    # Per-precinct registered voters (only one choice per precinct, party blank).
    rv_rows: dict[tuple, dict] = {}

    def get_cell(key, vt_name):
        row = rows[key]
        col = VOTE_TYPE_MAP.get(vt_name)
        if col is None:
            return
        if row[col] == "":
            row[col] = 0
        row[col] += 0  # placeholder; actual += done at call site

    for r in p.results:
        contest_text = r.contest.text
        # "BALLOTS CAST - DEMOCRATIC" / "BALLOTS CAST - REPUBLICAN" ->
        # office "Ballots Cast", candidate empty, party from suffix.
        if contest_text.upper().startswith("BALLOTS CAST"):
            suffix = contest_text.split("-", 1)[-1].strip().upper()
            party = "DEM" if suffix == "DEMOCRATIC" else ("REP" if suffix == "REPUBLICAN" else "")
            office = "Ballots Cast"
            district = ""
            if r.jurisdiction is None:
                continue  # skip county-level aggregate
            precinct = r.jurisdiction.name
            key = (precinct, office, district, party, "")
            row = rows.setdefault(key, {
                "county": county, "precinct": precinct, "office": office,
                "district": district, "party": party, "candidate": "",
                "votes": 0, "election_day": "", "absentee": "",
                "provisional": "",
            })
            # Each Ballots Cast VoteType (Election Day, Absentee/Mail,
            # Provisional) accumulates into the matching breakdown column
            # and the total. regVotersCounty is all-zero; skip it explicitly.
            vt = r.vote_type
            col = VOTE_TYPE_MAP.get(vt)
            if col is not None:
                if row[col] == "":
                    row[col] = 0
                row[col] += r.votes or 0
            row["votes"] = (row["votes"] or 0) + (r.votes or 0)
            continue
        # Skip "REGISTERED VOTERS" pseudo-contests.
        if contest_text.upper().startswith("REGISTERED VOTERS"):
            if r.jurisdiction is None:
                continue
            precinct = r.jurisdiction.name
            key = (precinct, "Registered Voters", "", "", "")
            rv = rv_rows.setdefault(key, {
                "county": county, "precinct": precinct, "office": "Registered Voters",
                "district": "", "party": "", "candidate": "",
                "votes": 0, "election_day": "", "absentee": "",
                "provisional": "",
            })
            # Clarity's "REGISTERED VOTERS" contest uses the regVotersCounty
            # VoteType whose Precinct votes are 0; the real count lives on
            # the jurisdiction's total_voters attribute.
            tv = getattr(r.jurisdiction, "total_voters", None)
            if tv:
                rv["votes"] = int(tv)
            continue
        office, district, party = _normalize_office(contest_text)
        if not office:
            continue
        # Resolve bare "STATE COMMITTEE" sentinel via the contest-party prefix.
        if office == "__STATE_COMMITTEE__":
            office = ("Member of Republican State Committee"
                      if party == "REP"
                      else "Member of Democratic State Committee")
        if r.choice is None:
            continue
        cand_text = r.choice.text or ""
        cand_party = (r.choice.party or "").upper()
        # For ballot questions (Yes/No), the choice's party is "YES"/"NO"
        # — keep the candidate as Yes/No and clear the party column.
        is_question = bool(getattr(r.contest, "is_question", False))
        if is_question or cand_text.strip().lower() in ("yes", "no"):
            party = ""
            candidate = cand_text.strip().capitalize()
        else:
            if not party and cand_party:
                party = cand_party
            candidate = _finalize_candidate(cand_text)
        if r.jurisdiction is None:
            continue
        precinct = r.jurisdiction.name
        vt = r.vote_type
        if vt == "regVotersCounty":
            continue
        key = (precinct, office, district, party, candidate)
        row = rows.setdefault(key, {
            "county": county, "precinct": precinct, "office": office,
            "district": district, "party": party, "candidate": candidate,
            "votes": 0, "election_day": "", "absentee": "",
            "provisional": "",
        })
        col = VOTE_TYPE_MAP.get(vt)
        if col is None:
            continue
        if row[col] == "":
            row[col] = 0
        row[col] += r.votes or 0
        row["votes"] += r.votes or 0

    out = list(rv_rows.values()) + list(rows.values())
    return out


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})


def main(argv: list[str]) -> None:
    if len(argv) != 4:
        sys.exit(
            f"Usage: {Path(argv[0]).name} <County> <detail.xml> <output.csv>"
        )
    county = argv[1]
    xml_path = Path(argv[2])
    out_path = Path(argv[3])
    if not xml_path.exists():
        sys.exit(f"Missing XML: {xml_path}")
    rows = parse_detail_xml(county, xml_path)
    write_csv(rows, out_path)
    offices = len({(r["office"], r["district"]) for r in rows})
    precincts = len({r["precinct"] for r in rows})
    print(
        f"Wrote {len(rows)} rows across {offices} contests / "
        f"{precincts} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)