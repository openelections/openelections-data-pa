#!/usr/bin/env python3
"""Carbon County 2023 primary — county-level and precinct-level results.

Two source formats, auto-detected from the file content:

1. Dominion "Election Summary Report / Closed Primary" county summary
   (parse_esr2 from pa_2023_summary_common; parse_county() below).
2. Dominion "Statement of Votes Cast" per-precinct report (505 pages;
   parse_sovc() below): per contest, page tables of
     [Precinct | Times Cast | Registered Voters] +
     [Precinct | ballot-candidate "(DEM)/(REP)" vote columns | "Total Votes"
      | named "Qualified Write In" columns | "Write-In" (unresolved) column]
   Candidate/write-in names are stacked header words above the column
   labels; columns are mapped to data numbers by x-position (each number's
   right edge falls in the label region [marker_x, next_marker_x)).  Precinct
   names wrap across up to 3 physical lines (name / numbers / name-tail);
   wrapped tail lines are detected as fragment-only lines.  Per precinct,
   write-ins = all "Qualified Write In" columns + the unresolved column
   (this reproduces the county summary's aggregate Write-in row, which
   includes unresolved write-ins).

Contest headers carry the party: "County Commissioners (DEM) (Vote for 2)".
map_contest strips "(DEM)"/"(REP)" and "(Vote for N)" and returns
(office, district, party).  In the summary, candidate rows carry a per-row
party token equal to the header party (checked; see validate log).

Office/district conventions mirror the county's own 2023 general file
(2023/20231107__pa__general__county.csv, Carbon rows / its parser
pa_carbon_general_2023_results_parser.py):
  "Council - Beaver Meadows Borough (4 Year Term)"
      -> office "Borough Council (4 Year)", district "Beaver Meadows Borough"
  "Supervisor - Banks Township (6 Year Term)"
      -> "Township Supervisor (6 Year)", "Banks Township"
  "Magisterial District Judge 56-3-01"
      -> "Magisterial District Judge", "Magisterial District 56-3-01"
  "<X> Area School District School Director (4 Year Term)"
      -> "School Director (4 Year)", "<X> Area School District"

The summary engine's check_totals() is party-blind (keys on office+
district), so paired DEM/REP contests of the same office would show
spurious mismatches; parse_county() reconciles each contest separately by
contest block (each block starts with its Undervotes row; verified
146 Undervotes == 146 contest headers == 146 "Total Votes" lines).

Usage: pa_carbon_primary_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import csv
import re
import sys

from pa_2023_summary_common import parse_esr2, write_csv, check_totals, \
    clean_name, FIELDNAMES

# "Supervisor - East Penn" in this source = "East Penn Township" in the
# county's general file (the primary header drops the township suffix).
MUNI_FIXUPS = {"East Penn": "East Penn Township"}


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    party = ""
    m = re.search(r"\((DEM|REP)\)", h, re.I)
    if m:
        party = m.group(1).upper()
        h = (h[:m.start()] + " " + h[m.end():]).strip()
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h).strip()
    term = None
    m = re.search(r"\((\d) Year Term\)", h, re.I)
    if m:
        term = m.group(1)
        h = (h[:m.start()] + " " + h[m.end():]).strip()
    h = re.sub(r"\s+", " ", h).strip()

    fixed = {
        "Justice of the Supreme Court": "Justice of the Supreme Court",
        "Judge of the Superior Court": "Judge of the Superior Court",
        "Judge of the Commonwealth Court":
            "Judge of the Commonwealth Court",
        "County Commissioners": "County Commissioner",
        "District Attorney": "District Attorney",
        "Controller": "Controller",
        "Coroner": "Coroner",
        "Prothonotary": "Prothonotary",
        "Recorder of Deeds": "Recorder of Deeds",
        "Sheriff": "Sheriff",
    }
    district = ""
    if h in fixed:
        office = fixed[h]
    else:
        m = re.match(r"^Magisterial District Judge (\d{2}-\d-\d{2})$", h)
        if m:
            office = "Magisterial District Judge"
            district = "Magisterial District " + m.group(1)
        else:
            m = re.match(r"^(Supervisor|Auditor|Council|Mayor|Tax Collector)"
                         r"\s*-\s*(.+)$", h)
            if m:
                base, rest = m.group(1), m.group(2).strip()
                office = {
                    "Supervisor": "Township Supervisor",
                    "Auditor": "Township Auditor",
                    "Council": "Borough Council",
                    "Mayor": "Mayor",
                    "Tax Collector": "Tax Collector",
                }[base]
                district = MUNI_FIXUPS.get(rest, rest)
            else:
                m = re.match(r"^(.+?) School Director$", h)
                if m and "School District" in m.group(1):
                    office = "School Director"
                    district = m.group(1).strip()
                else:
                    office = h
    if term:
        office += f" ({term} Year)"
    return office, district, party


# --------------------------------------------------------------------------
# county summary (Dominion ESR2)
# --------------------------------------------------------------------------

def reconcile_party_aware(R):
    """Contest-block reconciliation for the county summary.

    In this report every contest block begins with exactly one "Undervotes"
    row (printed even when 0; verified: 146 Undervotes lines == 146 contest
    headers == 146 "Total Votes" lines).  Rows are in document order, so the
    k-th Undervotes row starts the k-th contest; rows after it (Overvotes,
    candidates, Write-ins) belong to that contest until the next Undervotes
    row.  Candidate rows are checked against the header party."""
    meta_offices = ("Registered Voters", "Ballots Cast",
                    "Ballots Cast - Blank")
    data_rows = [r for r in R.rows if r[1] not in meta_offices]

    starts = [i for i, r in enumerate(data_rows) if r[4] == "Undervotes"]
    if len(starts) != len(R.contests):
        return (None, None,
                f"Undervotes-row count {len(starts)} != contest count "
                f"{len(R.contests)}")

    sums = {c: [0, 0, 0, 0] for c in R.contests}  # total, ed, mail, pr
    bad_party = []
    block = -1
    startset = set(starts)
    for i, r in enumerate(data_rows):
        if i in startset:
            block += 1
        c = R.contests[block]
        if r[3] and r[3] != c[2]:
            bad_party.append((c, r))
        if r[4] in ("Overvotes", "Undervotes"):
            continue  # source's Total Votes excludes over/under
        try:
            v = int(r[5])
        except (TypeError, ValueError):
            v = 0
        sums[c][0] += v
        for j, col in enumerate((6, 7, 8)):
            try:
                sums[c][j + 1] += int(r[col] or 0)
            except (TypeError, ValueError):
                pass

    bad = []
    for c in R.contests:
        tot = R.totals.get(c)
        if tot is not None and sums[c][0] != tot:
            bad.append((c, sums[c][0], tot))
    return bad, sums, bad_party


def county_contest_values(R):
    """Per contest: {key -> {"cand": {name: votes}, "wi": votes,
    "total": printed Total Votes}} using the Undervotes-row block rule."""
    meta_offices = ("Registered Voters", "Ballots Cast",
                    "Ballots Cast - Blank")
    data_rows = [r for r in R.rows if r[1] not in meta_offices]
    starts = [i for i, r in enumerate(data_rows) if r[4] == "Undervotes"]
    out = {}
    startset = set(starts)
    block = -1
    for i, r in enumerate(data_rows):
        if i in startset:
            block += 1
        c = R.contests[block]
        d = out.setdefault(c, {"cand": {}, "wi": 0, "total": None})
        if r[4] in ("Overvotes", "Undervotes"):
            continue
        if r[4] == "Write-ins":
            try:
                d["wi"] = int(r[5])
            except (TypeError, ValueError):
                pass
        else:
            d["cand"][r[4]] = int(r[5])
    for c, tot in R.totals.items():
        if c in out:
            out[c]["total"] = tot
    return out


def parse_county(src, dst):
    R = parse_esr2(src, "Carbon", map_contest)
    # The shared engine emits Write-ins/Undervotes/Overvotes rows with an
    # empty party; the repo-wide primary convention (matching the published
    # 2020 primary county files) stamps the CONTEST's party on them so the
    # DEM/REP sections stay distinct under the duplicate_entries test.
    # Metadata rows (Registered Voters / Ballots Cast) stay party-empty.
    # Party comes from the row's contest block (blocks start at each
    # Undervotes row, mirroring county_contest_values), since paired
    # DEM/REP contests share the same (office, district) key.
    meta_offices = ("Registered Voters", "Ballots Cast", "Ballots Cast - Blank")
    nonvote = ("Write-ins", "Undervotes", "Overvotes")
    data_idx = [i for i, r in enumerate(R.rows)
                if r[1] not in meta_offices]
    startset = {i for i in data_idx if R.rows[i][4] == "Undervotes"}
    block = -1
    filled = 0
    for i in data_idx:
        r = R.rows[i]
        if i in startset:
            block += 1
        if block < 0 or block >= len(R.contests):
            continue
        party = R.contests[block][2]
        if r[4] in nonvote and not r[3] and party:
            r[3] = party
            filled += 1
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst} (party stamped on {filled} "
          f"write-in/over/undervote rows)")
    print(f"contests parsed: {len(R.contests)}")

    bad = check_totals(R)
    if bad:
        print(f"engine check_totals: {len(bad)} party-blind key collisions "
              f"(expected in a closed primary; see party-aware check below)")
    else:
        print("engine check_totals: all contests reconcile")

    bad, sums, bad_party = reconcile_party_aware(R)
    if isinstance(bad, str):
        print(f"WARNING reconciliation aborted: {bad}")
        return R
    if bad_party:
        for c, r in bad_party:
            print(f"WARNING row party {r[3]!r} != header party {c[2]!r} "
                  f"in {c[0]} | {c[1]}: {r[4]}")
    if bad:
        for (office, dist, party), a, t in bad:
            print(f"WARNING {party} {office} | {dist}: rows sum {a} != "
                  f"Total Votes {t}")
    else:
        print("party-aware contest totals check: all contests reconcile")
    return R


# --------------------------------------------------------------------------
# per-precinct report (Dominion "Statement of Votes Cast")
# --------------------------------------------------------------------------

SOVC_CONTEST = re.compile(r"^(.*)\((DEM|REP)\)\s*\(Vote for (\d+)\)\s*$")

SOVC_KEYWORDS = {
    "Precinct", "Times", "Cast", "Registered", "Voters", "Total", "Votes",
    "Qualified", "Write", "In", "Unresolved", "Carbon", "County",
    "Cumulative", "(DEM)", "(REP)",
}

SOVC_META_PREFIXES = ("Carbon County", "Cumulative")

CAND, TOTAL, QWI, WIN = "CAND", "TOTAL", "QWI", "WIN"

FRONT_FURNITURE = re.compile(
    r"^(Page:|Registered\b|Voters$|Precinct\b|Carbon County$|"
    r"Statement of Votes Cast$|Closed Primary$|SOVC for:|May 16, 2023$)")

FRONT_CONTEST = re.compile(r"\(DEM\)|\(REP\)|Times Cast|Total Votes")


def parse_front_table(text_path):
    """Parse the report's front 'Statement of Votes Cast' summary table
    (per-precinct Registered Voters / Cards Cast / Voters Cast / turnout).

    The contest pages' per-ballot-style 'Registered Voters' column only
    covers the DEM+REP ballot styles; this front table carries the
    precinct's complete registered count, so it is the source for the RV
    meta rows.  Long precinct names wrap around the numeric columns
    (words-only lines before and/or after the numbers line), so each
    word-only continuation line is classified by lookahead: if the next
    content line is a numbers-only line, this line begins the next
    entry; otherwise it wraps the current entry's name.  Returns
    {precinct name: (registered, cards cast)}."""
    text = open(text_path, encoding="utf-8").read().replace("\f", "\n")
    lines = text.split("\n")
    entries = []           # [name words, rv or None, cards or None]
    for idx, raw in enumerate(lines):
        s = raw.strip()
        if not s or FRONT_FURNITURE.match(s):
            continue
        if FRONT_CONTEST.search(s):
            break
        toks = _tokens_with_x(raw)
        nums = [t for t, x in toks if re.fullmatch(r"[\d,]+", t)]
        words = [(t, x) for t, x in toks
                 if not re.fullmatch(r"[\d,]+", t) and "%" not in t]
        if len(nums) >= 3 and any(t.endswith("%") for t, x in toks):
            rv, cards = int(nums[0].replace(",", "")), \
                int(nums[1].replace(",", ""))
            if entries and entries[-1][1] is None:
                entries[-1][0].extend(t for t, x in words)
                entries[-1][1] = rv
                entries[-1][2] = cards
            else:
                entries.append([[" ".join(t for t, x in words).strip()],
                                rv, cards])
        elif words and entries:
            k = idx + 1
            while k < len(lines) and (not lines[k].strip()
                                      or FRONT_FURNITURE.match(
                                          lines[k].strip())):
                k += 1
            nt = _tokens_with_x(lines[k]) if k < len(lines) else []
            nnums = [t for t, x in nt if re.fullmatch(r"[\d,]+", t)]
            nwords = [t for t, x in nt
                      if not re.fullmatch(r"[\d,]+", t) and "%" not in t]
            if len(nnums) >= 3 and not nwords:
                entries.append([[" ".join(t for t, x in words).strip()],
                                None, None])
            else:
                entries[-1][0].extend(t for t, x in words)
    out = {}
    totals = None
    for ws, rv, cards in entries:
        name = " ".join(ws).strip()
        if not name or rv is None:
            continue
        if name.startswith("Carbon County"):
            # county TOTAL line(s) (the trailing 'Cumulative ...' words
            # are column-header debris wrapped onto the name)
            totals = (rv, cards)
        else:
            out[name] = (rv, cards)
    return out, totals


def _tokens_with_x(line):
    out = []
    idx = 0
    for t in line.split():
        x = line.index(t, idx)
        out.append((t, x))
        idx = x + len(t)
    return out


class _SovcRec:
    """One precinct's row under assembly (wrapped names span lines)."""

    __slots__ = ("parts", "rparts", "tc", "rv", "cands", "total", "wi",
                 "saw_l", "saw_r")

    def __init__(self):
        self.parts = []
        self.rparts = []
        self.tc = None
        self.rv = None
        self.cands = []
        self.total = None
        self.wi = 0
        self.saw_l = False
        self.saw_r = False


