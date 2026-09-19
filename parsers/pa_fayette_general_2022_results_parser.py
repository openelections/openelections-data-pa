#!/usr/bin/env python3
"""Parse Fayette County, PA 2022 General Election "Statement of Votes Cast"
(SOVC) precinct-level report into OpenElections CSV format.

Source: pdftotext -layout extract of the county's 2,645-page Dominion SOVC
report (Fayette G-22 OFFICIAL Results StatementOfVotesCastRPT_1.pdf).

Layout: every contest page carries four rows per precinct (Election Day /
Mail-In / Provisional / Total), often split across a left "Times Cast /
Registered Voters" panel and a right candidate panel.  Candidate columns are
defined by marker lines: "(DEM)"/"(REP)"/... party codes, a bare "Write-in"
column, a "Total Votes" column and one "Qualified Write In" column per
qualified write-in candidate (thousands, mostly zero).  The front section
lists all 77 precincts with Registered Voters / Cards Cast per counting
group.  Contests covered: U.S. Senate, Governor, U.S. House 14, State Senate
32, State House 51, State House 52 (the report contains no judicial
retentions and no local offices).

Qualified-write-in groups that received any votes also get county-total
summary pages at the end of each contest; those are harvested for
verification only (precinct detail lives on the detail pages).

Usage:
    python pa_fayette_general_2022_results_parser.py <sovc_txt> <out_csv>
"""
import argparse
import csv
import re
import sys

COUNTY = "Fayette"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "early_voting", "provisional",
]

# --------------------------------------------------------------------------
# office normalization
# --------------------------------------------------------------------------

_OFFICE_RES = [
    (re.compile(r"^United States Senator$"), ("U.S. Senate", "")),
    (re.compile(r"^Governor and Lieutenant Governor$"), ("Governor", "")),
    (re.compile(r"^Representative in Congress (\d+)"), ("U.S. House", 1)),
    (re.compile(r"^Senator in the General Assembly (\d+)"),
     ("State Senate", 1)),
    (re.compile(r"^Representative in the General Assembly (\d+)"),
     ("State House", 1)),
]


def normalize_office(title):
    t = re.sub(r"\s+", " ", title).strip()
    for rx, (office, grp) in _OFFICE_RES:
        m = rx.match(t)
        if m:
            return (office, m.group(grp) if isinstance(grp, int) else grp)
    return (t, "")


# --------------------------------------------------------------------------
# page / column machinery (adapted from pa_fayette_general_2023 parser)
# --------------------------------------------------------------------------

_MARKER_RES = [
    ("party", re.compile(r"\(([A-Z]{2,7}(?:/[A-Z]{2,7})?)\)")),
    ("qwi", re.compile(r"Qualified Write In")),
    ("writein", re.compile(r"\bWrite-In\b")),
    ("writein", re.compile(r"\bWrite-in\b")),
    ("total", re.compile(r"Total Votes")),
]

_NUM_RE = re.compile(r"^\d[\d,]*$")
_JUNK_NAME_TOKENS = {"FAYETTE", "COUNTY", "PRECINCT", "VOTERS", "TIMES",
                     "CAST", "REGISTERED", "VOTER", "QUALIFIED", "WRITE",
                     "IN", "TOTAL", "VOTES", "UNRESOLVED", "OFFICIAL",
                     "RESULTS", "WRITE-IN", "CUMULATIVE"}

_DATA_RE = re.compile(
    r"^\s*(Election Day|Mail-In|Provisional|Total|Fayette County - Total)"
    r"\s+\d")
_SKIP_RE = re.compile(r"Cumulative|Fayette County - Total")
_ROW_LABEL_RE = re.compile(r"(Election Day|Mail-In|Provisional|Total)")
_CODE_RE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*$")


def _norm(name):
    return re.sub(r"\s+", " ", name).strip().upper()


def _fix_hyphen(name):
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"([A-Za-z0-9])- ", r"\1 - ", name)


def _has_marker(line):
    return any(rx.search(line) for _k, rx in _MARKER_RES)


def _header_lines(lines):
    """Header zone: everything before the first data line."""
    for i, l in enumerate(lines):
        if _DATA_RE.match(l):
            return lines[:i]
    return lines


