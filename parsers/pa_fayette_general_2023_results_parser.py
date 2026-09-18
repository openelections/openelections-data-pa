#!/usr/bin/env python3
"""Parse Fayette County, PA 2023 General Election "Statement of Votes Cast"
(SOVC) precinct-level report into OpenElections CSV format.

Source: pdftotext -layout extract of the county's 736-page SOVC report
(e.g. work2023/txt/Fayette__Fayette_County_Precinct_Results_2023_General.txt).

Layout: each contest occupies a run of pages; a header line
"<Contest> (Vote for N)" starts a contest.  On every page candidates are
COLUMNS: subheader lines carry "(DEM)"/"(REP)"/"(DEM/REP)" party markers,
"Qualified Write In" / "Unresolved / Write-In" columns and a "Total Votes"
column; data rows list precincts in canonical order (the left panel repeats
Registered Voters / Cards Cast per precinct).  A contest's main panel may
span several pages with different column sets (e.g. a page of DEM candidate
columns, then a page with the REP columns plus Total Votes); each write-in
candidate gets its own page with a single "Qualified Write In" column
listing every precinct.  Pages before the first contest carry the
precinct list with Registered Voters / Cards Cast / Voters Cast / % Turnout.

Usage:
    python pa_fayette_general_2023_results_parser.py <sovc_txt> <out_csv>
"""
import argparse
import csv
import re
import sys

COUNTY = "Fayette"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

# --------------------------------------------------------------------------
# office normalization: raw "(Vote for N)" contest title -> (office, district)
# --------------------------------------------------------------------------

_EXACT = {
    "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas 14th Judicial District": (
        "Judge of the Court of Common Pleas", ""),
    "District Attorney": ("District Attorney", ""),
    "Sheriff": ("Sheriff", ""),
    "County Commissioner": ("County Commissioner", ""),
    "County Controller": ("County Controller", ""),
    "Clerk of Courts": ("Clerk of Courts", ""),
    "Register of Wills/Clerk of the Orphans Court": (
        "Register of Wills & Clerk of Orphans' Court", ""),
    "Prothonotary": ("Prothonotary", ""),
    "Coroner": ("Coroner", ""),
}

_RETENTION_RE = re.compile(
    r"^(Superior Court|Court of Common Pleas 14th Judicial)\s+-\s+"
    r"(.+?)\s*-?\s+Retention Question$", re.IGNORECASE)

_MDJ_RE = re.compile(r"^Magisterial District Judge District (.+)$")

_SCHOOL_RE = re.compile(r"^School Director \((\d) Year\) - (.+?)\s+School "
                        r"District$")

_LOCAL_RE = re.compile(r"^(Borough Council|City Council|Township Supervisor|"
                       r"Township Auditor|Borough Auditor|"
                       r"Borough Tax Collector|Township Tax Collector|"
                       r"City Mayor|City Controller|City Treasurer|Constable)"
                       r" \((\d) Year\) - (.+)$")


def _fix_hyphen(name):
    """Tidy spacing produced by wrapped names ('Borough- 66-01' etc.)."""
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"([A-Za-z0-9])- ", r"\1 - ", name)


def _local_office(raw_office, year, tail):
    tail = _fix_hyphen(tail)
    tail = re.sub(r"\s+Boro$", "", tail)
    tail = re.sub(r"\s+Twp$", " Township", tail)
    tail = re.sub(r"\s+City$", "", tail)
    year_sfx = " (%s Year)" % year
    if raw_office == "Borough Council":
        return ("Borough Council%s" % (year_sfx if year == "2" else ""), tail)
    if raw_office == "City Council":
        return ("City Council%s" % (year_sfx if year == "2" else ""), tail)
    if raw_office == "Township Supervisor":
        return ("Township Supervisor%s" % year_sfx, tail)
    if raw_office == "Township Auditor":
        return ("Township Auditor%s" % year_sfx, tail)
    if raw_office == "Borough Auditor":
        return ("Borough Auditor%s" % year_sfx, tail)
    if raw_office in ("Borough Tax Collector", "Township Tax Collector"):
        return ("Tax Collector%s" % year_sfx, tail)
    if raw_office == "City Mayor":
        return ("Mayor", tail)
    if raw_office == "City Controller":
        return ("City Controller", tail)
    if raw_office == "City Treasurer":
        return ("City Treasurer", tail)
    if raw_office == "Constable":
        return ("Constable%s" % year_sfx, tail)
    return (raw_office, tail)