def parse_sovc(text_path, county, map_contest):
    """Parse the Dominion SOVC per-precinct report.

    Returns (rows, problems, printed-total mismatches, contests,
    contest_order, pmeta)."""
    text = open(text_path, encoding="utf-8").read().replace("\f", "\n")
    lines = text.split("\n")

    contest_order = []
    contests = {}   # key -> {"prec": {name -> data}}
    cur_key = None
    rec = None
    markers = []        # [(x, type)] right-table column markers (page order)
    colnames = {}       # marker index -> [name words]
    cols = []           # finalized, x-sorted [(x, type, name)]
    split_x = None      # x of the right table's Precinct name column
    in_data = False
    hdrbuf = []         # header-zone lines of the current page
    detail_pname = ""   # set on write-in-detail pages (single-precinct
                        # contests); empty on normal pages
    problems = []

    def get_contest(key):
        if key not in contests:
            contests[key] = {"prec": {}}
            contest_order.append(key)
        return contests[key]

    def close_rec():
        nonlocal rec
        if rec is None:
            return
        name = " ".join(rec.parts).strip() or " ".join(rec.rparts).strip()
        myrec, rec = rec, None
        if not name or name.startswith(SOVC_META_PREFIXES):
            return
        if myrec.tc is None and not myrec.cands and not myrec.wi \
                and myrec.total is None:
            return  # name-only fragment with no data
        if cur_key is None:
            problems.append(f"data row outside any contest: {name!r}")
            return
        cd = get_contest(cur_key)
        p = cd["prec"].setdefault(name, {"rv": None, "tc": {}, "cand": {},
                                         "total": None, "wi": 0})
        if myrec.rv is not None:
            p["rv"] = myrec.rv if p["rv"] is None else max(p["rv"], myrec.rv)
        if myrec.tc is not None:
            tp = cur_key[2]
            p["tc"][tp] = max(myrec.tc, p["tc"].get(tp, 0))
        for nm, v in myrec.cands:
            p["cand"][nm] = p["cand"].get(nm, 0) + v
        if myrec.total is not None:
            p["total"] = myrec.total
        p["wi"] += myrec.wi

    def finalize_columns():
        """Sort the page's markers by x and attach assembled names."""
        nonlocal cols
        cols = []
        for k in sorted(range(len(markers)), key=lambda k: markers[k][0]):
            mx, mt = markers[k]
            nm = clean_name(" ".join(colnames.get(k, [])).strip())
            cols.append((mx, mt, nm))

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1
            continue
        if re.match(r"^Page: \d+ of \d+", stripped):
            close_rec()
            in_data = False
            markers = []
            colnames = {}
            cols = []
            split_x = None
            hdrbuf = []
            detail_pname = ""
            i += 1
            continue
        m = SOVC_CONTEST.match(stripped)
        if m and not in_data:
            close_rec()
            header = re.sub(r"\s+", " ", m.group(0)).strip()
            office, district, party = map_contest(header)
            cur_key = (office, district, party)
            detail_pname = ""
            get_contest(cur_key)
            i += 1
            continue
        if not in_data and stripped in ("DEM", "REP"):
            i += 1
            continue
        if cur_key is None:
            i += 1
            continue

        toks = _tokens_with_x(raw)
        nums = [(t, x) for t, x in toks if re.fullmatch(r"[\d,]+", t)]
        words = [(t, x) for t, x in toks
                 if not re.fullmatch(r"[\d,]+", t)]

        if not in_data and not nums:
            # header zone: buffer the line; markers and names are assigned in
            # two passes once all of the page's markers are known (candidate
            # name lines can precede the "(DEM)" label lines)
            hdrbuf.append(raw)
            i += 1
            continue

        if not in_data:
            # first numeric line of the page: finalize the column model
            detail_page = False
            for hline in hdrbuf:
                hw = [(t, x) for t, x in _tokens_with_x(hline)
                      if not re.fullmatch(r"[\d,]+", t)]
                if hw and hw[0] == ("Carbon", 0) \
                        and any(t == "-" for t, x in hw[:4]) \
                        and any(t == "Total" for t, x in hw[:5]):
                    # write-in-detail page of a single-precinct contest:
                    # columns are [Carbon County - Total | Cumulative
                    # - Total | ... | <precinct name>]; only the rightmost
                    # number (the precinct column) is per-precinct data
                    detail_page = True
                for t, x in hw:
                    if t in ("(DEM)", "(REP)"):
                        markers.append((x, CAND))
                    elif t == "Total":  # only in the "Total Votes" label
                        markers.append((x, TOTAL))
                    elif t == "Qualified":
                        markers.append((x, QWI))
                    elif t.startswith("Write-"):
                        markers.append((x, WIN))
                if hw and hw[0][0] == "Precinct" and hw[0][1] == 0:
                    second = [x for t, x in hw if t == "Precinct" and x > 0]
                    if second:
                        split_x = second[0]
            if detail_page and not detail_pname:
                dw = []
                for hline in hdrbuf:
                    for t, x in _tokens_with_x(hline):
                        if t not in SOVC_KEYWORDS and not re.fullmatch(
                                r"[-.\d,]+", t) and 88 <= x < 146:
                            dw.append(t)
                detail_pname = " ".join(dw).strip().rstrip(" -").strip()
                if not detail_pname and len(nums) >= 3:
                    problems.append(
                        f"write-in-detail page with no precinct label near "
                        f"{stripped!r}")
            for hline in hdrbuf:
                hw = [(t, x) for t, x in _tokens_with_x(hline)
                      if not re.fullmatch(r"[\d,]+", t)]
                cidx = [k for k, (mx, mt) in enumerate(markers)
                        if mt != TOTAL]
                cidx.sort(key=lambda k: markers[k][0])

                def region_of(x):
                    reg = None
                    for k in cidx:
                        if markers[k][0] <= x + 2:
                            reg = k
                        else:
                            break
                    return reg

                # name columns are left-aligned at their marker; cluster by
                # gap < 4, but force a split when the region changes with a
                # gap >= 2 (adjacent names can be only 3px apart, while a
                # name overflowing its column has no inter-word gap)
                groups = []
                prev_reg = None
                for t, x in hw:
                    if t in SOVC_KEYWORDS:
                        continue
                    reg = region_of(x)
                    gap = (x - (groups[-1][-1][1] + len(groups[-1][-1][0]))
                           if groups else 999)
                    if groups and (gap < 4 and not (reg != prev_reg
                                                    and gap >= 2)):
                        groups[-1].append((t, x))
                    else:
                        groups.append([(t, x)])
                    prev_reg = reg
                for g in groups:
                    chosen = region_of(g[0][1])
                    if chosen is None:
                        # no column marker at/before this cluster: it labels
                        # an unlabeled write-in column (e.g. a named
                        # "Qualified Write In" column whose label line is
                        # missing); make it an implicit QWI column
                        chosen = len(markers)
                        markers.append((g[0][1], QWI))
                    colnames.setdefault(chosen, []).extend(t for t, x in g)
            hdrbuf = []
            finalize_columns()
            in_data = True

        if detail_pname:
            # write-in-detail page rows: one row per write-in candidate;
            # the rightmost number is the precinct's count (the other
            # numbers are Carbon County / Cumulative totals)
            if nums:
                t, x = max(nums, key=lambda tx: tx[1])
                v = int(t.replace(",", ""))
                if v:
                    cd = get_contest(cur_key)
                    p = cd["prec"].setdefault(
                        detail_pname, {"rv": None, "tc": {}, "cand": {},
                                       "total": None, "wi": 0})
                    p["wi"] += v
            i += 1
            continue

        # ---- data line ----
        if split_x is not None:
            lnums = [(t, x) for t, x in nums if x < split_x]
            rnums = [(t, x) for t, x in nums if x >= split_x]
            lfrag = [(t, x) for t, x in words
                     if x < (lnums[0][1] if lnums else 28)]
            rfrag = [(t, x) for t, x in words
                     if split_x <= x < split_x + 28]
        else:
            lnums, rnums = [], nums
            lfrag = []
            rfrag = [(t, x) for t, x in words if x < 40]

        new = False
        if rec is None:
            new = True
        elif lnums and rec.saw_l:
            new = True
        elif rnums and rec.saw_r:
            new = True
        if new:
            close_rec()
            rec = _SovcRec()
        for t, x in lfrag:
            rec.parts.append(t)
        for t, x in rfrag:
            rec.rparts.append(t)
        if lnums:
            if len(lnums) >= 2:
                rec.tc = int(lnums[0][0].replace(",", ""))
                rec.rv = int(lnums[1][0].replace(",", ""))
                rec.saw_l = True
            else:
                problems.append(f"unexpected left row {stripped!r}")
        if rnums:
            for t, x in rnums:
                right_edge = x + len(t)
                # Column assignment.  Numbers are right-aligned inside
                # their column, but on narrow pages a number's right edge
                # can cross the NEXT column's label (Hazleton SD 2yr DEM:
                # "Carol Makuta" write-in column at x=33 holds "52"@42
                # whose edge 54 crosses the Total label at x=53).  So:
                #   - non-Total columns take the rightmost marker whose x
                #     is at/before the number's right edge (+1 slack);
                #   - a Total column qualifies only if the number's edge
                #     reaches its label AND the number starts no more
                #     than 2px before it (Total numbers are right-aligned
                #     to the label itself).
                best = None
                for k, (mx, mt, nm) in enumerate(cols):
                    if mt == TOTAL:
                        if right_edge >= mx and x >= mx - 2:
                            best = k
                    elif mx <= right_edge + 1:
                        best = k
                if best is None:
                    # fall back to nearest-marker by right edge
                    bestd = 10 ** 9
                    for k, (mx, mt, nm) in enumerate(cols):
                        if mt == TOTAL:
                            d = abs(right_edge - mx)
                        else:
                            d = right_edge - mx
                            if d < -2:
                                d = -d + 10000
                        if 0 <= d < bestd:
                            bestd, best = d, k
                if best is None:
                    problems.append(f"unmapped number {t}@{x} "
                                    f"in {stripped!r}")
                    continue
                v = int(t.replace(",", ""))
                mt = cols[best][1]
                if mt == CAND:
                    nm = cols[best][2] or f"col{best}"
                    rec.cands.append((nm, v))
                elif mt in (QWI, WIN):
                    rec.wi += v
                elif mt == TOTAL:
                    rec.total = v
            rec.saw_r = True
        i += 1
    close_rec()

    # ---- build rows ----
    rows = []
    problems2 = []

    # Registered Voters / Ballots Cast per precinct.  The per-page
    # "Registered Voters" column is per ballot style (party), so the RV row
    # is the DEM+REP sum; Ballots Cast = DEM Times Cast + REP Times Cast
    # (both verified against the front summary table).
    pmeta = {}
    for key in contest_order:
        cd = contests[key]
        for pname, p in cd["prec"].items():
            pm = pmeta.setdefault(pname, {"rv": {}, "tc": {},
                                          "order": len(pmeta)})
            if p["rv"] is not None:
                tp = key[2]
                pm["rv"][tp] = max(p["rv"], pm["rv"].get(tp, 0))
            for tp, v in p["tc"].items():
                pm["tc"][tp] = max(v, pm["tc"].get(tp, 0))
    for pname, pm in sorted(pmeta.items(), key=lambda kv: kv[1]["order"]):
        rv = sum(pm["rv"].values()) if pm["rv"] else ""
        rows.append([county, pname, "Registered Voters", "", "", rv,
                     "", "", "", ""])
        bc = sum(pm["tc"].values()) if pm["tc"] else ""
        rows.append([county, pname, "Ballots Cast", "", "", bc,
                     "", "", "", ""])

    for key in contest_order:
        cd = contests[key]
        office, district, party = key
        for pname, p in cd["prec"].items():
            for nm in sorted(p["cand"]):
                rows.append([county, pname, office, district, party, nm,
                             p["cand"][nm], "", "", ""])
            rows.append([county, pname, office, district, party, "Write-ins",
                         p["wi"], "", "", ""])
            if p["total"] is not None:
                got = sum(p["cand"].values()) + p["wi"]
                if got != p["total"]:
                    problems2.append((key, pname, got, p["total"]))

    return rows, problems, problems2, contests, contest_order, pmeta


