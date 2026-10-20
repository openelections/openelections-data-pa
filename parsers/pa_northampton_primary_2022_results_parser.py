#!/usr/bin/env python3
"""Northampton County PA 2022 primary (2022-05-17) — precinct results.

Source: "Northampton PA 2022 Primary Precinct Summary.pdf" (Electionware
"Precinct Summary Results Report", OFFICIAL RESULTS, generated 06/17/2022 —
post-recount vintage: its GOP U.S. Senate county totals match the ENR anchor
and the official Election Summary exactly).

Family: electionware_np (Electionware precinct summary), but primary-shaped:
each contest header carries the ballot party ("DEM United States Senator",
"REP Members of the Republican County Committee ..."), while candidate rows
are unprefixed and inherit the contest's party; the Statistics block reports
Registered Voters / Ballots Cast per party. So this is a standalone parser
that reuses the shared engine's precinct-block extractor
(parsers/electionware_precinct_np.py) with a primary-specific row parser.

Output (10-col header with breakdowns):
    county,precinct,office,district,party,candidate,votes,
    election_day,mail,provisional

Notes:
  - "Write-In: <name>" itemization rows are skipped; the contest's
    "Write-In Totals" aggregate is emitted as candidate "Write Ins" carrying
    the contest party (party-empty write-ins fail duplicate_entries in
    primaries), and page-split repeat aggregate rows for a contest are
    summed.
  - "Not Assigned" rows are skipped (part of the write-in accounting, not
    contest votes).
  - Registered Voters: one total row (party "") plus per-party rows
    (DEMOCRATIC/REPUBLICAN/NONPARTISAN -> DEM/REP/NONPARTISAN) —
    McKean/Northumberland 2022-primary precedent.
  - Ballots Cast: party "" total + per-party + "Ballots Cast - Blank".
  - U.S. House has no district in the printed header; all of Northampton
    was in congressional district 7 in 2022 (single contest per precinct;
    ENR anchor carries only district 7 rows): office "U.S. House",
    district "7".
  - County Committee contests are per-precinct party-internal races, kept
    (Allegheny/Washington/Lebanon 2022-primary precedent): office as
    printed, minus the party code — "Member of the Democratic County
    Committee" (district "Male/Female <municipality>") and "Member of the
    Republican County Committee" (district "<municipality>"; the Republican
    ballot style is unisex).
  - Member(s) of the (D|R) State Committee headers map to the anchor's
    exact office strings "Member of Democratic State Committee" /
    "Member of Republican State Committee".
  - East Allen Township Open Space Referendum Ordinance is a nonpartisan
    ballot question (Yes/No rows, party "").
  - Precinct names keep the printed "010001-1 ALLEN TWSP NORTHERN" form,
    matching the 2022-general county file convention in this repo.

Usage:
    python parsers/pa_northampton_primary_2022_results_parser.py \
        "<input.pdf>" "<output.csv>"
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from electionware_precinct_np import (
    ElectionwareConfig,
    VOTE_FOR_RE,
    VOTE_TAIL_RE,
    extract_precinct_blocks,
    parse_votes,
)

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

# Party code on a contest header line.
CONTEST_PARTY_RE = re.compile(
    r"^(DEM|REP|LIB|GRN|IND|LBR|CST|FWD|ASP|KEY|NP|NOA)\s+(.+)$", re.I
)

# Statistics rows. Registered Voters carry a single value; Ballots Cast
# carry total/election_day/mail/provisional.
STAT_SINGLE_RE = re.compile(
    r"^Registered Voters - (Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+"
    r"(\d[\d,]*)$"
)
STAT_TAIL_RE = re.compile(
    r"^Ballots Cast - (Total|Blank|DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$"
)

STATE_SENATE_RE = re.compile(
    r"^Senator in the General Assembly (\d+)(?:st|nd|rd|th) Senatorial District$"
)
STATE_HOUSE_RE = re.compile(
    r"^Representative in the General Assembly (\d+)(?:st|nd|rd|th)"
    r" Legislative District$"
)
COUNTY_COMMITTEE_DEM_RE = re.compile(
    r"^Member of the Democratic County Committee - (Male|Female)\s+(.+)$"
)
COUNTY_COMMITTEE_REP_RE = re.compile(
    r"^Members of the Republican County Committee\s+(.+)$"
)

EXACT_OFFICES = {
    "United States Senator": ("U.S. Senate", ""),
    "Governor": ("Governor", ""),
    "Lieutenant Governor": ("Lieutenant Governor", ""),
    "Representative in Congress": ("U.S. House", "7"),
    "Members of the Democratic State Committee": (
        "Member of Democratic State Committee",
        "",
    ),
    "Members of the Republican State Committee": (
        "Member of Republican State Committee",
        "",
    ),
}

REFERENDUM_HEADERS = {
    "East Allen Township Open Space Referendum Ordinance": (
        "East Allen Township Open Space Referendum",
        "East Allen Township",
    ),
}

STAT_PARTIES = {
    "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "NONPARTISAN": "NONPARTISAN",
}

_CC_COUNTERS: dict = {}

# Set by parse_precinct_rows so map_contest can pass the precinct name and
# the warning set through to _committee_muni.
_PRECINCT_REF = [None, None]


def _committee_muni(style: str, printed: str, precinct: str, warnings: set) -> str:
    """Full county-committee municipality label for this contest.

    The precinct summary truncates some labels (column width); the complete
    labels are listed per style in county-Election-Summary order, which
    matches the precinct PDF's document order (both alphabetize by
    precinct, one contest per precinct per style).
    """
    fulls = COMMITTEE_FULL_MUNIS[style].split("|")
    i = _CC_COUNTERS.get(style, 0)
    _CC_COUNTERS[style] = i + 1
    if i >= len(fulls):
        if warnings:
            warnings.add(f"{precinct}: {style} committee contest beyond known list")
        return printed
    full = fulls[i]
    if full == printed or (
        full.startswith(printed)
        and (len(full) == len(printed) or full[len(printed)] == " ")
    ):
        return full
    if warnings:
        warnings.add(
            f"{precinct}: {style} committee label {printed!r} does not match "
            f"expected {full!r} (position {i + 1})"
        )
    return printed


# The precinct summary truncates some county-committee contest labels
# (e.g. "Upper Mt. Bethel Twsp" for "Upper Mt. Bethel Twsp Centerville").
# Full labels, in county-Election-Summary order (alphabetical by precinct,
# identical to the precinct PDF's document order) — regenerate from the
# "Northampton PA 2022 Primary Election Summary.pdf" committee headers.
# Both reports list exactly one DEM Male / DEM Female / REP committee
# contest per precinct (154 each), so the k-th occurrence in the precinct
# PDF's document order pairs with the k-th full label below.
COMMITTEE_FULL_MUNIS = {
    "DEM Male": 'Allen Twsp North|Allen Twsp South|Bangor Boro 1st Ward|Bangor Boro 2nd Ward|Bangor Boro 3rd Ward|Bangor Boro 4th Ward|Bath Boro|Bethlehem 1-North|Bethlehem 1-South|Bethlehem 2nd Ward|Bethlehem 3rd Ward|Bethlehem 4th Ward|Bethlehem 5th Ward|Bethlehem 6th Ward|Bethlehem 7th Ward|Bethlehem 8th Ward|Bethlehem 9-1|Bethlehem 9-2|Bethlehem 9-3|Bethlehem 14-1|Bethlehem 14-2|Bethlehem 14-3|Bethlehem 14-4|Bethlehem 14-5|Bethlehem 14-6|Bethlehem 14-7|Bethlehem 14-8|Bethlehem 15-1|Bethlehem 15-2|Bethlehem 15-3|Bethlehem 16th Ward|Bethlehem 17th Ward|Bethlehem Twsp 1-1|Bethlehem Twsp 1-2|Bethlehem Twsp 1-3|Bethlehem Twsp 1-4|Bethlehem Twsp 2-1|Bethlehem Twsp 2-2|Bethlehem Twsp 2-3|Bethlehem Twsp 2-4|Bethlehem Twsp 3-1|Bethlehem Twsp 3-2|Bethlehem Twsp 3-3|Bethlehem Twsp 3-4|Bethlehem Twsp 3-5|Bethlehem Twsp 4-1|Bethlehem Twsp 4-2|Bethlehem Twsp 4-3|Bethlehem Twsp 4-4|Bushkill Twsp Bushkill Center|Bushkill Twsp Cherry Hill|Bushkill Twsp Clearfield|Chapman|East Allen Twsp East|East Allen Twsp West|East Bangor|Easton 2nd Ward|Easton 3-2|Easton 3-3|Easton 3-4|Easton 4th Ward|Easton 5th Ward|Easton 6th Ward|Easton 7th Ward|Easton 8th Ward East|Easton 8th Ward West|Easton 9th Ward|Easton 10th Ward East|Easton 10th Ward West|Easton 11th Ward|Easton 12th Ward|Forks Eastern 1|Forks Eastern 2|Forks Western 1|Forks Western 2|Freemansburg|Glendon|Hanover #1|Hanover #2|Hanover #3|Hanover #4|Hanover #5|Hanover #6|Hellertown 1st Ward|Hellertown 2nd Ward|Hellertown 3rd Ward|Lehigh Twsp Central|Lehigh Twsp Danielsville|Lehigh Twsp Pennsville|Lehigh Twsp Treichlers|Lehigh Twsp Northwestern|Lower Mt. Bethel Twsp Lower|Lower Mt. Bethel Twsp Upper|Lower Nazareth #1|Lower Nazareth #2|Lower Saucon Twsp 1|Lower Saucon Twsp 2|Lower Saucon Twsp 3|Lower Saucon Twsp 4|Lower Saucon Twsp 5|Lower Saucon Twsp 6|Lower Saucon Twsp 7|Lower Saucon Twsp 8|Moore Twsp Beersville|Moore Twsp Eastern|Moore Twsp Klecknersville|Moore Twsp Point Phillips|Nazareth 1st Ward|Nazareth 2nd Ward|Nazareth 3rd Ward|Northampton 1st Ward|Northampton 2nd Ward|Northampton 3rd Ward|Northampton 4th Ward|North Catasauqua 1|North Catasauqua 2|Palmer Eastern|Palmer Middle #1|Palmer Middle #2|Palmer Upper Eastern|Palmer Upper Western|Palmer Western #1|Palmer Western #2|Pen Argyl 1st Ward|Pen Argyl 2nd Ward|Pen Argyl 3rd Ward|Pen Argyl 4th Ward|Plainfield Twsp Belfast|Plainfield Twsp Delabole|Plainfield Twsp Kesslersville|Plainfield Twsp Plainfield Church|Portland Boro|Roseto Boro|Stockertown Boro|Tatamy Boro|Upper Mt. Bethel Twsp Centerville|Upper Mt. Bethel Twsp Johnsonville|Upper Mt. Bethel Twsp North Bangor|Upper Mt. Bethel Twsp Slateford|Upper Mt. Bethel Twsp Williamsburg|Upper Nazareth East|Upper Nazareth West|Walnutport|Washington Twsp Lower|Washington Twsp Upper|West Easton|Williams Twsp East|Williams Twsp Middle|Williams Twsp Upper|Williams Twsp West|Wilson Boro 1st Ward|Wilson Boro 2nd Ward|Wilson Boro 3rd Ward|Wind Gap Boro',
    "DEM Female": 'Allen Twsp North|Allen Twsp South|Bangor Boro 1st Ward|Bangor Boro 2nd Ward|Bangor Boro 3rd Ward|Bangor Boro 4th Ward|Bath Boro|Bethlehem 1-North|Bethlehem 1-South|Bethlehem 2nd Ward|Bethlehem 3rd Ward|Bethlehem 4th Ward|Bethlehem 5th Ward|Bethlehem 6th Ward|Bethlehem 7th Ward|Bethlehem 8th Ward|Bethlehem 9-1|Bethlehem 9-2|Bethlehem 9-3|Bethlehem 14-1|Bethlehem 14-2|Bethlehem 14-3|Bethlehem 14-4|Bethlehem 14-5|Bethlehem 14-6|Bethlehem 14-7|Bethlehem 14-8|Bethlehem 15-1|Bethlehem 15-2|Bethlehem 15-3|Bethlehem 16th Ward|Bethlehem 17th Ward|Bethlehem Twsp 1-1|Bethlehem Twsp 1-2|Bethlehem Twsp 1-3|Bethlehem Twsp 1-4|Bethlehem Twsp 2-1|Bethlehem Twsp 2-2|Bethlehem Twsp 2-3|Bethlehem Twsp 2-4|Bethlehem Twsp 3-1|Bethlehem Twsp 3-2|Bethlehem Twsp 3-3|Bethlehem Twsp 3-4|Bethlehem Twsp 3-5|Bethlehem Twsp 4-1|Bethlehem Twsp 4-2|Bethlehem Twsp 4-3|Bethlehem Twsp 4-4|Bushkill Twsp Bushkill Center|Bushkill Twsp Cherry Hill|Bushkill Twsp Clearfield|Chapman|East Allen Twsp East|East Allen Twsp West|East Bangor|Easton 2nd Ward|Easton 3-2|Easton 3-3|Easton 3-4|Easton 4th Ward|Easton 5th Ward|Easton 6th Ward|Easton 7th Ward|Easton 8th Ward East|Easton 8th Ward West|Easton 9th Ward|Easton 10th Ward East|Easton 10th Ward West|Easton 11th Ward|Easton 12th Ward|Forks Eastern 1|Forks Eastern 2|Forks Western 1|Forks Western 2|Freemansburg|Glendon|Hanover #1|Hanover #2|Hanover #3|Hanover #4|Hanover #5|Hanover #6|Hellertown 1st Ward|Hellertown 2nd Ward|Hellertown 3rd Ward|Lehigh Twsp Central|Lehigh Twsp Danielsville|Lehigh Twsp Pennsville|Lehigh Twsp Treichlers|Lehigh Twsp Northwestern|Lower Mt. Bethel Twsp Lower|Lower Mt. Bethel Twsp Upper|Lower Nazareth #1|Lower Nazareth #2|Lower Saucon Twsp 1|Lower Saucon Twsp 2|Lower Saucon Twsp 3|Lower Saucon Twsp 4|Lower Saucon Twsp 5|Lower Saucon Twsp 6|Lower Saucon Twsp 7|Lower Saucon Twsp 8|Moore Twsp Beersville|Moore Twsp Eastern|Moore Twsp Klecknersville|Moore Twsp Point Phillips|Nazareth 1st Ward|Nazareth 2nd Ward|Nazareth 3rd Ward|Northampton 1st Ward|Northampton 2nd Ward|Northampton 3rd Ward|Northampton 4th Ward|North Catasauqua 1|North Catasauqua 2|Palmer Eastern|Palmer Middle #1|Palmer Middle #2|Palmer Upper Eastern|Palmer Upper Western|Palmer Western #1|Palmer Western #2|Pen Argyl 1st Ward|Pen Argyl 2nd Ward|Pen Argyl 3rd Ward|Pen Argyl 4th Ward|Plainfield Twsp Belfast|Plainfield Twsp Delabole|Plainfield Twsp Kesslersville|Plainfield Twsp Plainfield Church|Portland Boro|Roseto Boro|Stockertown Boro|Tatamy Boro|Upper Mt. Bethel Twsp Centerville|Upper Mt. Bethel Twsp Johnsonville|Upper Mt. Bethel Twsp North Bangor|Upper Mt. Bethel Twsp Slateford|Upper Mt. Bethel Twsp Williamsburg|Upper Nazareth East|Upper Nazareth West|Walnutport|Washington Twsp Lower|Washington Twsp Upper|West Easton|Williams Twsp East|Williams Twsp Middle|Williams Twsp Upper|Williams Twsp West|Wilson Boro 1st Ward|Wilson Boro 2nd Ward|Wilson Boro 3rd Ward|Wind Gap Boro',
    "REP": 'Allen Twsp North|Allen Twsp South|Bangor Boro 1st Ward|Bangor Boro 2nd Ward|Bangor Boro 3rd Ward|Bangor Boro 4th Ward|Bath Boro|Bethlehem 1-North|Bethlehem 1-South|Bethlehem 2nd Ward|Bethlehem 3rd Ward|Bethlehem 4th Ward|Bethlehem 5th Ward|Bethlehem 6th Ward|Bethlehem 7th Ward|Bethlehem 8th Ward|Bethlehem 9-1|Bethlehem 9-2|Bethlehem 9-3|Bethlehem 14-1|Bethlehem 14-2|Bethlehem 14-3|Bethlehem 14-4|Bethlehem 14-5|Bethlehem 14-6|Bethlehem 14-7|Bethlehem 14-8|Bethlehem 15-1|Bethlehem 15-2|Bethlehem 15-3|Bethlehem 16th Ward|Bethlehem 17th Ward|Bethlehem Twsp 1-1|Bethlehem Twsp 1-2|Bethlehem Twsp 1-3|Bethlehem Twsp 1-4|Bethlehem Twsp 2-1|Bethlehem Twsp 2-2|Bethlehem Twsp 2-3|Bethlehem Twsp 2-4|Bethlehem Twsp 3-1|Bethlehem Twsp 3-2|Bethlehem Twsp 3-3|Bethlehem Twsp 3-4|Bethlehem Twsp 3-5|Bethlehem Twsp 4-1|Bethlehem Twsp 4-2|Bethlehem Twsp 4-3|Bethlehem Twsp 4-4|Bushkill Twsp Bushkill Center|Bushkill Twsp Cherry Hill|Bushkill Twsp Clearfield|Chapman|East Allen Twsp East|East Allen Twsp West|East Bangor|Easton 2nd Ward|Easton 3-2|Easton 3-3|Easton 3-4|Easton 4th Ward|Easton 5th Ward|Easton 6th Ward|Easton 7th Ward|Easton 8th Ward East|Easton 8th Ward West|Easton 9th Ward|Easton 10th Ward East|Easton 10th Ward West|Easton 11th Ward|Easton 12th Ward|Forks Eastern 1|Forks Eastern 2|Forks Western 1|Forks Western 2|Freemansburg|Glendon|Hanover #1|Hanover #2|Hanover #3|Hanover #4|Hanover #5|Hanover #6|Hellertown 1st Ward|Hellertown 2nd Ward|Hellertown 3rd Ward|Lehigh Twsp Central|Lehigh Twsp Danielsville|Lehigh Twsp Pennsville|Lehigh Twsp Treichlers|Lehigh Twsp Northwestern|Lower Mt. Bethel Twsp Lower|Lower Mt. Bethel Twsp Upper|Lower Nazareth #1|Lower Nazareth #2|Lower Saucon Twsp 1|Lower Saucon Twsp 2|Lower Saucon Twsp 3|Lower Saucon Twsp 4|Lower Saucon Twsp 5|Lower Saucon Twsp 6|Lower Saucon Twsp 7|Lower Saucon Twsp 8|Moore Twsp Beersville|Moore Twsp Eastern|Moore Twsp Klecknersville|Moore Twsp Point Phillips|Nazareth 1st Ward|Nazareth 2nd Ward|Nazareth 3rd Ward|Northampton 1st Ward|Northampton 2nd Ward|Northampton 3rd Ward|Northampton 4th Ward|North Catasauqua 1|North Catasauqua 2|Palmer Eastern|Palmer Middle #1|Palmer Middle #2|Palmer Upper Eastern|Palmer Upper Western|Palmer Western #1|Palmer Western #2|Pen Argyl 1st Ward|Pen Argyl 2nd Ward|Pen Argyl 3rd Ward|Pen Argyl 4th Ward|Plainfield Twsp Belfast|Plainfield Twsp Delabole|Plainfield Twsp Kesslersville|Plainfield Twsp Plainfield Church|Portland Boro|Roseto Boro|Stockertown Boro|Tatamy Boro|Upper Mt. Bethel Twsp Centerville|Upper Mt. Bethel Twsp Johnsonville|Upper Mt. Bethel Twsp North Bangor|Upper Mt. Bethel Twsp Slateford|Upper Mt. Bethel Twsp Williamsburg|Upper Nazareth East|Upper Nazareth West|Walnutport|Washington Twsp Lower|Washington Twsp Upper|West Easton|Williams Twsp East|Williams Twsp Middle|Williams Twsp Upper|Williams Twsp West|Wilson Boro 1st Ward|Wilson Boro 2nd Ward|Wilson Boro 3rd Ward|Wind Gap Boro',
}

def map_contest(line: str):
    """Map a contest header line to (office, district, party, mapped).

    ``party`` is "" for nonpartisan contests (ballot questions). Unmapped
    headers fall back to the printed body (minus the party code) so nothing
    is silently dropped.
    """
    line = re.sub(r"\s+", " ", line.strip())
    party = ""
    body = line
    pm = CONTEST_PARTY_RE.match(line)
    if pm:
        party, body = pm.group(1).upper(), pm.group(2).strip()

    if body in EXACT_OFFICES:
        office, district = EXACT_OFFICES[body]
        return (office, district, party, True)

    m = STATE_SENATE_RE.match(body)
    if m:
        return ("State Senate", m.group(1), party, True)

    m = STATE_HOUSE_RE.match(body)
    if m:
        return ("State House", m.group(1), party, True)

    m = COUNTY_COMMITTEE_DEM_RE.match(body)
    if m:
        style = "DEM Female" if m.group(1) == "Female" else "DEM Male"
        full = _committee_muni(style, m.group(2).strip(), _PRECINCT_REF[0], _PRECINCT_REF[1])
        return (
            "Member of the Democratic County Committee",
            f"{m.group(1)} {full}",
            party,
            True,
        )

    m = COUNTY_COMMITTEE_REP_RE.match(body)
    if m:
        full = _committee_muni("REP", m.group(1).strip(), _PRECINCT_REF[0], _PRECINCT_REF[1])
        return (
            "Member of the Republican County Committee",
            full,
            party,
            True,
        )

    if body in REFERENDUM_HEADERS:
        office, district = REFERENDUM_HEADERS[body]
        return (office, district, "", True)

    return (body, "", party, False)


def _merge_page_split_aggregates(rows):
    """Sum repeat "Write Ins"/"Overvotes"/"Undervotes" rows within a contest
    that span a page break (each page repeats the header)."""
    merged: list[dict] = []
    agg_index: dict[tuple, int] = {}
    current_key = None
    for row in rows:
        key_ctx = (row["office"], row["district"], row["party"])
        if key_ctx != current_key:
            current_key = key_ctx
            agg_index = {}
        key = (
            row["office"],
            row["district"],
            row["party"],
            row["candidate"],
        )
        if row["candidate"] in ("Write Ins", "Overvotes", "Undervotes"):
            if key in agg_index:
                prev = merged[agg_index[key]]
                for f in ("votes", "election_day", "mail", "provisional"):
                    prev[f] = str(int(prev[f] or 0) + int(row[f] or 0))
                continue
        merged.append(row)
        if row["candidate"] in ("Write Ins", "Overvotes", "Undervotes"):
            agg_index[key] = len(merged) - 1
    return merged


def parse_precinct_rows(precinct: str, text: str, warnings: set):
    rows: list[dict] = []
    totals = {}
    current = (None, "", "")  # office, district, party
    _PRECINCT_REF[0] = precinct
    _PRECINCT_REF[1] = warnings
    current_vote_for = 1

    lines = [ln.strip() for ln in text.split("\n")]

    # Merge wrapped write-in-name continuation lines (no digits).
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

    def add(candidate, vals):
        office, district, party = current
        total, ed, mail, prov = vals
        rows.append(
            {
                "county": "Northampton",
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "mail": mail,
                "provisional": prov,
            }
        )

    # Office headers = lines followed (next non-empty line) by "Vote For N".
    office_header_idx: dict[int, int] = {}
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            vf = VOTE_FOR_RE.match(nxt)
            if vf:
                office_header_idx[i] = int(vf.group(1))
            break

    for idx, line in enumerate(lines):
        if not line:
            continue

        if idx in office_header_idx:
            current_vote_for = office_header_idx[idx]
            office, district, party, mapped = map_contest(line)
            if not mapped:
                warnings.add(f"{precinct}: unmapped contest header: {line.strip()}")
            current = (office, district, party)
            continue

        m = STAT_SINGLE_RE.match(line)
        if m:
            which = m.group(1)
            rows.append(
                {
                    "county": "Northampton",
                    "precinct": precinct,
                    "office": "Registered Voters",
                    "district": "",
                    "party": STAT_PARTIES.get(which, ""),
                    "candidate": "",
                    "votes": int(m.group(2).replace(",", "")),
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                }
            )
            continue

        m = STAT_TAIL_RE.match(line)
        if m:
            which = m.group(1)
            vals = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
            total, ed, mail, prov = vals
            office = (
                "Ballots Cast"
                if which == "Total"
                else ("Ballots Cast - Blank" if which == "Blank" else "Ballots Cast")
            )
            party = "" if which in ("Total", "Blank") else STAT_PARTIES[which]
            rows.append(
                {
                    "county": "Northampton",
                    "precinct": precinct,
                    "office": office,
                    "district": "",
                    "party": party,
                    "candidate": "",
                    "votes": total,
                    "election_day": ed,
                    "mail": mail,
                    "provisional": prov,
                }
            )
            continue

        vote_m = VOTE_TAIL_RE.match(line)
        if vote_m is None:
            continue

        head = vote_m.group(1).strip()
        vals = parse_votes([vote_m.group(i) for i in (2, 3, 4, 5)])

        if head == "Total Votes Cast":
            if current[0]:
                totals[(precinct,) + current] = vals
            continue
        if head.lower().startswith("voter turnout"):
            continue
        if head == "Write-In Totals":
            add("Write Ins", vals)
            continue
        if head.startswith("Write-In:"):
            continue  # itemized write-in names; the aggregate row covers them
        if head == "Not Assigned":
            continue
        if head.upper() in ("YES", "NO"):
            add(head.upper().title(), vals)
            continue

        add(head, vals)  # inherits the contest's party

    return _merge_page_split_aggregates(rows), totals


SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "Primary Election",
    "May 17, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
)

CONFIG = ElectionwareConfig(
    county="Northampton",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Northampton County, Pennsylvania",
)


def parse_pdf(pdf_path: Path):
    import natural_pdf as npdf

    pdf = npdf.PDF(str(pdf_path))
    all_rows: list[dict] = []
    totals: dict[tuple, tuple] = {}
    warnings: set = set()
    precinct_count = 0
    _CC_COUNTERS.clear()
    for raw_name, text in extract_precinct_blocks(pdf, CONFIG):
        precinct_count += 1
        # Within-2022 convention (20221108 general county file): keep the
        # printed "010001-1 ALLEN TWSP NORTHERN" name as-is.
        name = re.sub(r"\s{2,}", " ", raw_name).strip()
        rows, block_totals = parse_precinct_rows(name, text, warnings)
        all_rows.extend(rows)
        totals.update(block_totals)
    return all_rows, precinct_count, totals, warnings


def write_csv(rows, out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> None:
    argv = list(argv) if argv is not None else sys.argv
    if len(argv) != 3:
        sys.exit(
            "Usage: pa_northampton_primary_2022_results_parser.py "
            "<input.pdf> <output.csv>"
        )
    pdf_path, out_path = Path(argv[1]), Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count, totals, warnings = parse_pdf(pdf_path)
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
    if warnings:
        print("WARNINGS:")
        for w in sorted(warnings):
            print(" ", w)
    return rows, precinct_count, totals


if __name__ == "__main__":
    main()