def _collect_columns(header):
    """Columns sorted by x: (start, kind, party, name).

    kind: 'named' (party column), 'writein', 'total'.
    """
    raw = []
    for line in header:
        if re.search(r"\(Vote for \d+\)", line):
            continue
        for kind, rx in _MARKER_RES:
            for m in rx.finditer(line):
                if kind == "party":
                    raw.append([m.start(), "named", m.group(1), ""])
                elif kind == "total":
                    raw.append([m.start(), "total", "", ""])
                elif kind == "qwi":
                    raw.append([m.start(), "qwi", "", ""])
                else:
                    raw.append([m.start(), "writein", "", ""])
    if not raw:
        return []
    raw.sort(key=lambda r: (r[0],))
    cols = []
    for s, kind, party, name in raw:
        if any(c[1] == kind and not c[0] < s - 8 for c in cols):
            continue
        cols.append([s, kind, party, name])
    # candidate names: header tokens left-aligned at the column start
    bounds = sorted(c[0] for c in cols)
    for line in header:
        if re.search(r"\(Vote for \d+\)", line):
            continue
        for m in re.finditer(r"\S+", line):
            tok = m.group()
            if _norm(tok) in _JUNK_NAME_TOKENS:
                continue
            if tok.startswith("("):
                continue
            col = None
            for c in cols:
                nxt = min([b for b in bounds if b > c[0]] or [10 ** 6])
                if c[0] <= m.start() < nxt:
                    col = c
                    break
            if col is None or col[1] != "named":
                continue
            col[3] = (col[3] + " " + tok).strip()
    return cols


def _col_for_x(cols, x_end):
    """Column owning a value whose right edge is at x_end.

    Values are right-aligned inside each column's value area, which can end
    slightly past the next column's marker start, so a value belongs to the
    last column whose marker starts at/before the value's right edge (+2).
    """
    best = None
    for c in cols:
        if c[0] <= x_end + 2:
            best = c
        else:
            break
    return best


# --------------------------------------------------------------------------
# front section: precinct list + Registered Voters / Cards Cast
# --------------------------------------------------------------------------

_FRONT_SKIP_RE = re.compile(
    r"^Precinct$|Registered|Cards Cast|Statement|General Election|November|"
    r"SOVC|OFFICIAL|^Voters$|Turnout")


def _front_rows(lines):
    """Precinct list + Registered Voters / Cards Cast from the front pages.

    Each precinct: name line(s), then Election Day / Mail-In / Provisional /
    Total rows of '<RV> <Cards Cast> <Voters Cast> <pct>%'.  The Total row
    supplies RV and Cards Cast; the group rows supply the Cards Cast
    breakdown (Election Day / Mail-In / Provisional).  The name is assembled
    from the preceding fragment lines and matched by its trailing 'NN-NN'
    precinct code.
    """
    names, vals, codes = [], {}, {}
    pending = []
    cards = {}
    for line in lines:
        s = line.strip()
        if not s:
            continue
        m = re.match(r"(Election Day|Mail-In|Provisional|Total)\b", s)
        if m:
            nums = [t for t in s.split() if _NUM_RE.match(t)]
            if m.group(1) == "Total":
                joined = " ".join(pending)
                pending = []
                cm = _CODE_RE.search(joined)
                if not cm or len(nums) < 2:
                    cards = {}
                    continue
                code = "%s-%s" % (cm.group(1), cm.group(2))
                name = re.sub(r"\s+", " ", joined[:cm.start()]).strip()
                name = re.sub(r"[\s\-]+$", "", name)
                name = "%s - %s" % (_fix_hyphen(name), code)
                names.append(name)
                vals[name] = (int(nums[0].replace(",", "")),
                              int(nums[1].replace(",", "")),
                              cards.get("ED", 0), cards.get("MI", 0),
                              cards.get("PV", 0))
                codes[code] = name
                cards = {}
            else:
                key = {"Election Day": "ED", "Mail-In": "MI",
                       "Provisional": "PV"}[m.group(1)]
                if len(nums) >= 2:
                    cards[key] = int(nums[1].replace(",", ""))
            continue
        if _FRONT_SKIP_RE.search(s):
            continue
        if "Fayette County" in s and not re.search(r"\d", s):
            continue
        if re.search(r"%\s*$", s) and not re.search(r"[A-Za-z]", s):
            continue
        pending.append(s)
    return names, vals, codes


# --------------------------------------------------------------------------
# contest pages
# --------------------------------------------------------------------------

def _code_in(text, codes):
    m = _CODE_RE.search(text)
    if not m:
        return None
    return codes.get("%s-%s" % (m.group(1), m.group(2)))


def _clean_name(name):
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"([A-Za-z0-9])- ", r"\1 - ", name)
    return name


