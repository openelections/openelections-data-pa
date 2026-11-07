#!/usr/bin/env python3
"""Parse Armstrong County PA 2024 (April 23) Primary precinct results.

Source: "Armstrong PA 2024 Primary Precinct Results.zip" — 62 Hart
"Election Summary Report / Closed Primary" PDFs, one per precinct, each
opening with ``Summary for: All Contests, <Precinct>, All Tabulators, All
Counting Groups`` followed by a registration/turnout block and contest
sections of the form::

    PRESIDENT OF THE UNITED STATES (DEM) (Vote for 1)
    DEM
    Precincts Reported: 1 of 1 (100.00%)
    Candidate  Party  Election Day  Mail  Provisional  Total
    JOSEPH R BIDEN JR  DEM  32  29  0  61
    Total Votes  42  31  0  73
    Scatter  WRITE-IN  7  0  0  7
    Unresolved Write-In  0  0  0  0

Long contest headers wrap across two lines (e.g. ``DELEGATE TO THE
REPUBLICAN NATIONAL CONVENTION`` / ``15th CONGRESSIONAL DISTRICT (Vote for
3)``); delegate contests carry their party on a standalone ``DEM``/``REP``
line instead of title parens. This is a standalone sibling of
parsers/pa_armstrong_primary_2026_results_parser.py (same vendor/report
family, but this one reads pdftotext -layout text rather than natural_pdf).
2024-batch conventions: "Write-In Totals" for the scatter write-in rows
(Scatter + Scattered merged), mail votes reported in the ``absentee``
column, Registered Voters / Ballots Cast rows included.

Usage:
    uv run python parsers/pa_armstrong_primary_2024_results_parser.py \
        <input_dir_or_file...> <output.csv>

Input items may be .pdf files (converted with pdftotext -layout) or .txt
files (pdftotext output); a directory is expanded to its PDFs.
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from electionware_primary_np import (
    STATEWIDE_OFFICES,
    _finalize_candidate,
)

COUNTY = "Armstrong"

# 2024 primary batch CSV convention (Adams/Chester/Venango): mail votes are
# reported in the ``absentee`` column; ``provisional`` precedes it.
FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "provisional",
    "absentee",
]

# Sentiment-free sentinel for Scatter WRITE-IN rows, renamed to
# "Write-In Totals" after the merge pass.
WRITE_IN_KEY = "__WRITE_IN__"

SUMMARY_RE = re.compile(
    r"^Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators"
)
BALLOTS_CAST_RE = re.compile(r"^Ballots Cast:\s*([\d,]+)")

# Registration / turnout block rows:
#   "DEMOCRATIC   Election Day   43   43   14.01%"
#   "             Mail           31   31   10.10%"
#   "             Total          74   74   307  24.10%"   (3rd number = RV)
GROUP_RE = re.compile(
    r"^(DEMOCRATIC|REPUBLICAN|Total)\s+(Election Day|Mail|Provisional|Total)\s+"
    r"([\d,]+)\s+([\d,]+)"
)
GROUP_CONTINUATION_RE = re.compile(
    r"^(Election Day|Mail|Provisional|Total)\s+([\d,]+)\s+([\d,]+)"
)

# Contest header with the ballot party in parens:
#   "PRESIDENT OF THE UNITED STATES (DEM) (Vote for 1)"              (one line)
#   "REPRESENTATIVE IN CONGRESS 15th CONGRESSIONAL DISTRICT (DEM)"   (wrapped)
CONTEST_PARTY_RE = re.compile(
    r"^(.+?)\s+\((DEM|REP|DEMOCRATIC|REPUBLICAN)\)"
    r"(?:\s*\(Vote for\s+\d+\))?\s*$"
)
VOTE_FOR_SUFFIX_RE = re.compile(r"\s*\(Vote for\s+\d+\)\s*$")

PARTY_LINE_RE = re.compile(r"^(DEM|REP|DEMOCRATIC|REPUBLICAN)$")
PARTY_NORMALIZE = {"DEMOCRATIC": "DEM", "DEM": "DEM", "REPUBLICAN": "REP", "REP": "REP"}

# District ordinals inside office titles ("15th CONGRESSIONAL DISTRICT",
# "41st SENATORIAL DISTRICT", "63th LEGISLATIVE DISTRICT" — source typo in
# the REP State House title, normalized to 63 like the DEM one).
DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+"
    r"(?:LEGISLATIVE\s+|SENATORIAL\s+|CONGRESSIONAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

DELEGATE_OFFICES = {
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION":
        "Delegate to the Democratic National Convention",
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        "Delegate to the Republican National Convention",
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        "Alternate Delegate to the Republican National Convention",
}
DELEGATE_START_RE = re.compile(
    r"^(DELEGATE TO THE|ALTERNATE DELEGATE TO THE)\b", re.IGNORECASE
)

# Candidate rows: name, party, election_day, mail, provisional, total
CANDIDATE_RE = re.compile(
    r"^(.+?)\s+(WRITE-IN|DEM|REP|GP|GRN|GREEN|LBR|LIB|IND|CON|NON|NONPARTISAN)\s+"
    r"([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)
TOTAL_VOTES_RE = re.compile(r"^Total Votes\b", re.IGNORECASE)
UNRESOLVED_RE = re.compile(r"^Unresolved Write-In\b", re.IGNORECASE)
SCATTER_RE = re.compile(r"^(Scatter|Scattered)\s+WRITE-IN\b", re.IGNORECASE)

SKIP_LINE_STARTS = (
    "Page:",
    "Precincts Reported:",
    "Election Summary Report",
    "Closed Primary",
    "Armstrong County",
    "April 23, 2024",
    "2024 General Primary Election",
    "Elector Group",
    "Candidate",
    "Election Day",
    "All Counting Groups",
)


def _normalize_office(raw_office: str) -> tuple[str, str]:
    """Return (office, district) for a raw contest office text (party parens
    and "(Vote for N)" already stripped)."""
    upper = re.sub(r"\s+", " ", raw_office.upper().strip())
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    for key, norm in DELEGATE_OFFICES.items():
        if upper.startswith(key):
            return (norm, district)
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


def _is_caps_fragment(line: str) -> bool:
    """True if a line is bare UPPER-CASE text apart from ordinal suffixes
    ("60th", "15th") — i.e. a contest-title fragment, not data."""
    stripped = re.sub(r"\d+(st|nd|rd|th)\b", "N", line)
    return stripped == stripped.upper()


def pdf_to_text(pdf_path: Path) -> str:
    """Run pdftotext -layout into a temp file and return its text."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp_name = tmp.name
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), tmp_name],
            check=True,
            capture_output=True,
        )
        return Path(tmp_name).read_text(errors="replace")
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _ints_in(text: str) -> list[int]:
    """Numeric tokens on a line, ignoring percentage tokens ("24.10%")."""
    no_pct = re.sub(r"\d+\.\d+%?", " ", text)
    return [int(c.replace(",", "")) for c in re.findall(r"[\d,]+", no_pct)]