def parse_precinct(src, dst, county_summary=None):
    (rows, problems, problems2, contests, contest_order,
     pmeta) = parse_sovc(src, "Carbon", map_contest)
    # Registered Voters: the contest pages' per-ballot-style RV columns
    # only cover the DEM+REP ballot styles (their sum understates the
    # precinct's registered count wherever minor-party/no-style voters
    # exist).  The front summary table carries the complete count, so
    # stamp the front table's RV onto the meta rows and cross-check its
    # Cards Cast against the parsed Ballots Cast.
    front, ftotals = parse_front_table(src)
    rv_fixed = bc_bad = 0
    names = {r[1] for r in rows if r[2] == "Registered Voters"}
    for r in rows:
        if r[2] == "Registered Voters" and r[1] in front:
            r[5] = str(front[r[1]][0])
            rv_fixed += 1
        elif r[2] == "Ballots Cast" and r[1] in front:
            cards = front[r[1]][1]
            if cards and str(cards) != str(r[5]):
                bc_bad += 1
                problems.append(
                    f"front-table Cards Cast {cards} != parsed Ballots "
                    f"Cast {r[5]} for {r[1]!r}")
    for name in front:
        if name not in names:
            problems.append(f"front-table precinct {name!r} not parsed "
                            f"from contest pages")
    if ftotals:
        srv = sum(int(r[5]) for r in rows if r[2] == "Registered Voters"
                  and r[5] != "")
        sbc = sum(int(r[5]) for r in rows if r[2] == "Ballots Cast"
                  and r[5] != "")
        tag = "OK" if (srv, sbc) == ftotals else "MISMATCH"
        problems.append(f"front-table county totals check: precinct RV "
                        f"sum {srv} / BC sum {sbc} vs county row "
                        f"{ftotals[0]} / {ftotals[1]} -> {tag}")
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct"] + FIELDNAMES[1:])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {dst}")
    print(f"contests parsed: {len(contests)}; precincts: {len(pmeta)}; "
          f"front-table entries: {len(front)} (RV stamped on {rv_fixed})")
    for p in problems:
        print(f"PROBLEM: {p}")

    print(f"per-precinct printed-total check: {len(problems2)} mismatches "
          f"of {sum(len(cd['prec']) for cd in contests.values())} "
          f"precinct/contest cells")
    for key, pname, got, tot in problems2[:20]:
        print(f"  MISMATCH {key} @ {pname}: rows {got} != printed Total {tot}")

    # cross-check every contest against the county summary
    if county_summary:
        R = parse_esr2(county_summary, "Carbon", map_contest)
        cvals = county_contest_values(R)
        cand_bad = wi_bad = tot_bad = 0
        for key in contest_order:
            cv = cvals.get(key)
            if cv is None:
                print(f"  WARNING contest {key} not found in county summary")
                continue
            agg = {}
            wi = 0
            for pname, p in contests[key]["prec"].items():
                for nm, v in p["cand"].items():
                    agg[nm] = agg.get(nm, 0) + v
                wi += p["wi"]
            for nm, v in cv["cand"].items():
                if agg.get(nm) != v:
                    cand_bad += 1
                    print(f"  CANDIDATE MISMATCH {key} {nm!r}: precincts "
                          f"{agg.get(nm)} != county {v}")
            if wi != cv["wi"]:
                wi_bad += 1
                print(f"  WRITE-IN MISMATCH {key}: precincts {wi} != "
                      f"county {cv['wi']}")
            if cv["total"] is not None:
                ptot = sum(p["total"] or 0
                           for p in contests[key]["prec"].values())
                if ptot != cv["total"]:
                    tot_bad += 1
                    print(f"  TOTAL MISMATCH {key}: sum of printed precinct "
                          f"totals {ptot} != county Total Votes {cv['total']}")
        print(f"cross-check vs county summary: {len(contest_order)} contests; "
              f"{cand_bad} candidate mismatches, {wi_bad} write-in "
              f"mismatches, {tot_bad} total mismatches")


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt> <output.csv> "
                 f"[--county-summary <path>]")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        from pa_2023_summary_common import text_from_pdf
        src = text_from_pdf(src)
    text = open(src, encoding="utf-8").read()
    if "Statement of Votes Cast" in text:
        cs = None
        if "--county-summary" in args:
            cs = args[args.index("--county-summary") + 1]
        parse_precinct(src, dst, cs)
    else:
        parse_county(src, dst)


if __name__ == "__main__":
    main()