def normalize_office(title):
    """Map a SOVC contest title to (office, district)."""
    t = re.sub(r"\s+", " ", title).strip()
    if t in _EXACT:
        return _EXACT[t]
    m = _RETENTION_RE.match(t)
    if m:
        if m.group(1).lower().startswith("superior"):
            return ("Superior Court Retention - " + m.group(2).strip(), "")
        return ("Court of Common Pleas Retention - " + m.group(2).strip(), "")
    m = _MDJ_RE.match(t)
    if m:
        code = re.sub(r"\s*-\s*", "-", m.group(1).strip())
        return ("Magisterial District Judge", code)
    m = _SCHOOL_RE.match(t)
    if m:
        return ("School Director (%s Year)" % m.group(1),
                _fix_hyphen(m.group(2)))
    m = _LOCAL_RE.match(t)
    if m:
        return _local_office(m.group(1), m.group(2), m.group(3))
    return (t, "")


# --------------------------------------------------------------------------
# precinct name matching
# --------------------------------------------------------------------------

def _norm(name):
    return re.sub(r"\s+", " ", name).strip().upper()


def _matchnorm(name):
    return _norm(_fix_hyphen(name))


def _longest_name_prefix(leading, precincts_norm):
    """Indices of precincts whose name starts with the longest whitespace-
    token prefix of `leading` (leading text may include left-panel numbers
    and the right-panel copy of the name)."""
    tokens = leading.split()
    for k in range(len(tokens), 0, -1):
        cand = _matchnorm(" ".join(tokens[:k]))
        if len(cand) < 4:
            continue
        hits = [i for i, pn in enumerate(precincts_norm)
                if pn.startswith(cand)]
        if hits:
            return hits
    return []


# --------------------------------------------------------------------------
# page / column machinery
# --------------------------------------------------------------------------

_MARKER_RES = [
    ("party", re.compile(r"\(([A-Z]{2,7}(?:/[A-Z]{2,7})?)\)")),
    ("writein", re.compile(r"Qualified Write In")),
    ("writein", re.compile(r"\bWrite-In\b")),
    ("unresolved", re.compile(r"\bUnresolved\b")),
    ("total", re.compile(r"Total Votes")),
    ("yesno", re.compile(r"\b(Yes|No)\b")),
]

_NUM_RE = re.compile(r"^\d[\d,]*$")
_JUNK_NAME_TOKENS = {"FAYETTE", "COUNTY", "PRECINCT", "VOTERS", "TIMES",
                     "CAST", "REGISTERED", "VOTER", "QUALIFIED", "WRITE",
                     "IN", "TOTAL", "VOTES", "UNRESOLVED", "OFFICIAL",
                     "RESULTS"}


def _has_marker(line):
    return any(rx.search(line) for _k, rx in _MARKER_RES)


def _header_lines(lines):
    """Header zone: everything up to and including the last marker-bearing
    line among the top of the page (candidate name lines can contain digits,
    e.g. 'SCATTERED 2', so the zone cannot end at the first digit line)."""
    last = None
    for i, l in enumerate(lines[:30]):
        if _has_marker(l):
            last = i
    if last is None:
        # fall back: up to the first line with a digit
        for i, l in enumerate(lines):
            if re.search(r"\d", l):
                return lines[:i]
        return lines
    return lines[:last + 1]