def parse_text(text: str, merged: dict, warnings: list[str]) -> None:
    """Parse one precinct report into `merged`.

    Keys are (precinct, office, district, party, candidate_or_key); values
    are [votes, election_day, provisional, absentee].
    """
    state: dict = {
        "precinct": None,
        "groups": {},
        "group_order": [],
        "ballots_cast_total": None,
        "registrar_emitted": False,
        "office": "",
        "district": "",
        "party": "",
        "pending_title": [],
        "pending_party": "",
    }

    def add(row_key, votes, ed, mail, prov):
        rec = merged.get(row_key)
        if rec is None:
            merged[row_key] = [votes, ed, prov, mail]
        else:
            rec[0] += votes
            rec[1] += ed
            rec[2] += prov
            rec[3] += mail

    def set_group(group_name, counting_group, counts):
        g = state["groups"].setdefault(
            group_name, {"ed": 0, "mail": 0, "prov": 0, "total": 0, "rv": 0}
        )
        if counting_group == "total":
            g["total"] = counts[0]
            if len(counts) >= 3:
                g["rv"] = counts[2]
        else:
            g[{"election day": "ed", "mail": "mail",
               "provisional": "prov"}[counting_group]] = counts[0]
        state["group_order"].append(group_name)

    def emit_registrar_rows():
        if state["registrar_emitted"]:
            return
        state["registrar_emitted"] = True
        precinct = state["precinct"]
        group = state["groups"].get("TOT")
        if not group:
            warnings.append(f"{precinct}: no turnout block found")
            return
        add((precinct, "Ballots Cast", "", "", ""),
            group["total"], group["ed"], group["mail"], group["prov"])
        if group["rv"]:
            add((precinct, "Registered Voters", "", "", ""),
                group["rv"], 0, 0, 0)
        printed = state["ballots_cast_total"]
        if printed is not None and printed != group["total"]:
            warnings.append(
                f"{precinct}: turnout-block ballots {group['total']} != "
                f"printed Ballots Cast {printed}"
            )

    def finalize_contest(office_text, party_in_title):
        text = office_text.strip()
        # A trailing "(DEM)"/"(REP)" or "(Vote for N)" may have been joined in
        # from a wrapped continuation line.
        pm = re.search(r"\((\s*(?:DEMOCRATIC|REPUBLICAN)\s*)\)\s*$", text,
                       re.IGNORECASE)
        if pm:
            if not party_in_title:
                party_in_title = pm.group(1)
            text = text[: pm.start()].strip()
        text = VOTE_FOR_SUFFIX_RE.sub("", text).strip()
        office, district = _normalize_office(text)
        party = (PARTY_NORMALIZE.get(party_in_title.upper(), "")
                 if party_in_title else "")
        state["office"] = office
        state["district"] = district
        state["party"] = party
        emit_registrar_rows()

    lines = text.split("\n")
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(SKIP_LINE_STARTS):
            continue

        sm = SUMMARY_RE.match(line)
        if sm:
            state["precinct"] = sm.group(1).strip()
            state["groups"] = {}
            state["group_order"] = []
            state["ballots_cast_total"] = None
            state["registrar_emitted"] = False
            state["office"] = ""
            state["district"] = ""
            state["party"] = ""
            state["pending_title"] = []
            state["pending_party"] = ""
            continue

        precinct = state["precinct"]
        if precinct is None:
            continue

        bm = BALLOTS_CAST_RE.match(line)
        if bm:
            state["ballots_cast_total"] = int(bm.group(1).replace(",", ""))
            continue

        # --- Registration / turnout block (before the first contest) -------
        if not state["office"]:
            gm = GROUP_RE.match(line)
            if gm:
                group_name = ("TOT" if gm.group(1) == "Total"
                              else PARTY_NORMALIZE[gm.group(1)])
                set_group(group_name, gm.group(2).lower(), _ints_in(line))
                continue
            km = GROUP_CONTINUATION_RE.match(line)
            if km and state["groups"]:
                last = state["group_order"][-1] if state["group_order"] else None
                if last is not None:
                    set_group(last, km.group(1).lower(), _ints_in(line))
                continue

        # --- Pending wrapped contest title --------------------------------
        if state["pending_title"]:
            pending = state["pending_title"]
            if VOTE_FOR_SUFFIX_RE.search(line):
                joined = " ".join(pending + [line])
                office_part = VOTE_FOR_SUFFIX_RE.sub("", joined)
                finalize_contest(office_part.strip(), state["pending_party"])
                state["pending_title"] = []
                state["pending_party"] = ""
                continue
            if (_is_caps_fragment(line)
                    and not re.search(r"\d\s+\d", line)
                    and not PARTY_LINE_RE.match(line)):
                pending.append(line)
                continue
            state["pending_title"] = []
            state["pending_party"] = ""
            # fall through: process this line on its own merits

        office = state["office"]
        if office:
            if UNRESOLVED_RE.match(line):
                continue

            if SCATTER_RE.match(line):
                counts = _ints_in(line)
                if len(counts) == 4:
                    add((precinct, office, state["district"], state["party"],
                         WRITE_IN_KEY),
                        counts[3], counts[0], counts[1], counts[2])
                else:
                    warnings.append(
                        f"{precinct} / {office}: scatter row {counts}")
                continue

            pm = PARTY_LINE_RE.match(line)
            if pm:
                if not state["party"]:
                    state["party"] = PARTY_NORMALIZE[pm.group(1)]
                continue

            cm = CANDIDATE_RE.match(line)
            if cm:
                counts = [int(cm.group(i).replace(",", "")) for i in range(3, 7)]
                candidate = _finalize_candidate(cm.group(1).strip())
                # Hart prints hyphenated surnames ALL CAPS ("BRADFORD-GREY");
                # re-capitalize the segment after a hyphen.
                candidate = re.sub(
                    r"-(\w)", lambda m: "-" + m.group(1).upper(), candidate
                )
                add((precinct, office, state["district"], state["party"],
                     candidate),
                    counts[3], counts[0], counts[1], counts[2])
                continue

        # --- New contest headers -------------------------------------------
        cph = CONTEST_PARTY_RE.match(line)
        if cph and VOTE_FOR_SUFFIX_RE.search(line):
            finalize_contest(cph.group(1).strip(), cph.group(2))
            continue
        if cph:
            # Party parens present but "(Vote for N)" may be on the next line
            state["pending_title"] = [cph.group(1).strip()]
            state["pending_party"] = cph.group(2)
            continue
        if DELEGATE_START_RE.match(line):
            state["pending_title"] = [line]
            state["pending_party"] = ""
            continue
        # Generic all-caps fragment start (e.g. "REPRESENTATIVE IN THE GENERAL
        # ASSEMBLY 63rd LEGISLATIVE" whose continuation is
        # "DISTRICT (DEM) (Vote for 1)"); candidate data rows and metadata are
        # excluded (they never look like bare all-caps text).
        if (_is_caps_fragment(line)
                and not re.search(r"\d\s+\d", line)
                and not PARTY_LINE_RE.match(line)
                and not VOTE_FOR_SUFFIX_RE.search(line)):
            state["pending_title"] = [line]
            state["pending_party"] = ""
            continue