def parse_input(path):
    with open(path, errors="replace") as f:
        data = f.read()
    parts = re.split(r"\x0c?Page: (\d+) of \d+.*", data)
    pages = {}
    for i in range(1, len(parts), 2):
        pages[int(parts[i])] = parts[i + 1]
    warnings = []

    starts = []
    for p in sorted(pages):
        for l in pages[p].split("\n"):
            m = re.match(r"^\s*(.+?)\s*\(Vote for (\d+)\)\s*$", l)
            if m:
                starts.append((p, m.group(1)))
                break
    first_contest = starts[0][0]

    names, rv_cards, codes = _front_rows(
        [l for p in sorted(pages) if p < first_contest
         for l in pages[p].split("\n")])
    if len(names) != 77:
        warnings.append("expected 77 precincts in front section, got %d"
                        % len(names))

    contests = []   # (office, district, named_order, named_party, records)
    for ci, (start, title) in enumerate(starts):
        end = starts[ci + 1][0] if ci + 1 < len(starts) else max(pages) + 1
        office, district = normalize_office(title)
        named_order, named_party = [], {}
        records = {}   # code -> {'named': {name: {rt: v}}, 'wi': {rt}, 'tv'}
        for pg in range(start, end):
            lines = pages[pg].split("\n")
            header = _header_lines(lines)
            cols = _collect_columns(header)
            first_data = len(lines)
            for i, l in enumerate(lines):
                if _DATA_RE.match(l):
                    first_data = i
                    break
            if not cols or first_data == len(lines):
                continue    # summary / blank pages handled separately
            first_start = min(c[0] for c in cols)
            # the first precinct block's name lines sit at the very end of
            # the header zone (after the last structural line)
            structural = -1
            for i, l in enumerate(lines[:first_data]):
                if _has_marker(l) or re.search(
                        r"Precinct|Registered|Times Cast|Cumulative|"
                        r"Vote for|Fayette County|Statement|November|"
                        r"SOVC|OFFICIAL|Voters", l):
                    structural = i
            pending_frag = [l.strip()
                            for l in lines[structural + 1:first_data]
                            if l.strip()]
            cur = None
            for line in lines[first_data:]:
                s = line.strip()
                if not s:
                    continue
                if _SKIP_RE.search(s):
                    # 'Fayette County - Total' row ends the real precinct
                    # data; what follows is the Cumulative section, whose
                    # zero rows must not be attributed to the last precinct
                    done = True
                    break
                if not _DATA_RE.match(line):
                    # wrapped name fragment (may be letters-only, digits-only
                    # like '- 06-01', or a doubled two-panel copy)
                    pending_frag.append(s)
                    continue
                # numbers after the LAST counting label on the line (the
                # right panel repeats the label; left-panel numbers are
                # before it and must be ignored)
                labs = list(_ROW_LABEL_RE.finditer(line))
                off = labs[-1].end()
                lab = labs[-1].group(1)
                rowtype = {"Election Day": "ED", "Mail-In": "MI",
                           "Provisional": "PV", "Total": "TV"}[lab]
                # anchor the precinct from the pending name fragments
                if pending_frag:
                    code = _code_in(" ".join(
                        re.split(r"\s{2,}", f)[0] for f in pending_frag),
                        codes)
                    if code is not None:
                        cur = code
                    else:
                        warnings.append(
                            "page %d (%s): unmatched precinct fragments %r"
                            % (pg, title, pending_frag))
                    pending_frag = []
                if cur is None:
                    warnings.append("page %d (%s): row with no precinct"
                                    % (pg, title))
                    continue
                rec = records.setdefault(
                    cur, {"named": {}, "wi": {}, "wq": {}, "tv": None})
                for m in re.finditer(r"\d[\d,]*", line[off:]):
                    if not _NUM_RE.match(m.group()):
                        continue
                    x_end = off + m.end()
                    col = _col_for_x(cols, x_end)
                    if col is None:
                        warnings.append(
                            "page %d (%s): value %s at x=%d not in a "
                            "column" % (pg, title, m.group(), x_end))
                        continue
                    _s, kind, party, name = col
                    val = int(m.group().replace(",", ""))
                    if kind == "total":
                        rec["tv"] = val
                    elif kind in ("writein", "qwi"):
                        key = "wi" if kind == "writein" else "wq"
                        rec[key][rowtype] = rec[key].get(rowtype, 0) + val
                    else:
                        if not name:
                            warnings.append(
                                "page %d (%s): named column with no name"
                                % (pg, title))
                            continue
                        name = _clean_name(name)
                        if office == "Governor":
                            name = re.sub(r"\s*/.*$", "", name).strip()
                        vals = rec["named"].setdefault(name, {})
                        if rowtype in vals:
                            warnings.append(
                                "page %d (%s): duplicate %s / code %s / %s"
                                % (pg, title, name, cur, rowtype))
                        vals[rowtype] = vals.get(rowtype, 0) + val
                        if name not in named_party:
                            named_party[name] = party
                            named_order.append(name)
            if pending_frag:
                warnings.append("page %d (%s): leftover fragments %r"
                                % (pg, title, pending_frag))
        contests.append((office, district, named_order, named_party,
                         records, title))
    return names, rv_cards, codes, contests, warnings