def _collect_columns(header):
    """Return list of columns sorted by x: (start, end, kind, party, name).

    kind: 'named' (party column or Yes/No), 'writein', 'total'.
    """
    raw = []
    for ln, line in enumerate(header):
        if re.search(r"\(Vote for \d+\)", line):
            continue
        for kind, rx in _MARKER_RES:
            for m in rx.finditer(line):
                if kind == "party":
                    raw.append([m.start(), m.end(), "named", m.group(1), ""])
                elif kind == "yesno":
                    raw.append([m.start(), m.end(), "named", "", m.group(1)])
                elif kind == "total":
                    raw.append([m.start(), m.end(), "total", "", ""])
                elif kind in ("writein", "unresolved"):
                    raw.append([m.start(), m.end(), "writein", "", ""])
    if not raw:
        return []
    raw.sort(key=lambda r: (r[0], r[1]))
    # merge a bare 'Write-In' token with an adjacent 'Unresolved' on the
    # same line ('Unresolved / Write-In' is ONE column)
    merged = []
    for r in raw:
        s, e, kind = r[0], r[1], r[2]
        if r[4] == "" and kind == "writein" and e - s <= 8:
            hit = False
            for j in range(len(merged) - 1, -1, -1):
                if merged[j][2] == "writein" \
                        and merged[j][1] - merged[j][0] <= 12 \
                        and 0 <= s - merged[j][1] <= 8:
                    merged[j][1] = e
                    hit = True
                    break
            if hit:
                continue
        merged.append(r)
    # drop markers that overlap an already-kept marker of the same kind
    cols = []
    for s, e, kind, party, name in sorted(merged, key=lambda r: r[0]):
        if any(c[2] == kind and not (e < c[0] or s > c[1]) for c in cols):
            continue
        cols.append([s, e, kind, party, name])
    # candidate names for 'named' columns: header tokens left-aligned at the
    # column start (a name may span several lines)
    named = [c for c in cols if c[2] == "named"]
    if named:
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
                for c in named:
                    nxt = min([b for b in bounds if b > c[0]] or [10 ** 6])
                    if c[0] <= m.start() < nxt:
                        col = c
                        break
                if col is None:
                    continue
                if col[4] in ("Yes", "No"):
                    # retention Yes/No column: the marker IS the name; never
                    # append further header tokens ('Yes Yes')
                    continue
                col[4] = (col[4] + " " + tok).strip()
    return cols


def _page_rows(lines, header_n, cols):
    """Data rows of a page: (leading_text, {col_index: value}).

    A data line has at least one number inside a column range.  Lines with
    no in-column numbers are either wrapped precinct-name fragments or the
    left panel's Registered Voters / Cards Cast numbers - ignored (wrapped
    name fragments are appended to the previous row's leading text).
    """
    out = []
    first_start = min(c[0] for c in cols)
    for line in lines[header_n:]:
        if "Cumulative" in line or re.search(r"Fayette\s+County\s+-\s+Total",
                                             line):
            continue
        vals = {}
        for m in re.finditer(r"\S+", line):
            tok = m.group()
            if not _NUM_RE.match(tok):
                continue
            if m.end() < first_start - 2:
                # token inside the precinct-name / left-panel region
                continue
            cx = (m.start() + m.end()) / 2.0
            best_ci, best_d = None, 1e9
            for ci, (s, e, kind, party, name) in enumerate(cols):
                if s - 2 <= cx <= e + 2:
                    d = 0.0
                else:
                    d = (s - 2 - cx) if cx < s - 2 else (cx - e - 2)
                if d < best_d:
                    best_ci, best_d = ci, d
            if best_ci is not None and best_d <= 10:
                vals[best_ci] = int(m.group().replace(",", ""))
        if not vals:
            if out and line.strip():
                prev_lead, pv = out[-1]
                out[-1] = (prev_lead + "\x01" + line.strip(), pv)
            continue
        lead = line[:max(first_start - 3, 0)]
        out.append((lead, vals))
    return out


_VERTICAL_WI_RE = re.compile(r"Qualified Write In|\bWrite-In\b|\bUnresolved\b")


