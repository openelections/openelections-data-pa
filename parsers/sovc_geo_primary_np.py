"""Primary-mode SOVC-by-geography parser for PA 2024 primary precinct PDFs.

Sibling of ``sovc_geo_np`` (which handles general elections). PA primary
SOVC reports put the party in parentheses on the contest line, e.g.::

    PRESIDENT OF THE UNITED STATES (DEMOCR) (Vote for 1)
    58 ballots (...), 193 registered voters, turnout 30.05%
    Joseph R. Biden Jr. 49 90.74% 23 26 0
    ...
    Total 54 100.00% 26 28 0
    Overvotes 0
    Undervotes 4

while general-election SOVC reports carry the party on each candidate row.
This parser extracts the party from the contest header and applies it to
every candidate row beneath, normalizes the office name via the shared
``STATEWIDE_OFFICES`` table, and writes the 2024-primary CSV schema
(``county, precinct, office, district, party, candidate, votes,
election_day, provisional, absentee``).

Counties: Wayne, Lycoming (and other 2024 primary SOVC-by-geography PDFs
that share the contest-header shape).
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import natural_pdf as npdf

from electionware_primary_np import (
    STATEWIDE_OFFICES,
    DISTRICT_ORDINAL_RE,
    PRIMARY_FIELDNAMES,
    _finalize_candidate,
)

# Contest header: "PRESIDENT OF THE UNITED STATES (DEMOCR) (Vote for 1)"
# or "President of the United States (Dem) (Vote for 1)". Party is captured
# as the raw abbreviation; the office text is captured separately.
PARTY_ABBR = (
    "DEMOCR|DEM|REPUBL|REP|NONPARTISAN|NON|GP|GREEN|LBR|LIB|CON|CONSTITUTIONAL"
)
CONTEST_RE = re.compile(
    rf"^(.+?)\s*\(\s*({PARTY_ABBR})\s*\)\s*\(Vote for\s+(\d+)\)"
    r"(?:[,\s].*)?$",
    re.IGNORECASE,
)

# Ballots line (Wayne): "58 ballots (0 over voted ballots, 0 overvotes, 4
# undervotes), 193 registered voters, turnout 30.05%". We extract registered
# voters to emit a per-precinct Registered Voters row (once per precinct).
BALLOTS_RE = re.compile(
    r"^\d+\s+ballots\s*\(.*?\),\s*(\d[\d,]*)\s+registered voters",
    re.IGNORECASE,
)

# Candidate data line: "Joseph R. Biden Jr. 49 90.74% 23 26 0"
DATA_LINE_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s+[\d.]+%\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$"
)

# Variant without the vote% token: "Joseph R. Biden, Jr. 245 72 172 1" (Bucks).
DATA_LINE_NO_PCT_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$"
)

PRECINCT_RE = re.compile(r"^Precinct\s+(.+)$")

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


def _normalize_party(raw: str) -> str:
    return PARTY_NORMALIZE.get(raw.upper(), raw.upper())


def _normalize_office(raw_office: str) -> tuple[str, str]:
    """Return (office, district) for a raw contest office text (party parens
    already stripped by CONTEST_RE). Falls back to title-case for local
    offices not in the statewide table."""
    # Wayne 2026 appends a party suffix to the office name (e.g.
    # "GOVERNOR-D (DEMOCR)", "REPRESENTATIVE IN CONGRESS-R (REPUBL)"). Strip
    # the trailing -D/-R/-I so STATEWIDE_OFFICES lookup succeeds.
    raw_office = re.sub(r"-(D|R|I)$", "", raw_office, flags=re.IGNORECASE)
    upper = raw_office.upper()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    # Wayne 2026 drops the word "DISTRICT" — match a bare ordinal suffix
    # ("SENATOR IN THE GENERAL ASSEMBLY 20TH") when the canonical form fails.
    if not dm:
        dm = re.search(r"\b(\d+)(?:ST|ND|RD|TH)\b\s*$", upper)
    district = str(int(dm.group(1))) if dm else ""
    office_key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    # Strip bare ordinal too (Wayne 2026).
    office_key = re.sub(r"\s+\d+(?:ST|ND|RD|TH)\s*$", "", office_key, flags=re.IGNORECASE).strip()
    # "(15th District)" style — strip and capture district.
    paren_dist = re.search(r"\((\d+)(?:st|nd|rd|th)?\s+District\)", office_key, re.IGNORECASE)
    if paren_dist and not district:
        district = str(int(paren_dist.group(1)))
        office_key = re.sub(r"\(\d+(?:st|nd|rd|th)?\s+District\)", "", office_key, flags=re.IGNORECASE).strip()
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


@dataclass
class PrimarySovcConfig:
    county: str
    skip_prefixes: tuple[str, ...] = ()
    precinct_re: "re.Pattern" = PRECINCT_RE
    # Exact-match line that marks the start of a countywide roll-up section
    # appearing AFTER all real "Precinct <name>" sections (e.g. Wayne's
    # standalone "All Precincts" line). When seen, current_precinct is
    # cleared so roll-up rows are dropped rather than misattributed.
    countywide_marker: Optional[str] = None
    # Whether to emit a per-precinct Registered Voters row from the ballots
    # line. Wayne has ballots lines; Lycoming does not.
    emit_registered_voters: bool = True
    # Contest-name prefixes to drop entirely (the contest line, its ballots
    # line, and all candidate rows beneath). Used for Wayne's
    # "MEMBER OF REPUBLICAN COUNTY COMMITTEE" block, which is organized by
    # precinct-name-in-contest-title rather than by "Precinct <name>" headers,
    # so the engine can't attribute rows correctly.
    contest_skip_prefixes: tuple[str, ...] = ()
    # Substrings to drop when found anywhere in the contest office text.
    # Used for Bucks' "Warwick Twp Dist 4 Committeeman (Rep) (Vote for 1)"
    # where the precinct name is embedded mid-title.
    contest_skip_contains: tuple[str, ...] = ()
    # Override the data-line regex. Defaults to DATA_LINE_RE (with vote%).
    # Bucks uses DATA_LINE_NO_PCT_RE (no vote% token).
    data_line_re: "re.Pattern" = DATA_LINE_RE


def parse_primary_sovc_text(text: str, config: PrimarySovcConfig, state: Optional[dict] = None) -> list[dict]:
    rows: list[dict] = []
    if state is None:
        state = {
            "current_precinct": None,
            "current_office": "",
            "current_district": "",
            "current_party": "",
            "seen_precincts": set(),
            "precinct_party_rv": {},
        }
    current_precinct = state["current_precinct"]
    current_office = state["current_office"]
    current_district = state["current_district"]
    current_party = state["current_party"]
    seen_precincts = state["seen_precincts"]
    precinct_party_rv: dict = state["precinct_party_rv"]
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if config.countywide_marker and line == config.countywide_marker:
            current_precinct = None
            continue
        if any(line.startswith(p) for p in config.skip_prefixes):
            continue
        pm = config.precinct_re.match(line)
        if pm:
            current_precinct = pm.group(1).strip()
            current_office = ""
            continue
        if current_precinct is None:
            continue
        if any(line.upper().startswith(p.upper()) for p in config.contest_skip_prefixes):
            current_office = ""
            state["skip_contest"] = True
            continue
        cm = CONTEST_RE.match(line)
        if cm:
            raw_office = cm.group(1).strip()
            if any(s.upper() in raw_office.upper() for s in config.contest_skip_contains):
                current_office = ""
                state["skip_contest"] = True
                continue
            state["skip_contest"] = False
            current_party = _normalize_party(cm.group(2))
            current_office, current_district = _normalize_office(raw_office)
            continue
        bm = BALLOTS_RE.match(line)
        bm = BALLOTS_RE.match(line)
        if bm:
            if config.emit_registered_voters and not state.get("skip_contest", False):
                rv = int(bm.group(1).replace(",", ""))
                d = precinct_party_rv.setdefault(current_precinct, {})
                d[current_party] = rv
            continue
        if not current_office:
            continue
        dm2 = config.data_line_re.match(line)
        if not dm2:
            continue
        name = dm2.group(1).strip()
        total = int(dm2.group(2).replace(",", ""))
        ed = int(dm2.group(3).replace(",", ""))
        mi = int(dm2.group(4).replace(",", ""))
        pr = int(dm2.group(5).replace(",", ""))
        if name == "Total":
            continue
        if name == "Write-in":
            rows.append({
                "county": config.county, "precinct": current_precinct,
                "office": current_office, "district": current_district,
                "party": current_party, "candidate": "Write-In Totals",
                "votes": total, "election_day": ed, "provisional": pr, "absentee": mi,
            })
            continue
        if name == "Overvotes":
            rows.append({
                "county": config.county, "precinct": current_precinct,
                "office": current_office, "district": current_district,
                "party": current_party, "candidate": "Overvotes",
                "votes": total, "election_day": ed, "provisional": pr, "absentee": mi,
            })
            continue
        if name == "Undervotes":
            rows.append({
                "county": config.county, "precinct": current_precinct,
                "office": current_office, "district": current_district,
                "party": current_party, "candidate": "Undervotes",
                "votes": total, "election_day": ed, "provisional": pr, "absentee": mi,
            })
            continue
        rows.append({
            "county": config.county, "precinct": current_precinct,
            "office": current_office, "district": current_district,
            "party": current_party, "candidate": _finalize_candidate(name),
            "votes": total, "election_day": ed, "provisional": pr, "absentee": mi,
        })
    state["current_precinct"] = current_precinct
    state["current_office"] = current_office
    state["current_district"] = current_district
    state["current_party"] = current_party
    state["seen_precincts"] = seen_precincts
    state["precinct_party_rv"] = precinct_party_rv
    return rows


def _flush_registered_voters(precinct_party_rv: dict, county: str) -> list[dict]:
    """Emit one Registered Voters row per precinct, summing party-specific
    RV numbers (PA primary SOVC reports a separate registered-voter count
    per party contest)."""
    out: list[dict] = []
    for precinct, parties in precinct_party_rv.items():
        total = sum(parties.values())
        out.append({
            "county": county, "precinct": precinct,
            "office": "Registered Voters", "district": "", "party": "",
            "candidate": "", "votes": total,
            "election_day": "", "provisional": "", "absentee": "",
        })
    return out


def parse_primary_sovc_pdf(pdf_path: Path, config: PrimarySovcConfig) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    state: dict = {
        "current_precinct": None,
        "current_office": "",
        "current_district": "",
        "current_party": "",
        "seen_precincts": set(),
        "precinct_party_rv": {},
    }
    for page in pdf.pages:
        text = page.extract_text() or ""
        rows.extend(parse_primary_sovc_text(text, config, state))
    if config.emit_registered_voters:
        rv_rows = _flush_registered_voters(state["precinct_party_rv"], config.county)
        # Interleave RV rows at the start of each precinct's block for readability.
        by_precinct: dict[str, list[dict]] = {}
        for r in rows:
            by_precinct.setdefault(r["precinct"], []).append(r)
        ordered: list[dict] = []
        for rv in rv_rows:
            ordered.append(rv)
            ordered.extend(by_precinct.get(rv["precinct"], []))
        return ordered
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRIMARY_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in PRIMARY_FIELDNAMES})


def run_cli(config: PrimarySovcConfig, argv: Optional[list[str]] = None) -> None:
    argv = list(argv) if argv is not None else sys.argv
    if len(argv) != 3:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_primary_sovc_pdf(pdf_path, config)
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")