def write_csv(rows: list[tuple], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for (precinct, office, district, party, candidate), vals in rows:
            candidate = "Write-In Totals" if candidate == WRITE_IN_KEY else candidate
            w.writerow([COUNTY, precinct, office, district, party, candidate,
                       *vals])


def _sort_key(item):
    (precinct, office, district, party, candidate), _vals = item
    return (precinct, office, int(district) if district else 0, party,
            candidate)


def collect_input_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            files.extend(sorted(path.glob("*.pdf")))
        elif path.exists():
            files.append(path)
        else:
            sys.exit(f"Input not found: {p}")

    def numeric_key(path: Path) -> tuple[int, str]:
        m = re.match(r"^(\d+)", path.stem.strip())
        return (int(m.group(1)) if m else 10**9, path.name)

    return sorted(files, key=numeric_key)


def main(argv: list[str]) -> None:
    if len(argv) < 3:
        sys.exit(
            "Usage: pa_armstrong_primary_2024_results_parser.py "
            "<input_dir_or_file...> <output.csv>"
        )
    out_path = Path(argv[-1])
    files = collect_input_files(argv[1:-1])
    merged: dict = {}
    warnings: list[str] = []
    for path in files:
        text = pdf_to_text(path) if path.suffix.lower() == ".pdf" else path.read_text()
        parse_text(text, merged, warnings)
    rows = sorted(merged.items(), key=_sort_key)
    write_csv(rows, out_path)

    precincts = len({key[0] for key in merged})
    contests = len({(key[1], key[2], key[3]) for key in merged
                    if key[1] not in ("Registered Voters", "Ballots Cast")})
    print(f"Wrote {len(rows)} rows across {contests} contests / "
          f"{precincts} precincts to {out_path}")
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
