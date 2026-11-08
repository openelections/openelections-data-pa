#!/usr/bin/env python3
"""Parse Fayette County PA 2026 General Primary precinct results.

Source: ``Fayette County 2026 Primary Precinct Detail XML Official.xml``
(Clarify/Clarity Elections "ElectionResult" snapshot, generated 6/8/2026
4:49:49 PM EDT, Region=Fayette, 66 precincts).  Produces BOTH output files:

  * 2026/counties/20260519__pa__primary__fayette__precinct.csv
  * 2026/counties/20260519__pa__primary__fayette__county.csv

This REPLACES the Jul 2026 committed files (parsed from an earlier vintage of
the same XML).  The old parser emitted every resolved write-in detail choice
("STACY GARRITY", "DOUG MASTRIANO", ...) as its own candidate row with party ""
— 40,684 zero-vote rows and 951 nonzero per-name rows (Governor "Doug
Mastriano" 117, "Josh Shapiro" 37, ...) that fail the brief's 2026 primary
write-in convention (party-empty write-ins collide in duplicate_entries) and
inflate the file to 43,681 rows (97% zeros).

XML structure (one <Contest text="..."> per ballot style):
  <Choice text="Josh Shapiro" party="DEM" totalVotes="...">
    <VoteType name="Election Day Votes"/> / "Mail-In/Absentee Votes" /
    "Provisional Votes", each with per-<Precinct name votes> children.
  <Choice text="Write-in"> (party absent)  — unresolved write-in bucket
  <Choice text="STACY GARRITY"> (party absent) — write-in resolutions

Conventions (brief + York 2026 refresh precedent, work2026p/york/NOTES.md):
  * Choice WITH a party attribute = ballot candidate; the row carries that
    party (DEM/REP as the XML spells it).
  * EVERY party-absent Choice of a contest (the "Write-in" bucket AND all
    resolved names) is aggregated — per precinct, and countywide — into a
    single "Write-In Totals" row carrying the contest's party, emitted for
    every precinct in the contest's reported scope (zero-vote aggregates
    kept, York precedent).  Named write-in detail is preserved only inside
    the aggregate; nonzero votes are preserved by construction and the
    totals are cross-checked against the old committed files.
  * Contests: Governor, Lieutenant Governor, U.S. House 14, State Senate 32,
    State House 51/52, Member of the (Democratic|Republican) State
    Committee 26th District (district column blank — Berks/Elk/York 2026
    precedent; the anchor keys State Committee by office+party+name only)
    plus 198 poll-scoped "<municipality> Democratic
    Committeeman/Committeewoman / Republican Committee Member" contests
    (kept at precinct level, office as printed — Delaware 2026 detail.xml
    precedent; most 2026 counties' precinct files carry them).  Poll-scoped
    contests are NOT in the countywide CSV (all 2026 *__county.csv files
    carry contest-level offices only).
  * No Registered Voters / Ballots Cast rows: the snapshot's <VoterTurnout>
    turnout block is internally inconsistent (per-precinct ballotsCast is
    smaller than the contest votes it would have to contain — e.g.
    "Belle Vernon Borough - 02-01": ballotsCast 68 vs 685 contest votes;
    percentReporting="4") so it is not trusted; the old committed files
    carry none either.
  * Precinct names as printed with internal whitespace runs collapsed
    ("Dunbar  Borough - 18-01" -> "Dunbar Borough - 18-01") — identical to
    the old committed files' 66 names.
  * Breakdown columns election_day / mail / provisional (the combined
    "Mail-In/Absentee Votes" maps to ``mail``, brief rule).  The old files
    used the header election_day,provisional,absentee holding the same three
    values (old "absentee" = this mail column, different column order); the
    new header matches the adams/bucks/dauphin/delaware 2026 mail-family
    files.

Verification (inline; --no-verify to skip):
  * every emitted row: election_day + mail + provisional == votes;
  * per contest/choice: sum over precincts == the XML countywide VoteType
    votes attribute (reported on drift; county CSV uses the sums);
  * named ballot candidates: BOTH files' per-contest sums vs the anchor
    ``2026/20260519__pa__primary__county.csv`` (case/punctuation-insensitive
    full-name keyed — last-name keys would collapse Fayette's RSC Chrash
    pair (MELANY / GREGORY); Democratic/Republican normalized) — must be
    digit-exact;
  * vs the old committed files (defaults: work2026p/fayette/fayette_old_*.csv):
    per-precinct ballot candidate values must be identical (old 'absentee'
    column == new 'mail'); per (precinct, office, district) write-in total —
    new DEM+REP aggregates vs old Write-In Totals row + all party-empty
    named rows — reported as match/diff (vintage drift surfaced).

Usage:
    uv run python parsers/pa_fayette_primary_2026_results_parser.py [--xml-src P]
        [--precinct-out P] [--county-out P] [--old-precinct P] [--old-county P]
        [--no-verify]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT.parent / "openelections-sources-pa" / "2026" / "primary"
OUT_DIR = REPO_ROOT / "2026" / "counties"
ANCHOR = REPO_ROOT / "2026" / "20260519__pa__primary__county.csv"

COUNTY = "Fayette"

PRECINCT_FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]
COUNTY_FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

DEFAULTS = {
    "xml_src": SRC_DIR / "Fayette County 2026 Primary Precinct Detail XML Official.xml",
    "old_precinct": REPO_ROOT / "work2026p" / "fayette" / "fayette_old_precinct.csv",
    "old_county": REPO_ROOT / "work2026p" / "fayette" / "fayette_old_county.csv",
    "precinct_out": OUT_DIR / "20260519__pa__primary__fayette__precinct.csv",
    "county_out": OUT_DIR / "20260519__pa__primary__fayette__county.csv",
}

WS_RE = re.compile(r"\s+")

# Choice breakdown VoteType names -> column index in [votes, ed, mail, prov]
VT_COL = {"Election Day Votes": 1, "Mail-In/Absentee Votes": 2,
          "Provisional Votes": 3}

# Poll-scoped municipal committee contest titles (excluded from county csv)
POLL_SCOPED_RE = re.compile(
    r"^(.+ )?(Democratic Committeeman|Democratic Committeewoman"
    r"|Republican Committee Member)$")


def norm_name(s: str) -> str:
    """Collapse internal whitespace runs; strip."""
    return WS_RE.sub(" ", s).strip()


def norm_party(p: str) -> str:
    return {"Democratic": "DEM", "Republican": "REP"}.get(p, p)


def norm_office(title: str):
    """XML contest title -> (office, district, party, poll_scoped)."""
    pm = re.search(r"\((Democratic|Republican)\)$", title)
    party = norm_party(pm.group(1)) if pm else ""
    base = re.sub(r"\s*\((?:Democratic|Republican)\)$", "", title).strip()
    if base == "Governor":
        return "Governor", "", party, False
    if base == "Lieutenant Governor":
        return "Lieutenant Governor", "", party, False
    m = re.match(r"^Representative in Congress (\d+)(?:st|nd|rd|th) District$", base)
    if m:
        return "U.S. House", m.group(1), party, False
    m = re.match(r"^Senator in the General Assembly (\d+)(?:st|nd|rd|th) District$", base)
    if m:
        return "State Senate", m.group(1), party, False
    m = re.match(r"^Representative in the General Assembly "
                 r"(\d+)(?:st|nd|rd|th) District$", base)
    if m:
        return "State House", m.group(1), party, False
    m = re.match(r"^Member of the (Democratic|Republican) State Committee", base)
    if m:
        # district column blank (Berks/Elk/York 2026 precedent)
        return ("Member of %s State Committee" % m.group(1), "",
                m.group(1)[:3].upper(), False)
    # poll-scoped municipal committee contest, office as printed
    return title, "", party, True


def parse_xml(path: Path):
    """-> (precinct_names (VoterTurnout order), contests, warnings).

    contests: list of dicts in document order with keys:
      office, district, party, poll_scoped, title,
      candidates: [{name, party, precincts: {precinct: [v, ed, mail, prov]}}],
      writein_prec: {precinct: [v, ed, mail, prov]} (sum of ALL party-absent
      choices of the contest per precinct).
    """
    warnings = []
    root = ET.parse(str(path)).getroot()
    precincts = [norm_name(pr.get("name")) for pr in
                 root.find("VoterTurnout").find("Precincts").findall("Precinct")]

    contests = []

    def acc(store, col, val):
        store[col] += val
        if col:
            store[0] += val

    for c in root.iter("Contest"):
        title = norm_name(c.get("text"))
        office, dist, party, poll = norm_office(title)
        candidates = []
        writein_prec = {}
        for o in c.findall("Choice"):
            text = norm_name(o.get("text"))
            is_ballot = o.get("party") is not None
            cand = None
            if is_ballot:
                cand = {"name": text, "party": o.get("party").strip(),
                        "precincts": {}}
                candidates.append(cand)
            for v in o.findall("VoteType"):
                col = VT_COL.get(v.get("name"))
                if col is None:
                    warnings.append(f"{title}: unmapped VoteType "
                                    f"{v.get('name')!r} on choice {text!r}; skipped")
                    continue
                attr = int(v.get("votes"))
                seen = 0
                for q in v.findall("Precinct"):
                    p = norm_name(q.get("name"))
                    val = int(q.get("votes"))
                    seen += val
                    store = cand["precincts"].setdefault(
                        p, [0, 0, 0, 0]) if is_ballot else \
                        writein_prec.setdefault(p, [0, 0, 0, 0])
                    acc(store, col, val)
                if seen != attr:
                    warnings.append(
                        f"{title}: choice {text!r} party={o.get('party')} "
                        f"{v.get('name')}: precinct sum {seen} != "
                        f"countywide attr {attr}")
        contests.append({"title": title, "office": office, "district": dist,
                         "party": party, "poll_scoped": poll,
                         "candidates": candidates, "writein_prec": writein_prec})
        # poll-scoped committee contests carry their party on the ballot
        # candidates ("Belle Vernon Borough Democratic Committeeman", choices
        # party=DEM) — propagate it (brief rule: every row carries the
        # contest party; berks 2026 precedent for committee Write-In Totals).
        if contests[-1]["party"] == "" and candidates:
            cps = {cand["party"] for cand in candidates}
            if len(cps) == 1:
                contests[-1]["party"] = cps.pop()
        # (some committee contests are all-write-in with no ballot candidate,
        # so fall back to the title's party word)
        if contests[-1]["party"] == "":
            m = re.search(r"\b(Democratic|Republican)\b", title)
            if m:
                contests[-1]["party"] = m.group(1)[:3].upper()
    return precincts, contests, warnings


def build_precinct_rows(precincts, contests):
    """Rows for the precinct CSV: precinct-major, contest document order, per
    contest each ballot candidate (source order, zero rows kept) then the
    aggregated Write-In Totals row."""
    rows = []
    for prec in precincts:
        for c in contests:
            out = []
            for cand in c["candidates"]:
                v = cand["precincts"].get(prec)
                if v is None:
                    continue  # candidate not on this precinct's ballot style
                out.append([COUNTY, prec, c["office"], c["district"],
                            cand["party"], cand["name"],
                            str(v[0]), str(v[1]), str(v[2]), str(v[3])])
            wi = c["writein_prec"].get(prec)
            if wi is not None:
                out.append([COUNTY, prec, c["office"], c["district"],
                            c["party"], "Write-In Totals",
                            str(wi[0]), str(wi[1]), str(wi[2]), str(wi[3])])
            rows.extend(out)
    return rows


def build_county_rows(contests):
    """Countywide rows for the regular contests: per contest each ballot
    candidate then the aggregated Write-In Totals row."""
    rows = []
    for c in contests:
        if c["poll_scoped"]:
            continue
        for cand in c["candidates"]:
            s = [sum(v[i] for v in cand["precincts"].values()) for i in range(4)]
            rows.append([COUNTY, c["office"], c["district"], cand["party"],
                         cand["name"], str(s[0]), str(s[1]), str(s[2]), str(s[3])])
        wi = [0, 0, 0, 0]
        for v in c["writein_prec"].values():
            for i in range(4):
                wi[i] += v[i]
        rows.append([COUNTY, c["office"], c["district"], c["party"],
                     "Write-In Totals", str(wi[0]), str(wi[1]), str(wi[2]),
                     str(wi[3])])
    return rows


def last_name_key(candidate: str) -> str:
    return re.split(r"\s+", candidate.strip())[-1].lower()


def full_name_key(candidate: str) -> str:
    """Case/punctuation/whitespace-insensitive full-name key.  Used because
    Fayette's Republican State Committee slate has a last-name collision
    (MELANY CHRASH 2424 / GREGORY CHRASH 1491) that a last-name key would
    collapse."""
    s = re.sub(r"[^a-z0-9 ]", "", candidate.lower())
    return re.sub(r"\s+", " ", s).strip()


def key_of_row(r, precinct=False):
    """Anchor-style key of a row: (office, district, party, full name)."""
    if precinct:
        office, dist, party, cand = r[2], r[3], r[4], r[5]
    else:
        office, dist, party, cand = r[1], r[2], r[3], r[4]
    if office.endswith("State Committee"):
        dist = ""
    return (office, dist, party, full_name_key(cand))


def read_anchor(path: Path):
    """FAYETTE rows of the statewide anchor ->
    {(office, dist, party, full name): (v, ed, ml, pv)}"""
    out = {}
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if row[0].upper() != COUNTY.upper():
                continue
            office, dist, party, cand = row[1:5]
            v, ed, ml, pv = (int(x) for x in row[5:9])
            if office.endswith("State Committee"):
                dist = ""
            out[(office, dist, norm_party(party), full_name_key(cand))] = (v, ed, ml, pv)
    return out


def compare_anchor(precinct_rows, county_rows, warnings, failures):
    anchor = read_anchor(ANCHOR)
    sums = defaultdict(int)
    breakdowns = defaultdict(lambda: [0, 0, 0])
    for r in precinct_rows:
        if r[4] not in ("DEM", "REP"):
            continue
        k = key_of_row(r, precinct=True)
        sums[k] += int(r[6])
        b = breakdowns[k]
        b[0] += int(r[7]); b[1] += int(r[8]); b[2] += int(r[9])
    matched = 0
    for k, (v, ed, ml, pv) in anchor.items():
        mine = sums.get(k, 0)
        if mine != v:
            failures.append(f"anchor mismatch (precinct): {k}: sum {mine} "
                            f"!= anchor {v}")
            continue
        matched += 1
        b = breakdowns.get(k, [0, 0, 0])
        if b != [ed, ml, pv]:
            warnings.append(f"breakdown drift vs anchor for {k}: "
                            f"(ed,mail,prov) {b} != ({ed}, {ml}, {pv})")
    csums = defaultdict(int)
    for r in county_rows:
        csums[key_of_row(r)] += int(r[5])
    for k, (v, _ed, _ml, _pv) in anchor.items():
        if csums.get(k, 0) != v:
            failures.append(f"anchor mismatch (county csv): {k}: sum "
                            f"{csums.get(k, 0)} != anchor {v}")
    return matched, len(anchor)


def compare_old_precinct(precinct_rows, old_path, warnings, failures):
    """(a) per-precinct ballot rows identical (old 'absentee' == new 'mail');
    (b) per (precinct, office, district): new DEM+REP write-in aggregates vs
    old Write-In Totals row + all party-empty named rows."""
    if not Path(old_path).exists():
        print(f"NOTE: old file {old_path} absent — skipped")
        return
    ballot_old = {}
    wi_old = defaultdict(lambda: [0, 0, 0, 0])  # (prec, office, dist): stores (v, ed, mail, prov)
    with Path(old_path).open(newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r)
        for row in r:
            _, prec, office, dist, party, cand, votes, ed, prov, ab = row
            prec = norm_name(prec)
            if party:
                ballot_old[(prec, office, dist, party, cand)] = \
                    (int(votes), int(ed), int(prov), int(ab))
            else:
                v = wi_old[(prec, office, dist)]
                v[0] += int(votes); v[1] += int(ed); v[2] += int(ab); v[3] += int(prov)
    ballot_new = {}
    wi_new = defaultdict(lambda: [0, 0, 0, 0])
    committee_new = defaultdict(int)   # poll-scoped committee rows (old: none)
    for r in precinct_rows:
        prec, office, dist, party, cand = r[1], r[2], r[3], r[4], r[5]
        if cand == "Write-In Totals":
            v = wi_new[(prec, office, dist)]
            v[0] += int(r[6]); v[1] += int(r[7]); v[2] += int(r[8]); v[3] += int(r[9])
        elif not POLL_SCOPED_RE.match(office):
            ballot_new[(prec, office, dist, party, cand)] = \
                (int(r[6]), int(r[7]), int(r[9]), int(r[8]))
            # stored as (votes, ed, prov, mail) to match the old column order
        else:
            committee_new[(prec, office, dist, party, cand)] = int(r[6])
    identical = diff = 0
    for key, new in ballot_new.items():
        old = ballot_old.get(key)
        if old is None:
            print(f"  ballot row not in old file: {key} {new}")
        elif old == new:
            identical += 1
        else:
            diff += 1
            if diff <= 15:
                print(f"  BALLOT DIFF {key}: new {new} old {old}")
    only_old = set(ballot_old) - set(ballot_new)
    if only_old:
        print(f"  ballot rows in old but not new: {len(only_old)}")
        for k in list(only_old)[:10]:
            print(f"    {k} {ballot_old[k]}")
    print(f"old-vs-new ballot candidate rows: identical {identical}, "
          f"differ {diff}, old-only {len(only_old)}")
    if diff or only_old:
        failures.append(f"{diff + len(only_old)} old-vs-new ballot row differences")
    wi_ok = wi_diff = 0
    wi_groups = set(wi_old) | {g for g in wi_new
                               if not POLL_SCOPED_RE.match(g[1])}
    for g in sorted(wi_groups):
        n = wi_new.get(g, [0, 0, 0, 0])
        o = wi_old.get(g, [0, 0, 0, 0])
        # old stores (votes, ed, mail, prov) — same layout here
        if n == o:
            wi_ok += 1
        else:
            wi_diff += 1
            if wi_diff <= 15:
                print(f"  WI DIFF {g}: new(aggregated) {n} old {o}")
    print(f"old-vs-new per-precinct write-in totals: match {wi_ok}, "
          f"diff {wi_diff} (of {len(wi_groups)})")
    print(f"poll-scoped committee rows added (old file had none): "
          f"{len(committee_new)}")
    return wi_diff


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name, default in DEFAULTS.items():
        ap.add_argument("--" + name.replace("_", "-"), default=str(default))
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    precincts, contests, warnings = parse_xml(Path(args.xml_src))
    print(f"source: {len(precincts)} precincts, {len(contests)} contests "
          f"({sum(not c['poll_scoped'] for c in contests)} regular, "
          f"{sum(c['poll_scoped'] for c in contests)} poll-scoped committee)")

    rows = build_precinct_rows(precincts, contests)

    # breakdown sanity for every emitted row
    for r in rows:
        if int(r[7]) + int(r[8]) + int(r[9]) != int(r[6]):
            sys.exit(f"breakdown mismatch: {r}")

    county_rows = build_county_rows(contests)
    county_rows.sort(key=lambda r: (
        {c: i for i, c in enumerate(["Governor", "Lieutenant Governor",
                                     "U.S. House", "State Senate",
                                     "State House",
                                     "Member of Democratic State Committee",
                                     "Member of Republican State Committee"])}.get(r[1], 99),
        int(r[2]) if r[2] else 0, r[1], r[3],
        1 if r[4] == "Write-In Totals" else 0, r[4]))

    if not args.no_verify:
        failures = []
        matched, n_anchor = compare_anchor(rows, county_rows, warnings, failures)
        print(f"anchor: prec-file named sums {matched}/{n_anchor} keys "
              f"digit-exact (+ county-file cross-check)")
        compare_old_precinct(rows, Path(args.old_precinct), warnings, failures)
    else:
        failures = []
        matched = None

    for path, fieldnames, data in (
            (Path(args.precinct_out), PRECINCT_FIELDNAMES, rows),
            (Path(args.county_out), COUNTY_FIELDNAMES, county_rows)):
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(fieldnames)
            w.writerows(data)
        print(f"wrote {len(data)} rows -> {path}")

    if warnings:
        print(f"NOTE: {len(warnings)} warning(s):")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print(f"  ... +{len(warnings) - 40} more")
    if matched is not None and matched == 0:
        sys.exit("no anchor keys matched — aborting (wrong anchor format?)")
    if failures:
        sys.exit(f"{len(failures)} verification failure(s):\n" +
                 "\n".join("  " + f for f in failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