def _vertical_rows(lines):
    """Write-in values on 'vertical' write-in pages.

    Single-precinct contests with many write-in candidates stack one block
    per candidate: a numeric values row (precinct value in the rightmost
    column) followed by the candidate name and a 'Qualified Write In'
    marker.  Those pages have no horizontal column layout, so the values
    never land in a column.  Returns the rightmost numeric value of every
    block values line (numbers far left of the marker, or a pure-numeric
    line of >= 4 tokens).
    """
    out = []
    for line in lines:
        if "Cumulative" in line or re.search(r"Fayette\s+County\s+-\s+Total",
                                             line):
            continue
        m = _VERTICAL_WI_RE.search(line)
        if m:
            if m.start() < 60:
                continue
            toks = [t for t in re.finditer(r"\d[\d,]*", line)
                    if _NUM_RE.match(t.group()) and t.end() <= m.start()]
            if toks and toks[-1].end() < m.start() - 30:
                out.append(int(toks[-1].group().replace(",", "")))
            continue
        if re.search(r"[A-Za-z]", line):
            continue
        toks = [t for t in re.finditer(r"\d[\d,]*", line)
                if _NUM_RE.match(t.group())]
        if len(toks) >= 4:
            out.append(int(toks[-1].group().replace(",", "")))
    return out


# --------------------------------------------------------------------------
# front section: canonical precinct list + Registered Voters / Cards Cast
# --------------------------------------------------------------------------

_VALONLY_RE = re.compile(
    r"^\s*\d[\d,]*\s+\d[\d,]*\s+\d[\d,]*\s+\d[\d.]*%?\s*$")
_FRONT_SKIP_RE = re.compile(
    r"Turnout|Cards Cast|Registered|Precinct|Statement|General Election|"
    r"November|SOVC|OFFICIAL|Voters$|Vote for|^Fayette County$|"
    r"^Cumulative|^Fayette County - Total")
_MIXED_STRIP_RE = re.compile(
    r"\s+(\d[\d,]*)(?:\s+\d[\d,]*)*\s+\d+\.\d+%?\s*$")


def _join_fragments(a, b):
    if a.endswith("-") or b.startswith("-"):
        j = a + b
    else:
        j = a + " " + b
    return _fix_hyphen(j)


def _front_rows(pages, n_pages):
    """Precinct list + Registered Voters / Cards Cast from the front section.

    Row shapes: '<Name> <RV> <Cards> <VotersCast> <pct>%' on one line, or a
    wrapped name with the numbers on their own line between the two name
    fragments.  Returns (ordered precinct names, {name: (rv, cards)}).
    """
    names, vals = [], {}
    pending_name = ""
    pending_vals = None

    def finalize(name, nums):
        name = _fix_hyphen(name)
        if name not in vals and len(nums) >= 2:
            vals[name] = (int(nums[0].replace(",", "")),
                          int(nums[1].replace(",", "")))
        if name not in set(names):
            names.append(name)

    for pi in range(n_pages):
        for l in pages[pi].split("\n"):
            s = l.strip()
            if not s or _FRONT_SKIP_RE.search(s):
                continue
            nums = [t for t in s.split()
                    if _NUM_RE.match(t) or re.match(r"^\d+\.\d+%$", t)]
            if _VALONLY_RE.match(l):
                if len(nums) >= 3:
                    pending_vals = [t for t in nums if _NUM_RE.match(t)]
                continue
            if re.search(r"\d+\.\d+%\s*$", s) and len(nums) >= 2 \
                    and re.search(r"[A-Za-z]", s):
                m = _MIXED_STRIP_RE.search(s)
                if m:
                    # values come only from the stripped value tail, never
                    # from digits inside the precinct name ('Township 1',
                    # 'City 5', code '08-01' ...)
                    inline = [t for t in m.group(0).split()
                              if _NUM_RE.match(t)]
                    name = s[:m.start()].strip()
                else:
                    name = _MIXED_STRIP_RE.sub("", s).strip()
                    inline = [t for t in nums if _NUM_RE.match(t)]
                if pending_name:
                    name = _join_fragments(pending_name, name)
                    pending_name = ""
                if pending_vals is not None:
                    inline = pending_vals
                    pending_vals = None
                finalize(name, inline)
            elif re.search(r"[A-Za-z]", s):
                if pending_vals is not None:
                    if re.search(r"\d+-\d+$", pending_name):
                        # pending name is already complete; this line starts
                        # the next precinct's name
                        pv, pending_vals = pending_vals, None
                        finalize(pending_name, pv)
                        pending_name = s
                    else:
                        # second fragment of a wrapped name (numbers between)
                        name = _join_fragments(pending_name, s)
                        pv, pending_vals = pending_vals, None
                        pending_name = ""
                        finalize(name, pv)
                elif pending_name:
                    pending_name = _join_fragments(pending_name, s)
                else:
                    pending_name = s
            else:
                # wrapped code fragment (e.g. '01', '-01') with no letters
                if pending_vals is not None and pending_name and \
                        re.search(r"\d+-\d+$", pending_name):
                    pv, pending_vals = pending_vals, None
                    finalize(pending_name, pv)
                    pending_name = s
                elif pending_vals is not None:
                    nm = _join_fragments(pending_name, s)
                    pv, pending_vals = pending_vals, None
                    pending_name = ""
                    finalize(nm, pv)
                else:
                    pending_name = _join_fragments(pending_name, s) \
                        if pending_name else s
    return names, vals


