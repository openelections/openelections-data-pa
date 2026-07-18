#!/usr/bin/env python3
"""Parse Lehigh County PA 2026 Primary precinct + county CSVs.

Lehigh publishes two official CSV exports:

  * ``Lehigh County Precincts_12.csv`` — per-precinct candidate totals only
    (no vote-method breakdown). Columns: Precinct, Contest Name, Candidate
    Name, Votes, Voter Turnout.
  * ``Lehigh County summary_12.csv`` — county-wide candidate totals with
    Mail / Election Day / Provisional breakdown. Columns: Contest Name,
    Candidate Name, Party, Mail Ballots Votes, Election Day Votes,
    Provisional Votes, Total Votes, Number Of Precincts, Precincts Reported,
    Ballots Cast, Under Votes, Vote For.

Both files share a header preamble (``Official Election Results`` /
``LEHIGH``) followed by the column header. Party is encoded differently in
each file: the precinct CSV puts a ``DEM``/``REP`` prefix on party-primary
contests (``DEM Governor``) but NOT on cross-party contests
(``Representative in Congress in the 7th Congressional Dist``), so we build
a ``(contest, candidate) -> party`` lookup from the summary CSV (which has
an explicit Party column) and fall back to the contest-name prefix.

Usage:
    uv run python parsers/pa_lehigh_primary_2026_results_parser.py \
        <precincts.csv> <summary.csv> <precinct_out.csv> <county_out.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from electionware_primary_np import STATEWIDE_OFFICES, _finalize_candidate

# "DEM Governor" / "REP Representative in Congress ..." — leading party token.
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP|GP|GRN|LBR|CON|NON|IND)\s+(.+)$")

# Lehigh writes districts as "in the 7th Congressional Dist",
# "in the 16th Senatorial D", "in the 187th Legis" — trailing abbreviations.
DISTRICT_RE = re.compile(
    r"\bin\s+the\s+(\d+)(?:ST|ND|RD|TH)?\s+"
    r"(?:CONGRESSIONAL|SENATORIAL|LEGI)\w*"
    r"(?:\s+DIST\.?|\s+D\.?)?\s*$",
    re.IGNORECASE,
)

# Per-county committee races and ballot questions — not carried.
SKIP_CONTEST_PREFIXES = (
    "DEMOCRATIC COMMITTEE",
    "REPUBLICAN COMMITTEE",
    "COMMITTEEPERSON",
    "COMMITTEEMAN",
    "COMMITTEEWOMAN",
    "MEMBER OF THE COUNTY COMMITTEE",
    "MEMBER OF COUNTY COMMITTEE",
)
SKIP_CONTEST_KEYWORDS = ("REFERENDUM", "QUESTION")

PRECINCT_FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "provisional", "absentee",
]
COUNTY_FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]


def _normalize_office(contest: str) -> tuple[str, str, str]:
    """Return (office, district, party_from_prefix_or_empty).

    Strips a leading party prefix when present; extracts an ordinal
    district when the contest has an "in the Nth Congressional/Senatorial/
    Legislative" suffix.
    """
    upper = contest.upper()
    if any(k in upper for k in SKIP_CONTEST_KEYWORDS):
        return ("", "", "")
    pm = PARTY_PREFIX_RE.match(contest)
    party = ""
    rest = contest
    if pm:
        party = pm.group(1).upper()
        rest = pm.group(2)
    # Per-precinct committee races are prefixed ("DEM Member of the County
    # Committee ...") — check the skip table after stripping the prefix.
    rest_upper = rest.upper()
    if any(rest_upper.startswith(p) for p in SKIP_CONTEST_PREFIXES):
        return ("", "", "")
    # Member of State Committee — party in the office name.
    if "MEMBER OF THE DEMOCRATIC STATE COMMITTEE" in rest.upper() or \
       "MEMBER OF DEMOCRATIC STATE COMMITTEE" in rest.upper():
        return ("Member of Democratic State Committee", "", "DEM")
    if "MEMBER OF THE REPUBLICAN STATE COMMITTEE" in rest.upper() or \
       "MEMBER OF REPUBLICAN STATE COMMITTEE" in rest.upper():
        return ("Member of Republican State Committee", "", "REP")
    # Strip Lehigh's "in the Nth ... Dist" suffix -> district + office key.
    dm = DISTRICT_RE.search(rest)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_RE.sub("", rest, count=1).strip() if dm else rest
    key_upper = re.sub(r"\s+", " ", key.upper()).strip()
    if key_upper in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key_upper]
        return (norm, district if extract else "", party)
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key_upper == k or key_upper.startswith(k + " "):
            return (norm, district if extract else "", party)
    # Fall through: title-case the contest text (minus prefix).
    return (rest.title(), district, party)


def _read_csv_lines(path: Path) -> list[list[str]]:
    """Read a Lehigh CSV, skipping the two-line preamble (BOM + title + county).

    Lehigh's CSVs start with a UTF-8 BOM and two banner lines before the
    actual header row.
    """
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = [r for r in reader if any(c.strip() for c in r)]
    # Drop the two banner rows ("Official Election Results", "LEHIGH").
    if rows and rows[0] and rows[0][0].strip() == "Official Election Results":
        rows = rows[1:]
    if rows and rows[0] and rows[0][0].strip() == "LEHIGH":
        rows = rows[1:]
    return rows


def _build_party_lookup(summary_rows: list[list[str]]) -> dict[tuple[str, str], str]:
    """(contest, candidate_upper) -> party from the summary CSV."""
    header = summary_rows[0]
    idx = {col.strip(): i for i, col in enumerate(header)}
    ci, ni, pi = idx["Contest Name"], idx["Candidate Name"], idx["Party"]
    lookup: dict[tuple[str, str], str] = {}
    for r in summary_rows[1:]:
        if len(r) < max(ci, ni, pi) + 1:
            continue
        contest = r[ci].strip()
        name = r[ni].strip()
        party = r[pi].strip().upper()
        if not contest or not name or contest == "Total Precincts Reported":
            continue
        lookup[(contest, name.upper())] = party
    return lookup


def parse_precincts(
    path: Path, party_lookup: dict[tuple[str, str], str]
) -> list[dict]:
    rows_in = _read_csv_lines(path)
    header = rows_in[0]
    idx = {col.strip(): i for i, col in enumerate(header)}
    pi, ci, ni, vi = idx["Precinct"], idx["Contest Name"], idx["Candidate Name"], idx["Votes"]
    out: list[dict] = []
    for r in rows_in[1:]:
        if len(r) < max(pi, ci, ni, vi) + 1:
            continue
        precinct = r[pi].strip()
        contest = r[ci].strip()
        name = r[ni].strip()
        votes_raw = r[vi].strip()
        if not precinct or not contest or not name:
            continue
        if contest == "Total Precincts Reported":
            continue
        office, district, prefix_party = _normalize_office(contest)
        if not office:
            continue
        party = party_lookup.get((contest, name.upper())) or prefix_party
        if name.upper() in ("WRITE-IN", "WRITEIN", "WRITE IN"):
            candidate = "Write-In Totals"
        elif name.upper() == "SCATTERED":
            candidate = "Scattered"
        else:
            candidate = _finalize_candidate(name)
        try:
            votes = int(votes_raw.replace(",", ""))
        except ValueError:
            votes = ""
        out.append({
            "county": "Lehigh", "precinct": precinct, "office": office,
            "district": district, "party": party, "candidate": candidate,
            "votes": votes, "election_day": "", "provisional": "", "absentee": "",
        })
    return out


def parse_summary(path: Path) -> list[dict]:
    rows_in = _read_csv_lines(path)
    header = rows_in[0]
    idx = {col.strip(): i for i, col in enumerate(header)}
    ci = idx["Contest Name"]
    ni = idx["Candidate Name"]
    pi = idx["Party"]
    mi = idx["Mail Ballots Votes"]
    ei = idx["Election Day Votes"]
    pri = idx["Provisional Votes"]
    ti = idx["Total Votes"]
    out: list[dict] = []
    for r in rows_in[1:]:
        if len(r) < max(ci, ni, pi, mi, ei, pri, ti) + 1:
            continue
        contest = r[ci].strip()
        name = r[ni].strip()
        party_raw = r[pi].strip().upper()
        if not contest or not name or contest == "Total Precincts Reported":
            continue
        office, district, prefix_party = _normalize_office(contest)
        if not office:
            continue
        party = party_raw or prefix_party
        if name.upper() in ("WRITE-IN", "WRITEIN", "WRITE IN"):
            candidate = "Write-In Totals"
        elif name.upper() == "SCATTERED":
            candidate = "Scattered"
        else:
            candidate = _finalize_candidate(name)
        def _i(s: str) -> int:
            try:
                return int(s.replace(",", ""))
            except (ValueError, AttributeError):
                return 0
        out.append({
            "county": "Lehigh", "office": office, "district": district,
            "party": party, "candidate": candidate,
            "votes": _i(r[ti]), "election_day": _i(r[ei]),
            "mail": _i(r[mi]), "provisional": _i(r[pri]),
        })
    return out


def write_csv(rows: list[dict], out_path: Path, fieldnames: list[str]) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main(argv: list[str]) -> None:
    if len(argv) != 5:
        sys.exit(
            f"Usage: {Path(argv[0]).name} "
            "<precincts.csv> <summary.csv> <precinct_out.csv> <county_out.csv>"
        )
    precinct_in = Path(argv[1])
    summary_in = Path(argv[2])
    precinct_out = Path(argv[3])
    county_out = Path(argv[4])
    if not precinct_in.exists():
        sys.exit(f"Missing precinct CSV: {precinct_in}")
    if not summary_in.exists():
        sys.exit(f"Missing summary CSV: {summary_in}")
    summary_rows = _read_csv_lines(summary_in)
    party_lookup = _build_party_lookup(summary_rows)
    precinct_rows = parse_precincts(precinct_in, party_lookup)
    county_rows = parse_summary(summary_in)
    write_csv(precinct_rows, precinct_out, PRECINCT_FIELDNAMES)
    write_csv(county_rows, county_out, COUNTY_FIELDNAMES)
    offices = len({(r["office"], r["district"]) for r in precinct_rows})
    precincts = len({r["precinct"] for r in precinct_rows})
    print(
        f"Wrote {len(precinct_rows)} precinct rows ({offices} contests / "
        f"{precincts} precincts) and {len(county_rows)} county rows"
    )


if __name__ == "__main__":
    main(sys.argv)