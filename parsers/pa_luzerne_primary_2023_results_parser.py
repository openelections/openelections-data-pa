#!/usr/bin/env python3
"""Luzerne County 2023 Municipal Primary — county-level results from the
Dominion "Election Summary Report / Closed Primary" county summary
(Official Results, May 16, 2023, certified 6/13/2023).

COUNTY-LEVEL ONLY: the 2023 primary source is a 194-page countywide summary
(contest totals + write-in detail blocks); it carries no per-precinct data,
so no precinct CSV is produced.

Layout (Dominion ESR2 "Closed Primary"; differs from the generic
parse_esr2 in pa_2023_summary_common, so this is a dedicated parser
modeled on pa_fayette_primary_2023_results_parser.py):
- Contest headers: "<Office>-<District> (N Year Term) (DEM) (Vote for N)";
  headers wrap across lines (both "(DEM)
(Vote for N)" and
  "(Vote for
N)" shapes). The contest party goes in the party
  column of every row of the contest (repo-wide primary convention:
  write-in rows carry the contest party too, so DEM/REP sections of the
  same office do not collide in the duplicate_entries test).
- Each contest: "Times Cast", "Candidate Party ... Total" block, an
  optional aggregate "Write-in" row, "Total Votes ed mi pr total", then a
  write-in detail block ("NAME WRITE-IN ed mi pr total" / "SCATTER [n]
  WRITE-IN ..."). Details are folded into one "Write-ins" row (aggregate
  row is used when present; else details are summed).
- No Undervotes/Overvotes rows.
- Top "Elector Group" block: DEM 23,559 / REP 21,779 ballots with
  Election Day / Mail-In / Provisional breakdowns; countywide 45,338 of
  195,727 registered.

Usage: pa_luzerne_primary_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pa_2023_summary_common import FIELDNAMES, nums_from, text_from_pdf

COUNTY = "Luzerne"

# contest headers that map_contest could not normalize (populated at parse
# time; run_cli prints them so nothing is silently guessed)
UNMAPPED = []

# --------------------------------------------------------------------------
# office normalization: "<Office>-<District> (N Year Term) (DEM) (Vote for N)"
# -> (office, district, party), mirroring the county's 2023 general file
# (work2023/county_level/20231107__pa__general__luzerne__county.csv):
# offices keep the "(N Year)" suffix in the office column, boroughs lose
# "Borough", townships/cities keep their suffix, auditors (township or
# borough) are "Township Auditor", ward qualifiers become " Ward N".
# --------------------------------------------------------------------------

_MDJ_RE = re.compile(r"^Magisterial District Judge (\d{2}-\d-\d{2})$")

_SCHOOL = {
    "Berwick Area": "Berwick Area",
    "Crestwood": "Crestwood School District",
    "Dallas": "Dallas Area",
    "Greater Nanticoke Area": "Greater Nanticoke Area",
    "Hanover Area": "Hanover Area",
    "Hazleton Area": "Hazleton Area",
    "Lake Lehman": "Lake-Lehman School District",
    "Northwest Area": "Northwest Area",
    "Pittston Area": "Pittston Area",
    "Wilkes Barre Area": "Wilkes-Barre Area",
    "Wyoming Area": "Wyoming Area",
    "Wyoming Valley West": "Wyoming Valley West School District",
}

_STATEWIDE = {
    "Justice of the Supreme Court": "Justice of the Supreme Court",
    "Judge of the Superior Court": "Judge of the Superior Court",
    "Judge of the Commonwealth Court": "Judge of the Commonwealth Court",
}


def _muni(name):
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\s*Borough\s*(?:\(Ward (\d+)\)|Ward (\d+)\b|WD (\d+))?\s*$",
                  lambda m: (" Ward %s" % (m.group(1) or m.group(2)
                                           or m.group(3))) if
                  (m.group(1) or m.group(2) or m.group(3)) else "",
                  name)
    name = re.sub(r"\s+Boro$", "", name)
    if name == "Buck Townhip Township":
        name = "Buck Township"
    if name == "Wilkes Barre Township":
        name = "Wilkes-Barre Township"
    return name


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
    m = re.search(r"\((\d) Year Term ?\)\s*$", h)
    year = m.group(1) if m else ""
    if m:
        h = (h[:m.start()] + " " + h[m.end():]).strip()

    def _yy(office):
        return "%s (%s Year)" % (office, year) if year else office

    m = _MDJ_RE.match(h)
    if m:
        return ("Magisterial District Judge",
                "Magisterial District " + m.group(1), party)

    m = re.match(r"^School Director-(.+)$", h)
    if m:
        name = m.group(1).strip()
        region = ""
        m2 = re.search(r"\s*-\s*Region (\d+)$", name, re.I)
        if m2:
            region = " Region " + m2.group(1)
            name = name[:m2.start()].strip()
        # the general file keeps " School District" for Crestwood /
        # Lake-Lehman / Wyoming Valley West but drops it for "* Area"
        # districts; _SCHOOL maps the primary's spelling to the general name
        name = re.sub(r"\s+School District$", "", name, flags=re.I)
        if name not in _SCHOOL:
            UNMAPPED.append(header)
        return _yy("School Director"), _SCHOOL.get(name, name) + region, party

    m = re.match(r"^Borough Council-(.+)$", h)
    if m:
        return _yy("Borough Council"), _muni(m.group(1)), party

    m = re.match(r"^City Council District ([A-E])-(.+)$", h)
    if m:
        return (_yy("City Council"),
                _muni(m.group(2)) + " Council District " + m.group(1), party)

    m = re.match(r"^(City Council|Township Council|Township Supervisor|"
                 r"Township Commissioner|Mayor|Tax Collector|City Treasurer|"
                 r"Auditor|Controller)-(.+)$", h)
    if m:
        office, tail = m.group(1), _muni(m.group(2))
        if office == "Auditor":
            # general file maps every "Auditor <Muni>" to "Township Auditor"
            office = "Township Auditor"
        elif office == "Controller":
            office = "City Controller"
        return _yy(office), tail, party

    if h in ("County Council", "District Attorney"):
        return _yy(h), "", party

    if h in _STATEWIDE:
        return _STATEWIDE[h], "", party

    UNMAPPED.append(header)
    return h, "", party


# --------------------------------------------------------------------------
# Elector Group block (top of report): per-party and countywide ballots
# --------------------------------------------------------------------------

def parse_elector_group(lines):
    """Return (groups, countywide).

    groups: {'DEM': {'total','ed','mi','pr','registered'}, 'REP': {...}}
    countywide: {'total','ed','mi','pr','registered'} (DEM+REP).
    """
    groups = {}
    cw = {}
    label = None
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
        tgt = cw if label == "CW" else groups.setdefault(label, {})
        if kind == "total":
            tgt["total"] = int(nums[0].replace(",", ""))
            if len(nums) >= 3:
                tgt["registered"] = int(nums[2].replace(",", ""))
        else:
            tgt[{"election day": "ed", "mail-in": "mi",
                 "provisional": "pr"}[kind]] = int(nums[0].replace(",", ""))
    if cw and "ed" not in cw and "DEM" in groups and "REP" in groups:
        cw["ed"] = groups["DEM"].get("ed", 0) + groups["REP"].get("ed", 0)
        cw["mi"] = groups["DEM"].get("mi", 0) + groups["REP"].get("mi", 0)
        cw["pr"] = groups["DEM"].get("pr", 0) + groups["REP"].get("pr", 0)
    return groups, cw


# --------------------------------------------------------------------------
# parse
# --------------------------------------------------------------------------

class LuzerneRows:
    def __init__(self, county):
        self.county = county
        self.rows = []
        self.contests = []
        self.totals = {}      # (office, district, party) -> "Total Votes"
        self.times_cast = {}  # (office, district, party) -> "Times Cast" tot
        self.wi_total = {}    # (office, district, party) -> write-in total
        self.wi_detail = {}   # key -> summed detail [ed, mi, pr, total]
        self.warnings = []

    def add(self, office, district, party, candidate, total, ed="", mi="",
            pr=""):
        self.rows.append([self.county, office, district, party, candidate,
                          total, ed, mi, pr])


VOTEFOR = re.compile(r"\(vote for (\d+)\)", re.IGNORECASE)
HEADER_END = re.compile(
    r"(\((?:dem|rep)\)\s*$|\(vote(\s+for)?\s*$|\(vote\s+for\s+\d+\)\s*$|"
    r"\(\d Year Term ?\)$)", re.IGNORECASE)


def parse(text_path, county=COUNTY):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = LuzerneRows(county)
    groups, cw = parse_elector_group(lines)
    for label, expect in (("DEM", 23559), ("REP", 21779)):
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

    current = None             # (office, district, party) from map_contest
    details = [False, [0, 0, 0, 0]]   # in-detail-block, [ed, mi, pr, total]
    had_agg = [False]
    pending_tot = [None]       # wrapped Total of a truncated 3-number row

    def flush_details():
        if current is not None and details[0] \
                and any(details[1]):
            s = details[1]
            R.add(current[0], current[1], current[2], "Write-ins",
                  s[3], s[0], s[1], s[2])
            R.wi_total[current] = R.wi_total.get(current, 0) + s[3]
            R.wi_detail[current] = list(s)
        details[0] = False
        details[1] = [0, 0, 0, 0]

    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # -- contest header (may wrap across lines) --------------------------
        if HEADER_END.search(stripped) and "WRITE-IN" not in stripped.upper():
            flush_details()
            header = stripped
            j = i
            while not VOTEFOR.search(header) and j + 1 < n \
                    and lines[j + 1].strip():
                j += 1
                header += " " + lines[j].strip()
                if VOTEFOR.search(header):
                    break
            if not VOTEFOR.search(header):
                R.warnings.append("unterminated contest header at line %d: "
                                  "%r" % (i + 1, header))
            current = map_contest(header)
            R.contests.append(current)
            if UNMAPPED and UNMAPPED[-1] == header:
                R.warnings.append("UNRECOGNIZED contest header %r" % header)
            had_agg[0] = False
            i = j + 1
            continue

        if current is None:
            i += 1
            continue

        low = stripped.lower()
        if low.startswith(("precincts reported", "times cast", "candidate",
                           "elector group", "counting group",
                           "registered voters:", "ballots cast:")):
            i += 1
            continue
        # column-header rows (contest start / repeated at page breaks) carry
        # no digits; the bare DEM/REP party line after a header too
        if re.match(r"^(election day|mail-in|provisional|total|democratic|"
                    r"republican)\b", low) \
                and not re.search(r"\d", stripped):
            i += 1
            continue
        if is_junk_line(stripped):
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
                # detail-block row (after the contest's "Total Votes" line):
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
                R.wi_total[current] = R.wi_total.get(current, 0) \
                    + int(nums[3] if len(nums) >= 4 else nums[0])
            i += 1
            continue

        # a lone number right after a truncated 3-number write-in row is its
        # wrapped Total column
        if pending_tot[0] is not None:
            pending_tot[0] = None
            nums = nums_from(stripped.split())
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
        nums2 = nums_from(toks[pi:])
        if len(nums2) >= 4:
            total, edv, miv, prv = nums2[3], nums2[0], nums2[1], nums2[2]
        elif nums2:
            total, edv, miv, prv = nums2[0], "", "", ""
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


PAGE_JUNK = re.compile(
    r"^\s*(page[:\s]|report generated|election summary report|closed primary|"
    r"official results)", re.IGNORECASE)


def is_junk_line(stripped):
    if re.search(r"\d{1,2}/\d{1,2}/\d{2,4}\s", " " + stripped) \
            and re.search(r"\d\d:\d\d:\d\d", stripped):
        return True
    if re.match(r"^\s*page:?\s*\d+", stripped, re.I):
        return True
    return bool(PAGE_JUNK.match(stripped))


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_totals(R):
    """Candidate rows + Write-ins vs each contest's 'Total Votes'."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in R.rows:
        key = (r[1], r[2], r[3])
        try:
            agg[key] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is None:
            continue
        s = agg.get(key, 0)
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
    for h in UNMAPPED:
        print("WARNING unmapped contest header: %r" % h)
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