# --------------------------------------------------------------------------
# main parse
# --------------------------------------------------------------------------

def parse_input(path):
    with open(path, errors="replace") as f:
        data = f.read()
    pages = re.split(r"\x0c?Page: \d+ of \d+.*", data)[1:]
    warnings = []

    precincts, rv_cards = _front_rows(pages, 3)
    pn_norm = [_matchnorm(p) for p in precincts]
    if len(precincts) != 77:
        warnings.append("expected 77 precincts in front section, got %d"
                        % len(precincts))
    for p in precincts:
        if p not in rv_cards:
            warnings.append("no RV/Cards Cast found for %r" % p)

    starts = []
    for i, p in enumerate(pages):
        for l in p.split("\n"):
            m = re.match(r"^\s*(.+?)\s*\(Vote for (\d+)\)\s*$", l)
            if m:
                starts.append((i, m.group(1)))
                break

    contest_records = []  # (office, district, named_order, named_party,
    #                       per_precinct)
    for ci, (start, title) in enumerate(starts):
        end = starts[ci + 1][0] if ci + 1 < len(starts) else len(pages)
        office, district = normalize_office(title)
        named_order = []
        named_party = {}
        per_precinct = {}
        vertical_vals = []
        prev_end = None
        for pg in range(start, end):
            lines = pages[pg].split("\n")
            header = _header_lines(lines)
            cols = _collect_columns(header)
            data = _page_rows(lines, len(header), cols) if cols else []
            if not data:
                # 'vertical' write-in page: one stacked block per write-in
                # candidate, values far left of the marker (single-precinct
                # contests only)
                vvals = _vertical_rows(lines)
                if vvals:
                    vertical_vals.extend(vvals)
                continue
            first_lead, _fv = data[0]
            if pg == start and not _longest_name_prefix(first_lead, pn_norm):
                warnings.append("page %d (%s): cannot anchor first row %r"
                                % (pg + 1, title,
                                   first_lead.split("\x01")[0]))
            # Per-row anchoring: local contests list only their region's
            # precincts (non-consecutive in the county-wide order), so
            # "next precinct" cannot be assumed.  Each data row's lead names
            # its own precinct; fall back to prev+1 only when the lead is
            # unmatchable (wrapped names).
            assigned = []
            prev = None
            for ri, (lead, _vals) in enumerate(data):
                cands = _longest_name_prefix(lead, pn_norm)
                if len(cands) == 1:
                    pidx = cands[0]
                elif len(cands) > 1:
                    if prev is not None and prev + 1 in cands:
                        pidx = prev + 1
                    else:
                        if ri + 1 < len(data):
                            nxt = set(_longest_name_prefix(data[ri + 1][0],
                                                           pn_norm))
                            ok = [c for c in cands if c + 1 in nxt]
                            if ok:
                                cands = ok
                        pidx = cands[0]
                        if len(cands) > 1:
                            warnings.append(
                                "page %d (%s): ambiguous anchor %r -> %s"
                                % (pg + 1, title,
                                   lead.split("\x01")[0],
                                   [precincts[c] for c in cands]))
                else:
                    if prev is not None and prev + 1 < len(precincts):
                        pidx = prev + 1
                    else:
                        warnings.append(
                            "page %d (%s): cannot anchor row %r"
                            % (pg + 1, title, lead.split("\x01")[0]))
                        continue
                assigned.append((pidx, _vals))
                prev = pidx
            for pidx, vals in assigned:
                if pidx >= len(precincts):
                    warnings.append("page %d (%s): row beyond precinct list"
                                    % (pg + 1, title))
                    break
                pname = precincts[pidx]
                rec = per_precinct.setdefault(
                    pname, {"named": {}, "wi": 0, "tvs": set(),
                            "has_tv": False})
                for coli, v in vals.items():
                    s, e, kind, party, name = cols[coli]
                    if kind == "named":
                        if not name:
                            warnings.append(
                                "page %d (%s): named column with no "
                                "candidate name" % (pg + 1, title))
                            continue
                        if name not in named_party:
                            named_party[name] = party
                            named_order.append(name)
                        key = named_order.index(name)
                        rec["named"][key] = rec["named"].get(key, 0) + v
                    elif kind == "writein":
                        rec["wi"] += v
                    elif kind == "total":
                        rec["tvs"].add(v)
                        rec["has_tv"] = True
        if vertical_vals:
            covered = [p for p in per_precinct]
            if len(covered) == 1:
                per_precinct[covered[0]]["wi"] += sum(vertical_vals)
            else:
                warnings.append(
                    "%s: %d vertical write-in values but %d precincts in "
                    "contest; not attributed" % (title, len(vertical_vals),
                                                 len(covered)))
        contest_records.append((office, district, named_order, named_party,
                                per_precinct, title))

    return precincts, rv_cards, contest_records, warnings


