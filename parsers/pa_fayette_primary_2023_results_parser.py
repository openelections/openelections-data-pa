#!/usr/bin/env python3
"""Fayette County 2023 Municipal Primary — county-level results from the
Dominion "Election Summary Report / Closed Primary" county summary
(Final Certification of Computation, May 16, 2023).

COUNTY-LEVEL ONLY: the 2023 primary source is a countywide summary with
per-contest write-in detail blocks; no per-precinct primary source exists
(the 2023 GENERAL used a 736-page SOVC precinct report, but the primary
certification carries county totals only).

Layout (differs from the generic parse_esr2 in pa_2023_summary_common):
- Contest headers: "<Office> (DEM) (Vote for N)" / "(REP)"; the contest
  party goes in the party column of every candidate row of the contest.
- Each contest: "Candidate Party Election Day Mail-In Provisional Total"
  block, a "Total Votes ed mi pr total" line, then a large write-in detail
  block ("NAME WRITE-IN ed mi pr total", "Scattered [N] WRITE-IN ...")
  that frequently spills across page breaks with repeated column headers,
  plus one truncated row whose Total wrapped onto the following line.
- No aggregate "Write-in" rows and no Undervotes/Overvotes rows.
- Top "Elector Group" block: per-party ballots (DEM 12,318 / REP 11,883)
  with Election Day / Mail-In / Provisional breakdowns, plus countywide
  totals (24,201 of 77,360 registered).

This wrapper does NOT reuse parse_esr2 because party-duplicated contest
headers (same office DEM + REP) collide in that engine's contest-totals
bookkeeping and it does not parse the Elector Group block.

Usage: pa_fayette_primary_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pa_2023_summary_common import FIELDNAMES, nums_from, is_junk, text_from_pdf

COUNTY = "Fayette"

# --------------------------------------------------------------------------
# office normalization: "<Office> (DEM) (Vote for N)" -> (office, district,
# party), mirroring the county's 2023 general file conventions
# --------------------------------------------------------------------------

_EXACT = {
    "District Attorney": "District Attorney",
    "Sheriff": "Sheriff",
    "County Commissioner": "County Commissioner",
    "County Controller": "County Controller",
    "Clerk of Courts": "Clerk of Courts",
    "Register of Wills/Clerk of the Orphans Court":
        "Register of Wills & Clerk of Orphans' Court",
    "Prothonotary": "Prothonotary",
    "Coroner": "Coroner",
}

# primary header name (after stripping " School District") -> district used
# by the county's 2023 general file
_SCHOOL = {
    "Albert Gallatin": "Albert Gallatin Area",
    "Belle Vernon": "Belle Vernon Area",
    "Brownsville": "Brownsville Area",
    "Connellsville": "Connellsville Area",
    "Frazier": "Frazier",
    "Laurel Highlands": "Laurel Highlands",
    "Southmoreland": "Southmoreland",
    "Uniontown": "Uniontown Area",
}

_LOCAL_OFFICES = ("Borough Council", "City Council", "Township Supervisor",
                  "Township Auditor", "Borough Auditor",
                  "Borough Tax Collector", "Township Tax Collector",
                  "City Mayor", "City Controller", "City Treasurer",
                  "Constable")

_LOCAL_RE = re.compile(
    r"^(%s) \((\d) Year\) - (.+)$" % "|".join(_LOCAL_OFFICES))

_MDJ_RE = re.compile(r"^Magisterial District Judge District (.+)$")

_SCHOOL_RE = re.compile(r"^School Director \((\d) Year\) - (.+?)\s+School "
                        r"District$")


def _muni(name):
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s+Boro$", "", name)
    name = re.sub(r"\s+Twp$", " Township", name)
    name = re.sub(r"\s+City$", "", name)
    return name


def _local_office(raw_office, year, tail):
    tail = re.sub(r"\s+", " ", tail).strip()
    year_sfx = " (%s Year)" % year
    if raw_office == "Borough Council":
        return ("Borough Council" + (year_sfx if year == "2" else ""),
                _muni(tail))
    if raw_office == "City Council":
        return ("City Council" + (year_sfx if year == "2" else ""),
                _muni(tail))
    if raw_office == "Township Supervisor":
        return ("Township Supervisor" + year_sfx, _muni(tail))
    if raw_office == "Township Auditor":
        return ("Township Auditor" + year_sfx, _muni(tail))
    if raw_office == "Borough Auditor":
        return ("Borough Auditor" + year_sfx, _muni(tail))
    if raw_office in ("Borough Tax Collector", "Township Tax Collector"):
        return ("Tax Collector" + year_sfx, _muni(tail))
    if raw_office == "City Mayor":
        return ("Mayor", _muni(tail))
    if raw_office == "City Controller":
        return ("City Controller", _muni(tail))
    if raw_office == "City Treasurer":
        return ("City Treasurer", _muni(tail))
    if raw_office == "Constable":
        return ("Constable" + year_sfx, re.sub(r"\s+", " ", tail).strip())
    return (raw_office, _muni(tail))


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    party = ""
    m = re.search(r"\s*\(Vote for (\d+)\)\s*$", h, re.I)
    if m:
        h = h[:m.start()].strip()
    m = re.search(r"\s*\((DEM|REP)\)\s*$", h, re.I)
    if m:
        party = m.group(1).upper()
        h = h[:m.start()].strip()

    if h in _EXACT:
        return _EXACT[h], "", party
    m = _MDJ_RE.match(h)
    if m:
        code = re.sub(r"\s*-\s*", "-", m.group(1).strip())
        return "Magisterial District Judge", code, party
    m = _SCHOOL_RE.match(h)
    if m:
        dist = _SCHOOL.get(m.group(2).strip(), m.group(2).strip())
        return "School Director (%s Year)" % m.group(1), dist, party
    m = _LOCAL_RE.match(h)
    if m:
        office, dist = _local_office(m.group(1), m.group(2), m.group(3))
        return office, dist, party
    return h, "", party


# --------------------------------------------------------------------------
# Elector Group block (top of report): per-party and countywide ballots
# --------------------------------------------------------------------------

def parse_elector_group(lines):
    """Return (groups, countywide).

    groups: {'DEM': {'total','ed','mi','pr','registered'}, 'REP': {...}}
    countywide: {'total','ed','mi','pr','registered'} (DEM+REP breakdowns).
    """
    groups = {}
    cw = {}
    label = None  # 'DEM' | 'REP' | 'CW'
    for line in lines[:80]:
        s = re.sub(r"\s+", " ", line.strip())
        if not s:
            continue
        if "(Vote for" in s:   # first contest header: the block is over
            break
        m = re.match(r"^(Democratic|Republican)\s+(.*)$", s)
        if m:
            label = "DEM" if m.group(1) == "Democratic" else "REP"
            groups.setdefault(label, {})
            s_rest = m.group(2)
        elif re.match(r"^Total Election Day\b", s, re.I):
            label = "CW"
            s_rest = s
        elif re.match(r"^(Elector Group|Counting Group)\b", s, re.I):
            continue
        else:
            s_rest = s
        if label is None:
            continue
        cg = re.match(r"^(Election Day|Mail-In|Provisional|Total)\s+(.*)$",
                      s_rest, re.I)
        if not cg:
            continue
        nums = nums_from(cg.group(2).split())
        if not nums:
            continue
        kind = cg.group(1).lower()
        if kind == "total":
            n = int(nums[0].replace(",", ""))
            if label == "CW":
                cw["total"] = n
                if len(nums) >= 3:
                    cw["registered"] = int(nums[2].replace(",", ""))
            else:
                groups[label]["total"] = n
                if len(nums) >= 3:
                    groups[label]["registered"] = int(nums[2].replace(",", ""))
        else:
            key = {"election day": "ed", "mail-in": "mi",
                   "provisional": "pr"}[kind]
            if label == "CW":
                cw[key] = int(nums[0].replace(",", ""))
            else:
                groups[label][key] = int(nums[0].replace(",", ""))
    if cw and "ed" not in cw and "DEM" in groups and "REP" in groups:
        cw["ed"] = groups["DEM"].get("ed", 0) + groups["REP"].get("ed", 0)
        cw["mi"] = groups["DEM"].get("mi", 0) + groups["REP"].get("mi", 0)
        cw["pr"] = groups["DEM"].get("pr", 0) + groups["REP"].get("pr", 0)
    return groups, cw


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------

class FayetteRows:
    def __init__(self, county):
        self.county = county
        self.rows = []
        self.contests = []
        self.totals = {}     # (office, district, party) -> "Total Votes"
        self.wi_total = {}   # (office, district, party) -> write-in aggregate
        self.warnings = []

    def add(self, office, district, party, candidate, total, ed="", mi="",
            pr=""):
        self.rows.append([self.county, office, district, party, candidate,
                          total, ed, mi, pr])


def parse(text_path, county=COUNTY):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = FayetteRows(county)
    groups, cw = parse_elector_group(lines)
    for label, expect in (("DEM", 12318), ("REP", 11883)):
        got = groups.get(label, {}).get("total")
        if got != expect:
            R.warnings.append("%s elector group ballots %s != %d"
                              % (label, got, expect))
    if cw.get("total") is not None:
        if cw["ed"] + cw["mi"] + cw["pr"] != cw["total"]:
            R.warnings.append(
                "countywide ED/Mail/Prov %s+%s+%s != total %s"
                % (cw["ed"], cw["mi"], cw["pr"], cw["total"]))
        elif groups.get("DEM", {}).get("total", 0) \
                + groups.get("REP", {}).get("total", 0) != cw["total"]:
            R.warnings.append("DEM + REP elector group ballots != countywide "
                              "total %s" % cw["total"])

    text = "\n".join(lines)
    rv = bc = None
    m = re.search(r"Registered Voters:\s*[\d,]+\s*of\s*([\d,]+)", text)
    if m:
        rv = int(m.group(1).replace(",", ""))
    m = re.search(r"^Ballots Cast:\s*([\d,]+)\s*$", text, re.M)
    if m:
        bc = int(m.group(1).replace(",", ""))
    if bc is not None and cw.get("total") is not None and cw["total"] != bc:
        R.warnings.append("Elector Group total ballots %s != 'Ballots Cast:' "
                          "%s" % (cw["total"], bc))
    if rv is not None and cw.get("registered") not in (None, rv):
        R.warnings.append("Elector Group registered %s != 'Registered "
                          "Voters:' %s" % (cw.get("registered"), rv))

    VOTEFOR = re.compile(r"\(vote for (\d+)\)", re.IGNORECASE)
    current = None            # (office, district, party) from map_contest
    details = [False, [0, 0, 0, 0]]   # in-detail-block, [ed, mi, pr, total]
    had_agg = [False]
    pending_tot = [None]      # wrapped Total of a truncated 3-number row

    def flush_details():
        if current is not None and details[0]:
            s = details[1]
            R.add(current[0], current[1], current[2], "Write-ins",
                  s[3], s[0], s[1], s[2])
            R.wi_total[current] = s[3]
        details[0] = False
        details[1] = [0, 0, 0, 0]

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # -- contest header -------------------------------------------------
        if VOTEFOR.search(stripped) \
                or re.search(r"\(vote(\s+for)?\s*$", stripped, re.I):
            flush_details()
            header = stripped
            j = i
            while not VOTEFOR.search(header) and j + 1 < len(lines) \
                    and lines[j + 1].strip():
                j += 1
                header += " " + lines[j].strip()
                if VOTEFOR.search(header):
                    break
            current = map_contest(header)
            R.contests.append(current)
            had_agg[0] = False
            i = j + 1
            continue

        if current is None or is_junk(stripped):
            i += 1
            continue

        low = stripped.lower()
        if low.startswith(("precincts reported", "times cast", "candidate",
                           "elector group", "counting group",
                           "registered voters:", "ballots cast:")):
            i += 1
            continue
        # column-header rows (contest start / repeated at page breaks):
        # those carry no digits — digit-bearing "Total Votes ..." lines must
        # NOT be caught here
        if re.match(r"^(election day|mail-in|provisional|total|democratic|"
                    r"republican)\b", low) \
                and not re.search(r"\d", stripped):
            i += 1
            continue

        if re.match(r"^total votes", stripped, re.I):
            flush_details()
            details[0] = True
            nums = nums_from(stripped.replace("WRITE-IN", " ").split())
            if nums:
                R.totals[current] = int(nums[3] if len(nums) >= 4 else nums[0])
            i += 1
            continue

        if re.search(r"write-in", stripped, re.I):
            toks = stripped.split()
            try:
                widx = next(k for k, t in enumerate(toks)
                            if t.upper().rstrip(",") == "WRITE-IN")
            except StopIteration:
                widx = -1
            after = toks[widx + 1:] if widx >= 0 else toks
            nums = nums_from(after)
            if details[0]:
                # detail-block row (after the contest's "Total Votes" line);
                # accumulate unless the contest had an aggregate row instead
                if not had_agg[0]:
                    if len(nums) >= 4:
                        details[1] = [a + int(b) for a, b in
                                      zip(details[1], nums[:4])]
                    elif len(nums) == 3:
                        # truncated row: its Total wrapped to the next line
                        pending_tot[0] = current
                        d = details[1]
                        details[1] = [d[0] + int(nums[0]),
                                      d[1] + int(nums[1]),
                                      d[2] + int(nums[2]), d[3]]
                    elif len(nums) == 1:
                        details[1][3] += int(nums[0])
            else:
                had_agg[0] = True
                if len(nums) >= 4:
                    R.add(current[0], current[1], current[2], "Write-ins",
                          nums[3], nums[0], nums[1], nums[2])
                elif len(nums) == 1:
                    R.add(current[0], current[1], current[2], "Write-ins",
                          nums[0])
                R.wi_total[current] = int(nums[3] if len(nums) >= 4
                                          else nums[0])
            i += 1
            continue

        # a lone number right after a truncated 3-number write-in row is its
        # wrapped Total column
        if pending_tot[0] is not None:
            nums = nums_from(stripped.split())
            pending_tot[0] = None
            if len(nums) == 1:
                details[1][3] += int(nums[0])
                i += 1
                continue
            # otherwise fall through (e.g. a wrapped name fragment)

        nums = nums_from(stripped.split())
        if not nums:
            i += 1
            continue

        # candidate row: "<Name> <DEM|REP> ed mi pr total"
        toks = stripped.split()
        head = []
        pi = None
        for k, t in enumerate(toks):
            if re.fullmatch(r"\d[\d,]*", t.replace(",", "")):
                pi = k
                break
            head.append(t)
        if pi is None:
            i += 1
            continue
        row_party = ""
        if head and head[-1].upper().rstrip(",") in ("DEM", "REP"):
            row_party = head[-1].upper()
            head = head[:-1]
        name = re.sub(r"\s+", " ", " ".join(head)).strip()
        contest_party = current[2] if current else ""
        if row_party and row_party != contest_party:
            R.warnings.append(
                "candidate row party %r disagrees with contest party %r in "
                "%s | %s: %s" % (row_party, contest_party, current[0],
                                 current[1], name))
        nums = nums_from(toks[pi:])
        if len(nums) >= 4:
            total, edv, miv, prv = nums[3], nums[0], nums[1], nums[2]
        elif nums:
            total, edv, miv, prv = nums[0], "", "", ""
        else:
            i += 1
            continue
        R.add(current[0], current[1], contest_party, name, total,
              edv, miv, prv)
        i += 1

    flush_details()

    if rv is not None:
        R.add("Registered Voters", "", "", "", rv)
    if bc is not None:
        if cw and cw.get("total") == bc:
            R.add("Ballots Cast", "", "", "", bc, cw["ed"], cw["mi"],
                  cw["pr"])
        else:
            R.add("Ballots Cast", "", "", "", bc)
    return R


def check_totals(R):
    """Candidate rows + Write-ins vs each contest's 'Total Votes'."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in R.rows:
        if r[4] == "Write-ins":
            continue  # counted via R.wi_total (keyed to the contest)
        key = (r[1], r[2], r[3])
        try:
            agg[key] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is None:
            continue
        s = agg.get(key, 0) + R.wi_total.get(key, 0)
        if s != tot:
            bad.append((key, s, tot))
    return bad


def run_cli():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit("Usage: %s <input.pdf|txt> <output.csv>" % sys.argv[0])
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    R = parse(src)
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(R.rows)
    print("wrote %d rows -> %s" % (len(R.rows), dst))
    print("contests parsed: %d" % len(R.contests))
    for wmsg in R.warnings:
        print("WARNING:", wmsg)
    bad = check_totals(R)
    if bad:
        for (office, dist, party), a, t in bad:
            print("WARNING %s (%s) | %s: rows sum %s != Total Votes %s"
                  % (office, party, dist, a, t))
    else:
        print("contest totals check: all contests reconcile")
    brk_bad = 0
    for r in R.rows:
        try:
            t, e, m, p = int(r[5]), int(r[6]), int(r[7]), int(r[8])
        except (TypeError, ValueError):
            continue
        if e + m + p != t:
            brk_bad += 1
            print("WARNING breakdown mismatch: %s %s %s: %s+%s+%s != %s"
                  % (r[1], r[2], r[4], e, m, p, t))
    print("breakdown check: %d row(s) with ED+Mail+Prov != total" % brk_bad)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())