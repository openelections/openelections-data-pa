#!/usr/bin/env python3
"""Westmoreland County PA 2023 Municipal Primary (May 16, 2023) — results parser.

Source: "Westmoreland County Official Results 2023 Primary" (Electionware
"Summary Results Report", COUNTY-LEVEL ONLY — the county published no
per-precinct report for the 2023 primary, so this parser produces the
9-column county CSV and there is no precinct CSV).

Layout notes (Westmoreland 2023 primary, vertical 4-column layout):

- Contest headers carry the ballot party as a prefix: "DEM Justice of the
  Supreme Court" / "REP County Commissioner".  The contest party goes into
  the party column of every candidate row of that contest, INCLUDING the
  Write-ins row (repo-wide primary convention, as in the published
  2020/2024 primary files: a party-less Write-ins row would make the DEM
  and REP sections of the same office collide under duplicate detection).
  The Registered Voters / Ballots Cast / Ballots Cast - Blank metadata
  rows keep an empty party.
- Data-row columns: TOTAL, Election Day, Absentee/Mail, Provisional.
- Multi-county contests (school districts and magisterial districts that
  span neighboring counties) carry two extra columns ("<other county>
  Votes", "Total Votes Combined"); only the first four columns
  (Westmoreland) are recorded.
- Named "Write-In: <name>" detail rows are folded into ONE "Write-ins" row
  per contest (carrying the contest party).  The "Write-In Totals" row IS
  the aggregate: it already includes the "Not Assigned" row (verified:
  named details + Not Assigned == Write-In Totals in several contests;
  the Not Assigned row itself is not emitted separately).  Contests with
  no aggregate row would get the details (+ Not Assigned) summed into one
  Write-ins row; in practice all 358 contests carry a "Write-In Totals"
  row, including eleven whose totals are 0 (emitted as a 0-vote Write-ins
  row, the same convention the other 2023-primary county files use).
- Two write-in detail rows in a Supervisor Township contest carry inline
  tie-breaker annotations ("Lost Tie Breaker" / "Won Tie Breaker"); they
  are ordinary detail rows and fold into the aggregate.
- Contests repeat their header on every continuation page of a multi-page
  write-in list; a section closes at its "Write-In Totals" + "Not
  Assigned" rows (Not Assigned can follow the totals row on the next
  page, under a repeated header), so a repeated header before that is a
  continuation, and a repeated header after it is a genuinely separate
  contest (Sewickley / Allegheny / Donegal townships elect several
  auditors; Derry Borough and Jeannette City elect two councils — the
  primary headers carry no term suffix).  The 22 offices that appear as
  several complete sections are resolved per group (see TERM_BY_BODY
  below): sections with distinct "Vote For N" counts that the county's
  2023 general file maps to distinct terms get the general file's own
  " ... (2 Year)" office label on the 2-year section; the rest (equal
  Vote For counts — term not recoverable from any source) are merged
  into one row per (office, district, party, candidate), summing the
  vote columns, so the duplicate_entries data test passes (Berks
  2023-primary county-summary convention).
- There are NO "Total Votes Cast" / "Contest Totals" rows in the source;
  reconciliation is by row breakdown sums, write-in arithmetic
  (details + Not Assigned == Write-In Totals) and the party-ballot
  ceiling (each contest's rows <= its party's Ballots Cast).
- "DEM/REP COUNTY COMMISSIONER" etc. page furniture: the statistics block
  reports Registered Voters 213,881, Ballots Cast 62,855 (ED 44,788 /
  Mail 17,970 / Provisional 97), Ballots Cast - Democratic 30,435,
  Republican 32,420, NONPARTISAN 0, Ballots Cast - Blank 58.

Office/district mapping mirrors the Westmoreland 2023 general parser
(pa_westmoreland_general_2023_results_parser), with the DEM/REP prefix
stripped and without the general's "- N Year" suffixes (the primary
headers carry no term suffix).

Usage:
    python parsers/pa_westmoreland_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

from pa_2023_summary_common import text_from_pdf
from pa_westmoreland_general_2023_results_parser import (
    _EXACT,
    _SCHOOL_FIX,
    _cap_word,
    _muni_name,
    _muni_type,
    _strip_type,
    _tcase,
)

COUNTY = "Westmoreland"

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

VOTE_FOR_RE = re.compile(r"^Vote For\s+\d+$", re.IGNORECASE)
PARTY_PREFIX = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
WRITE_IN_DETAIL_RE = re.compile(r"^Write-In\s*:", re.IGNORECASE)
WRITE_IN_TOTALS_RE = re.compile(r"^Write-In Totals", re.IGNORECASE)
NOT_ASSIGNED_RE = re.compile(r"^Not Assigned", re.IGNORECASE)
MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE\s+(.+)$", re.IGNORECASE)
# School director, primary variant (no "- N Year" suffix):
#   "SCHOOL DIRECTOR [AT LARGE ]<dist> [REGION N]"
SCHOOL_RE = re.compile(
    r"^SCHOOL DIRECTOR\s+(AT LARGE\s+)?(.+?)(?:\s+REGION\s+([IVX]+))?$",
    re.IGNORECASE,
)
# Local offices, primary variant (no "- N Year" suffix):
#   "<OFFICE> <muni> [Nth WARD]"
LOCAL_RE = re.compile(
    r"^(MAYOR|TREASURER|CONTROLLER|COUNCIL|AUDITOR|SUPERVISOR|COMMISSIONER"
    r"|TAX COLLECTOR)\s+(.+)$",
    re.IGNORECASE,
)
_ORDINAL_RE = re.compile(r"^(.+?)\s+(\d+(?:ST|ND|RD|TH))\s+WARD$", re.IGNORECASE)
_NUM_RE = re.compile(r"^\d[\d,]*$")
# inline tie-breaker annotations on write-in detail rows
_TIEBREAK_RE = re.compile(r"\s+(Won|Lost)\s+Tie\s+Breaker\s*$", re.IGNORECASE)

# Seat-term table for the multi-section contests the 2023 PRIMARY report
# prints with an identical header (the primary headers carry no term
# suffix).  Extracted from the county's own 2023 GENERAL report
# ("Westmoreland__Westmoreland_County_Official_Results_2023_General.txt"):
# for each seat the general file's contest headers state the term and the
# "Vote For N" count that belongs to it, e.g.
# "COUNCIL DERRY BOROUGH - 4 Year / Vote For 4" and
# "COUNCIL DERRY BOROUGH - 2 Year / Vote For 1".  A primary section whose
# "Vote For N" uniquely identifies a term is labelled with the general
# file's own office label (" ... (2 Year)"); seat continuity verified (the
# general 2-year candidates are the primary VF-matching section's
# winners).  Offices whose repeated sections share one "Vote For N" (and
# New Kensington-Arnold Region III, where the general prints Vote For 1
# for BOTH seats) are NOT in this table: their term is not recoverable.
TERM_BY_BODY = {
    "COUNCIL DERRY BOROUGH": {4: 4, 1: 2},
    "COUNCIL EAST VANDERGRIFT BOROUGH": {3: 4, 1: 2},
    "COUNCIL NORTH IRWIN BOROUGH": {4: 4, 2: 2},
    "COUNCIL OKLAHOMA BOROUGH": {2: 4, 1: 2},
    "COUNCIL SEWARD BOROUGH": {4: 4, 1: 2},
    "COUNCIL SOUTHWEST GREENSBURG BOROUGH": {3: 4, 1: 2},
    "COUNCIL SUTERSVILLE BOROUGH": {3: 4, 1: 2},
    "COUNCIL WEST NEWTON BOROUGH": {4: 4, 1: 2},
    "SCHOOL DIRECTOR AT LARGE BELLE VERNON AREA": {5: 4, 1: 2},
    "SCHOOL DIRECTOR AT LARGE FRANKLIN REGIONAL": {5: 4, 1: 2},
    "SCHOOL DIRECTOR AT LARGE GREATER LATROBE": {5: 4, 1: 2},
    "SCHOOL DIRECTOR AT LARGE JEANNETTE CITY": {5: 4, 2: 2},
    "SCHOOL DIRECTOR KISKI AREA REGION II": {2: 4, 1: 2},
}

# page furniture / title lines
_JUNK_SUB = (
    "Summary Results Report",
    "OFFICIAL MUNICIPAL ELECTION BALLOT",
    "OFFICIAL RESULTS",
    "May 16, 2023",
    "Westmoreland County",
    "Election Results Including Group Totals and Write-ins in Winner Order",
    "Report generated with Electionware",
    "Page ",
    " of ",
    "Voter Turnout",
    "Precincts Complete",
    "Precincts Partially Reported",
    "Election Day Precincts Reporting",
    "Absentee/ Early Precincts Reporting",
    "Absentee/",
    "STATISTICS",
    "TOTAL",
    "Provisional",
    "Election Day",
)


# ---------------------------------------------------------------------------
# header normalization (mirrors the 2023 general Westmoreland parser,
# minus the "- N Year" term suffixes the primary headers do not carry)
# ---------------------------------------------------------------------------

def _ward(tail):
    """("North Huntingdon", "1st") from "NORTH HUNTINGDON TOWNSHIP 1ST WARD"."""
    m = _ORDINAL_RE.match(tail)
    if not m:
        return tail, None
    ward = re.sub(r"^(\d+)(ST|ND|RD|TH)$",
                  lambda mm: mm.group(1) + mm.group(2).lower(),
                  m.group(2), flags=re.IGNORECASE)
    return m.group(1).strip(), ward


def _school(body):
    m = SCHOOL_RE.match(body)
    if not m:
        return None
    at_large, dist, region = m.groups()
    parts = dist.split()
    fixed = " ".join(_SCHOOL_FIX.get(t.upper(), _cap_word(t)) for t in parts)
    if region:
        return (f"School Director Region {region.upper()}", fixed)
    if at_large:
        return ("School Director At Large", fixed)
    return ("School Director", fixed)


def _local(body):
    m = LOCAL_RE.match(body)
    if not m:
        return None
    prefix, tail = m.group(1), m.group(2).strip()
    tail, ward = _ward(tail)
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
        if mtype == "Borough":
            office, district = "Borough Council", f"{base} Borough"
        else:
            office, district = "City Council", base
        if ward:
            district += f" {ward} Ward"
        return (office, district)
    if p == "AUDITOR":
        office = "Township Auditor" if mtype == "Township" else "Borough Auditor"
        district = f"{base} {mtype}" if mtype in ("Borough", "Township") else base
        return (office, district)
    if p == "SUPERVISOR":
        return ("Township Supervisor", f"{base} Township")
    if p == "COMMISSIONER":
        district = f"{base} Township"
        if ward:
            district += f" {ward} Ward"
        return ("Township Commissioner", district)
    if p == "TAX COLLECTOR":
        district = f"{base} {mtype}" if mtype in ("Borough", "Township") else base
        return ("Tax Collector", district)
    return None


def normalize_primary(body):
    """(office, district) from the party-stripped contest header body."""
    u = re.sub(r"\s+", " ", body).strip().upper()
    if u in _EXACT:
        return _EXACT[u]
    if u == "REGISTER OF WILLS AND CLERK OF THE ORPHANS COURT":
        return ("Register of Wills and Clerk of the Orphans' Court", "")
    if u.startswith("MAGISTERIAL DISTRICT JUDGE "):
        return ("Magisterial District Judge",
                re.sub(r"\s+", " ", body.split(None, 3)[3]).strip())
    m = _school(body)
    if m:
        return m
    m = _local(body)
    if m:
        return m
    return None


# ---------------------------------------------------------------------------
# parse loop
# ---------------------------------------------------------------------------

def tail_nums(l):
    """Contiguous trailing numeric tokens, in source order (left->right)."""
    toks = l.split()
    out = []
    for t in reversed(toks):
        if _NUM_RE.match(t):
            out.append(t)
        else:
            break
    out.reverse()
    return [t.replace(",", "") for t in out]


def vals(nums):
    """(total, ed, mail, prov) from trailing numeric tokens.

    4+ numbers -> TOTAL / Election Day / Absentee-Mail / Provisional;
    multi-county contests append "<other county> Votes" and "Total Votes
    Combined" columns, which are dropped (Westmoreland-only figures are
    the first four).  A single number is a total-only row.
    """
    if len(nums) >= 4:
        return nums[0], nums[1], nums[2], nums[3]
    if len(nums) == 1:
        return nums[0], "", "", ""
    return None


class Contest:
    def __init__(self, office, district, party, raw, vf=1):
        self.office = office
        self.district = district
        self.party = party
        self.raw = raw
        self.vf = vf               # "Vote For N"
        self.agg = None            # "Write-In Totals" row (or None)
        self.details = [0, 0, 0, 0]
        self.has_detail = False
        self.na = [0, 0, 0, 0]
        self.has_na = False
        self.cand_sum = [0, 0, 0, 0]  # candidate rows
        self.rows = 0

    def label(self):
        return f"{self.office}|{self.district}|{self.party}"

    def wi_sum(self):
        return [a + b for a, b in zip(self.details, self.na)]


def parse_input(path):
    lines = [l.strip() for l in open(path, errors="replace") if l.strip()]
    rows = []
    row_owner = []         # parallel list: Contest that emitted each row
    warnings = []
    contests = []          # completed Contest objects, in order
    current = None
    stats = {}             # statistics-block values (incl. per-party BC)

    def emit(contest, party, candidate, v):
        total, ed, mi, pr = v
        rows.append({
            "county": COUNTY,
            "office": contest.office,
            "district": contest.district,
            "party": party,
            "candidate": candidate,
            "votes": total,
            "election_day": ed,
            "mail": mi,
            "provisional": pr,
        })
        row_owner.append(contest)

    def flush(contest):
        if contest is None:
            return
        # ONE Write-ins row per contest.  The source's "Write-In Totals"
        # aggregate ALREADY includes the "Not Assigned" row (verified:
        # details + Not Assigned == Write-In Totals).  Contests with no
        # aggregate row get the details (+ Not Assigned) summed instead.
        if contest.agg is not None:
            v = contest.agg
        elif contest.has_detail or contest.has_na:
            v = contest.wi_sum()
        else:
            v = None
        if v is not None:
            # Write-ins carry the CONTEST's party (repo-wide primary
            # convention; see the published 2020/2024 primary files).
            emit(contest, contest.party, "Write-ins", v)
        contests.append(contest)

    for i, l in enumerate(lines):
        # contest header: the next non-empty line is "Vote For N"
        if not _NUM_RE.match(l) and VOTE_FOR_RE.match(lines[i + 1] if i + 1 < len(lines) else ""):
            m = PARTY_PREFIX.match(l)
            if m is None:
                warnings.append(f"unrecognized header: {l!r}")
                continue
            party, body = m.group(1).upper(), m.group(2).strip()
            mvf = re.match(r"^Vote For\s+(\d+)", lines[i + 1], re.IGNORECASE)
            vf = int(mvf.group(1)) if mvf else 1
            norm = normalize_primary(body)
            if norm is None:
                warnings.append(f"unmapped contest body: {body!r}")
                current = Contest(body, "", party, body, vf)
                flush(current)
                current = None
                continue
            office, district = norm
            if current is not None:
                same = (current.office, current.district, current.party) == \
                    (office, district, party)
                # A repeated header is a continuation page unless the
                # contest's section already closed: its "Write-In Totals"
                # AND "Not Assigned" rows have both been seen (the Not
                # Assigned row can follow the totals row on the next page,
                # under a repeated header).
                if same and (current.agg is None or not current.has_na):
                    continue
                flush(current)
                current = None
            current = Contest(office, district, party, body, vf)
            continue

        if VOTE_FOR_RE.match(l):
            continue
        if any(sub in l for sub in _JUNK_SUB):
            continue
        if current is None:
            # statistics block
            for key in ("Registered Voters - Total", "Registered Voters - Democratic",
                        "Registered Voters - Republican",
                        "Ballots Cast - Total", "Ballots Cast - Democratic",
                        "Ballots Cast - Republican", "Ballots Cast - NONPARTISAN",
                        "Ballots Cast - Blank"):
                if l.startswith(key):
                    nums = tail_nums(l[len(key):])
                    if nums:
                        stats[key] = nums
                    break
            continue
        if WRITE_IN_TOTALS_RE.match(l):
            nums = tail_nums(l)
            v = vals(nums) if len(nums) >= 4 or len(nums) == 1 else None
            if v is None:
                warnings.append(f"bad Write-In Totals row ({len(nums)} nums): {l!r}")
                continue
            agg = [int(x) for x in (v if len(v) == 4 else (v[0], 0, 0, 0))]
            agg += [0] * (4 - len(agg))
            agg = agg[:4]
            if current.agg is not None:
                warnings.append(f"second Write-In Totals row in {current.label()}")
            current.agg = agg
            continue
        if WRITE_IN_DETAIL_RE.match(l):
            l = _TIEBREAK_RE.sub("", l)  # inline "Won/Lost Tie Breaker"
            nums = tail_nums(l)
            v = vals(nums)
            if v is None:
                warnings.append(f"bad write-in detail row: {l!r}")
                continue
            if v[0] != "":
                t, e, mi2, p = (int(x) if x != "" else 0 for x in v)
                current.details[0] += t
                current.details[1] += e
                current.details[2] += mi2
                current.details[3] += p
                current.has_detail = True
            continue
        if NOT_ASSIGNED_RE.match(l):
            nums = tail_nums(l)
            v = vals(nums)
            if v is not None:
                t, e, mi2, p = (int(x) for x in v)
                current.na[0] += t
                current.na[1] += e
                current.na[2] += mi2
                current.na[3] += p
                current.has_na = True
            continue
        nums = tail_nums(l)
        if not nums:
            continue
        v = vals(nums)
        if v is None:
            warnings.append(f"odd numeric layout ({len(nums)} nums): {l!r}")
            continue
        if len(nums) > 6:
            warnings.append(f"too many numeric columns: {l!r}")
        head = " ".join(l.split()[: len(l.split()) - len(nums)])
        if not head:
            warnings.append(f"data row without a name: {l!r}")
            continue
        total, ed, mi, pr = v
        emit(current, current.party, head, (total, ed, mi, pr))
        current.rows += 1
        current.cand_sum[0] += int(total)
        current.cand_sum[1] += int(ed) if ed != "" else 0
        current.cand_sum[2] += int(mi) if mi != "" else 0
        current.cand_sum[3] += int(pr) if pr != "" else 0
    flush(current)

    # Statistics-block metadata rows (party column empty, candidate column
    # empty): Registered Voters, Ballots Cast, Ballots Cast - Blank.  The
    # per-party Ballots Cast rows are NOT recorded (repo convention).
    def meta(office, nums):
        rows.append({
            "county": COUNTY, "office": office, "district": "",
            "party": "", "candidate": "", "votes": nums[0],
            "election_day": nums[1] if len(nums) > 1 else "",
            "mail": nums[2] if len(nums) > 2 else "",
            "provisional": nums[3] if len(nums) > 3 else "",
        })
        row_owner.append(None)

    if stats.get("Registered Voters - Total"):
        meta("Registered Voters", stats["Registered Voters - Total"])
    if stats.get("Ballots Cast - Total"):
        meta("Ballots Cast", stats["Ballots Cast - Total"])
    if stats.get("Ballots Cast - Blank"):
        meta("Ballots Cast - Blank", stats["Ballots Cast - Blank"])

    # Same-key repeated sections: some offices appear as several complete
    # contest sections with an identical header (the primary headers carry
    # no term suffix, so the 4-year and 2-year council/supervisor/auditor
    # seats and the 4-year/2-year school-director seats print identically).
    # Resolution, per repeated (office, district, party) group:
    #
    #   1. If the sections' "Vote For N" values differ AND the county's own
    #      2023 GENERAL file maps each of those Vote For counts to a distinct
    #      term for that seat (TERM_BY_BODY below, extracted from
    #      "Westmoreland__Westmoreland_County_Official_Results_2023_General.txt"),
    #      the sections are disambiguated: the seat matching the general
    #      file's 2-year contest gets the general file's own office label
    #      " ... (2 Year)" (verified seat continuity: the general 2-year
    #      candidates are the primary VF-matching section's winners, e.g.
    #      Belle Vernon Area 2-year: primary DEM VF1 = Livengood/Pollock/
    #      TerBest -> general "BELLE VERNON AREA - 2 Year" candidates
    #      Pollock 1501 / Livengood 1210 / TerBest write-in).
    #   2. Otherwise (equal Vote For counts: borough VF2/VF2, auditor and
    #      supervisor VF1/VF1, New Kensington-Arnold Region III VF1/VF1 -
    #      the general file also prints VF1 for both of its seats) the
    #      section's term is NOT recoverable from any available source, so
    #      the sections are merged into ONE row per (office, district,
    #      party, candidate), summing the vote columns - the Berks
    #      2023-primary county-summary convention ("matching the precinct
    #      engine").  Merging is required anyway for the duplicate_entries
    #      data test, which hashes every non-vote column.
    groups = {}
    for idx, c in enumerate(contests):
        groups.setdefault((c.office, c.district, c.party), []).append(idx)

    # 1. disambiguate the resolvable multi-section groups (distinct Vote For
    #    counts, each uniquely mapping to a term in the county's general
    #    file): the 2-year section gets the general file's "(2 Year)" label
    renamed_ids = set()
    for key, cidx in groups.items():
        if len(cidx) < 2:
            continue
        body = re.sub(r"\s+", " ", contests[cidx[0]].raw.strip().upper())
        vfmap = TERM_BY_BODY.get(body)
        vfs = [contests[i].vf for i in cidx]
        if vfmap and len(set(vfs)) == len(vfs) and all(v in vfmap for v in vfs):
            for i in cidx:
                if vfmap[contests[i].vf] == 2:
                    contests[i].office += " (2 Year)"
                    renamed_ids.add(id(contests[i]))
    for pos, c in enumerate(row_owner):
        if c is not None and id(c) in renamed_ids:
            rows[pos]["office"] = c.office

    # 2. merge whatever duplicate (office, district, party, candidate) keys
    #    remain (the ambiguous multi-section groups), summing the vote
    #    columns - the Berks 2023-primary county-summary convention
    #    ("matching the precinct engine"); required for the
    #    duplicate_entries data test, which hashes every non-vote column
    merged = {}
    out = []
    for r in rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        if key not in merged:
            merged[key] = r
            out.append(r)
            continue
        m = merged[key]
        for col in ("votes", "election_day", "mail", "provisional"):
            a, b = m[col], r[col]
            if a == "":
                m[col] = b
            elif b != "":
                m[col] = str(int(a) + int(b))
    rows = out

    return rows, warnings, stats, contests


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate(rows, stats, contests):
    problems = []
    # 1. every row's ED + Mail + Provisional must sum to votes
    breakdown_bad = 0
    for r in rows:
        if r["election_day"] == "":
            continue
        s = int(r["election_day"]) + int(r["mail"]) + int(r["provisional"])
        if s != int(r["votes"]):
            breakdown_bad += 1
            if breakdown_bad <= 5:
                problems.append(
                    f"breakdown mismatch: {r['office']}|{r['district']} "
                    f"{r['candidate']}: {r['election_day']}+{r['mail']}+"
                    f"{r['provisional']} != {r['votes']}")
    if breakdown_bad:
        problems.append(f"total rows with breakdown mismatch: {breakdown_bad}")

    # 2. write-in arithmetic: details + Not Assigned == Write-In Totals
    for c in contests:
        if c.agg is not None and c.has_detail:
            d = c.wi_sum()
            if d != c.agg:
                problems.append(
                    f"write-in arithmetic {c.label()}: details sum {d} "
                    f"!= Write-In Totals {c.agg}")

    # 3. party-ballot arithmetic: contest rows + write-ins <= the party's
    #    Ballots Cast x "Vote For N"
    bc = {
        "DEM": int(stats["Ballots Cast - Democratic"][0]),
        "REP": int(stats["Ballots Cast - Republican"][0]),
    }
    ceil_bad = 0
    for c in contests:
        s = c.cand_sum[0] + c.wi_sum()[0]
        if s > bc[c.party] * c.vf:
            ceil_bad += 1
            problems.append(
                f"party-ballot ceiling {c.label()}: rows+write-ins {s} > "
                f"{c.vf} x {c.party} Ballots Cast {bc[c.party]}")
    # 4. statistics internal checks
    bc_total = [int(x) for x in stats["Ballots Cast - Total"]]
    if sum(bc_total[1:]) != bc_total[0]:
        problems.append(f"Ballots Cast breakdown: {bc_total}")
    if bc["DEM"] + bc["REP"] != bc_total[0]:
        problems.append(
            f"DEM {bc['DEM']} + REP {bc['REP']} != Ballots Cast - Total "
            f"{bc_total[0]}")
    blank = [int(x) for x in stats["Ballots Cast - Blank"]]
    if sum(blank[1:]) != blank[0]:
        problems.append(f"Ballots Cast - Blank breakdown: {blank}")
    return problems


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    src = argv[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    rows, warnings, stats, contests = parse_input(src)

    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} county-level rows to {out_path}")
    print(f"contests parsed: {len(contests)} "
          f"(DEM {sum(1 for c in contests if c.party == 'DEM')}, "
          f"REP {sum(1 for c in contests if c.party == 'REP')})")
    print(f"write-in contests with aggregate row: "
          f"{sum(1 for c in contests if c.agg is not None)}")
    if stats.get("Ballots Cast - Total"):
        print("Ballots Cast - Total:", ",".join(stats["Ballots Cast - Total"]))
        print("Ballots Cast - Democratic:", ",".join(stats["Ballots Cast - Democratic"]))
        print("Ballots Cast - Republican:", ",".join(stats["Ballots Cast - Republican"]))
    problems = validate(rows, stats, contests)
    if warnings:
        print(f"WARNING: {len(warnings)} parse warnings:")
        for msg in warnings[:20]:
            print("  " + msg)
    if problems:
        print(f"VALIDATION: {len(problems)} problem(s):")
        for msg in problems:
            print("  " + msg)
    else:
        print("validation: all checks pass "
              "(row breakdowns, write-in arithmetic, party-ballot ceilings, "
              "statistics)")


if __name__ == "__main__":
    main(sys.argv)