def build_rows(precincts, rv_cards, contest_records, county):
    rows = []
    for p in precincts:
        rv, cards = rv_cards.get(p, ("", ""))
        rows.append([county, p, "Registered Voters", "", "", "", rv,
                     "", "", ""])
        rows.append([county, p, "Ballots Cast", "", "", "", cards,
                     "", "", ""])
    tv_mismatch = []
    for office, district, named_order, named_party, per_precinct, title \
            in contest_records:
        for p in precincts:
            rec = per_precinct.get(p)
            if rec is None:
                continue
            total_named = 0
            for key, name in enumerate(named_order):
                v = rec["named"].get(key, 0)
                total_named += v
                rows.append([county, p, office, district,
                             named_party.get(name, ""), name, v, "", "", ""])
            rows.append([county, p, office, district, "", "Write-ins",
                         rec["wi"], "", "", ""])
            if rec["has_tv"]:
                tv = max(rec["tvs"])
                if len(rec["tvs"]) > 1:
                    warnings.append(
                        "%s / %s: differing Total Votes values seen: %s"
                        % (title, p, sorted(rec["tvs"])))
                if tv != total_named + rec["wi"]:
                    tv_mismatch.append((title, p, tv,
                                        total_named + rec["wi"]))
    return rows, tv_mismatch


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path")
    ap.add_argument("output_path")
    ap.add_argument("--county", default=COUNTY)
    args = ap.parse_args(argv)

    precincts, rv_cards, contest_records, warnings = parse_input(
        args.input_path)
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)
    rows, tv_mismatch = build_rows(precincts, rv_cards, contest_records,
                                   args.county)
    for title, p, tv, s in tv_mismatch[:40]:
        print("TV MISMATCH %s / %s: total %s != named+wi %s"
              % (title, p, tv, s), file=sys.stderr)
    if len(tv_mismatch) > 40:
        print("... %d more TV mismatches" % (len(tv_mismatch) - 40),
              file=sys.stderr)

    with open(args.output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDNAMES)
        w.writerows(rows)
    print("precincts: %d, contests: %d, rows: %d, TV mismatches: %d"
          % (len(precincts), len(contest_records), len(rows),
             len(tv_mismatch)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    main()