# --------------------------------------------------------------------------
# summary-page harvesting (verification only)
# --------------------------------------------------------------------------

def harvest_summaries(pages):
    """County-total pages of each contest: candidate -> county total.

    Summary pages carry only 'Fayette County - Total' and 'Cumulative' rows
    (no precinct blocks); some are rotated and extract garbled, in which case
    the header columns still yield the candidate totals.
    """
    out = []
    for p in sorted(pages):
        lines = pages[p].split("\n")
        first_data = len(lines)
        for i, l in enumerate(lines):
            if _DATA_RE.match(l):
                first_data = i
                break
        if first_data == len(lines):
            continue
        header = _header_lines(lines)
        cols = _collect_columns(header)
        if not cols:
            continue
        is_summary = True
        for line in lines[first_data:]:
            s = line.strip()
            if not s or _SKIP_RE.search(s):
                continue
            if _DATA_RE.match(line):
                is_summary = False
            break
        if not is_summary:
            continue
        got, tv = {}, None
        for line in lines[first_data:]:
            if "Fayette County - Total" not in line:
                continue
            labs = list(_ROW_LABEL_RE.finditer(line))
            if not labs:
                continue
            off = labs[-1].end()
            for m in re.finditer(r"\d[\d,]*", line):
                if not _NUM_RE.match(m.group()) or m.end() < off:
                    continue
                col = _col_for_x(cols, (m.start() + m.end()) / 2.0)
                if col is None:
                    continue
                v = int(m.group().replace(",", ""))
                _s, kind, _party, name = col
                if kind == "total":
                    tv = v
                elif kind == "named" and name:
                    got[_clean_name(name)] = v
            break
        if got or tv is not None:
            out.append((p, got, tv))
    return out


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def build_rows(precincts, codes, rv_cards, contests, county):
    code_of = {v: k for k, v in codes.items()}
    rows = []
    for p in precincts:
        rv, cards, ed, mi, pv = rv_cards.get(p, ("", "", "", "", ""))
        rows.append([county, p, "Registered Voters", "", "", "", rv, "", "",
                     ""])
        rows.append([county, p, "Ballots Cast", "", "", "", cards, ed, mi,
                     pv])
    tv_mismatch = []
    for office, district, named_order, named_party, records, title \
            in contests:
        for p in precincts:
            rec = records.get(p)   # records are keyed by precinct name
            if rec is None:
                continue
            total_named = 0
            for name in named_order:
                vals = rec["named"].get(name)
                if not vals:
                    continue
                v = vals.get("TV", 0)
                total_named += v
                rows.append([county, p, office, district,
                             named_party.get(name, ""), name, v,
                             vals.get("ED", 0), vals.get("MI", 0),
                             vals.get("PV", 0)])
            # The unnamed Write-in column is the TOTAL write-in count for
            # the precinct (the source's Total Votes column = named
            # candidates + this column).  The Qualified Write In candidate
            # columns are a subset breakdown of that same number (each
            # write-in vote went to a qualified candidate), so they must
            # NOT be added on top.
            wi = rec["wi"]
            rows.append([county, p, office, district, "", "Write Ins",
                         wi.get("TV", 0),
                         wi.get("ED", 0), wi.get("MI", 0), wi.get("PV", 0)])
            if rec["tv"] is not None and \
                    rec["tv"] != total_named + wi.get("TV", 0):
                tv_mismatch.append((title, p, rec["tv"],
                                    total_named + wi.get("TV", 0)))
    return rows, tv_mismatch


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path")
    ap.add_argument("output_path")
    ap.add_argument("--county", default=COUNTY)
    args = ap.parse_args(argv)

    names, rv_cards, codes, contests, warnings = parse_input(args.input_path)
    for w in warnings[:60]:
        print("WARNING:", w, file=sys.stderr)
    if len(warnings) > 60:
        print("... %d more warnings" % (len(warnings) - 60), file=sys.stderr)
    rows, tv_mismatch = build_rows(names, codes, rv_cards, contests, COUNTY)
    for title, code, tv, s in tv_mismatch[:40]:
        print("TV MISMATCH %s / %s: total %s != named+wi %s"
              % (title, code, tv, s), file=sys.stderr)
    if len(tv_mismatch) > 40:
        print("... %d more TV mismatches" % (len(tv_mismatch) - 40),
              file=sys.stderr)

    with open(args.output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDNAMES)
        w.writerows(rows)
    print("precincts: %d, contests: %d, rows: %d, TV mismatches: %d"
          % (len(names), len(contests), len(rows), len(tv_mismatch)),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    main()