#!/usr/bin/env python3
"""Parse Fayette County, PA 2022 Primary Election "Statement of Votes Cast"
official cumulative report into OpenElections CSV format.

Sources:
  * pdftotext -layout extract of "Fayette PA P-22 official Cumulative
    report5.31.22.pdf" (Dominion SOVC, 1,204 pages) -- the main input
  * the same PDF, read with pdfplumber for the 46 pages whose panels are
    drawn rotated (candidate column headers vertical); pdftotext loses
    those pages' content below single vertical fragments, so every page
    with >=50% non-upright glyphs is re-extracted from the PDF

Same vendor/format family as the county's 2022 general SOVC report
(parsers/pa_fayette_general_2022_results_parser.py); the primary report has
no per-precinct vote-type breakdown rows, so election_day / mail /
provisional are always blank.

Layout: the front pages list all 77 precincts with Registered Voters /
Cards Cast / Voters Cast (countywide totals, no party).  Each contest then
consists of a number of column "panels"; every panel prints all 77
precincts over page bands (typically 18 + 21 + 19 + 19 precinct rows), so
panel column headers repeat on the band pages.  Panel columns are defined
by marker text anywhere on a page: "Times Cast" / "Registered"-"Voters"
(per-party ballot counting panels), "(DEM)"/"(REP)" named-candidate
columns, a "Total Votes" column, named "Qualified Write In" columns (the
per-candidate write-in assignment breakdown) and one plain "Write-In"
column (write-in votes left unresolved).  Each precinct is one name line
(often carrying the row's values), optional extra numeric lines and a
trailing wrapped-code fragment that completes the "NN-NN" precinct code.

A contest's write-in total per precinct = the sum of its Qualified Write In
column values + its plain Write-In (unresolved) column value.  Qualified
Write In columns are never added separately (the county's known QWI
double-count bug class in the general report).

Usage:
    python pa_fayette_primary_2022_results_parser.py <sovc_txt> <out_csv> \
        [--pdf <cumulative_pdf>]
"""
import argparse
import collections
import csv
import functools
import re
import sys

COUNTY = "Fayette"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_OFFICE_RES = [
    (re.compile(r"^United States Senator$"), ("U.S. Senate", None)),
    (re.compile(r"^Governor$"), ("Governor", None)),
    (re.compile(r"^Lieutenant Governor$"), ("Lieutenant Governor", None)),
    (re.compile(r"^Representative in Congress (\d+)"), ("U.S. House", 1)),
    (re.compile(r"^Senator in the General Assembly (\d+)"),
     ("State Senate", 1)),
    (re.compile(r"^Representative in the General Assembly (\d+)"),
     ("State House", 1)),
    (re.compile(r"^Member of Democratic State Committee$"),
     ("Member of Democratic State Committee", None)),
    (re.compile(r"^Member of Republican State Committee$"),
     ("Member of Republican State Committee", None)),
]

# column markers found anywhere on a page ("party" = named candidate column)
_MARKERS = [
    ("party", re.compile(r"\((DEM|REP)\)")),
    ("total", re.compile(r"Total Votes")),
    ("writein", re.compile(r"\bWrite-In\b|\bWrite-in\b")),
    ("qwi", re.compile(r"Qualified Write In")),
    ("times", re.compile(r"Times Cast")),
    ("rv", re.compile(r"\bRegistered\b")),
]

_INTEGER_RE = re.compile(r"^\d[\d,]*$")


def _marker_kind(text):
    """Column kind for a rotated marker-stack text (which may have lost its
    spaces), else None."""
    t = re.sub(r"\s+", "", text).strip()
    if re.fullmatch(r"\((?:DEM|REP)\)", t):
        return "party"
    if len(t) < 7 or not re.fullmatch(r"[A-Za-z()\-]+", t):
        return None
    for kind, rx in _MARKERS:
        if kind == "party":
            continue
        if rx.search(re.sub(r"\s+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2",
                                               t))):
            return kind
    return None


_JUNK_NAME_TOKENS = {"FAYETTE", "COUNTY", "PRECINCT", "VOTERS", "VOTER",
                     "TIMES", "CAST", "REGISTERED", "QUALIFIED", "WRITE",
                     "IN", "TOTAL", "UNRESOLVED"}
_CODE_RE = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*$")
_SKIPPED_LINE_RE = re.compile(
    r"^(?:Cumulative\b|Fayette County - Total|Precinct\b|Times Cast\b|"
    r"Registered\b|Voters\b|Dem\b|Rep\b|DEM\b|REP\b|"
    r"Statement of Votes Cast|Closed Primary|May \d+[ ,]?\d* ?20\d\d\b|"
    r"SOVC for|Election Summary Report)")
_PARTY_CODE = {"Dem": "DEM", "Rep": "REP"}


def normalize_office(title):
    t = re.sub(r"\s+", " ", title).strip()
    t = re.sub(r"\s*\((?:Dem|Rep|DEM|REP)\)\s*\(Vote for \d+\)\s*$", "", t)
    t = re.sub(r"\s*\(Vote for \d+\)\s*$", "", t)
    for rx, (office, grp) in _OFFICE_RES:
        m = rx.match(t)
        if m:
            return (office, str(m.group(grp)) if grp else "")
    return (t.strip(), "")


def _clean_name(name):
    return re.sub(r"\s+", " ", name).strip()


def _join_name(cur, word):
    """Wrapped name fragments joined; keep hyphenated words together."""
    if not cur:
        return word
    if cur.endswith("-"):
        return cur + word
    return cur + " " + word


def collect_columns(lines):
    """Columns of one page, sorted by x.

    Returns a list of dicts {x, kind, party, name, key} where key
    (kind, name, rank) is stable across pages when the same panel's
    headers repeat.
    """
    raw = []
    for line in lines:
        for kind, rx in _MARKERS:
            for m in rx.finditer(line):
                party = m.group(1) if kind == "party" else ""
                raw.append((m.start(), kind, party))
    if not raw:
        return []
    raw.sort(key=lambda r: r[0])
    cols = []
    for x, kind, party in raw:
        if any(c["kind"] == kind and abs(c["x"] - x) <= 8 for c in cols):
            continue
        cols.append({"x": x, "kind": kind, "party": party, "name": ""})
    # candidate names live at x-spans owned by a named (party) column
    bounds = sorted(c["x"] for c in cols)
    for line in lines:
        if re.search(r"\(Vote for \d+\)", line):
            continue
        for m in re.finditer(r"\S+", line):
            word = m.group()
            if word.upper() in _JUNK_NAME_TOKENS or word.startswith("("):
                continue
            col = None
            for i, c in enumerate(cols):
                nxt = bounds[i + 1] if i + 1 < len(bounds) else 10 ** 6
                if c["x"] <= m.start() < nxt:
                    col = c
                    break
            if col is None or col["kind"] != "party":
                continue
            col["name"] = _join_name(col["name"], word)
    for c in cols:
        if c["kind"] == "qwi":
            c["party"] = ""          # write-in assignment breakdown columns
    ranks = {}
    for c in cols:
        n = ranks.get((c["kind"], c["name"]), 0)
        c["key"] = (c["kind"], c["name"], n)
        ranks[(c["kind"], c["name"])] = n + 1
    return cols


def _col_for_x(cols, x_end):
    """Column owning a value whose right edge is at x_end (values are
    right-aligned; the nearest marker start at/before the edge wins)."""
    best = None
    for c in cols:
        if c["x"] <= x_end + 2:
            best = c
    return best


def parse_front(pages, first_contest_page, warnings):
    """Precinct list + Registered Voters / Cards Cast from the front pages."""
    names, vals, codes = [], {}, {}
    lines = []
    for p in sorted(pages):
        if p >= first_contest_page:
            break
        lines += pages[p].split("\n")
    pend, buf = [], []
    pct_re = re.compile(r"\d+\.\d+%")
    for line in lines:
        s = line.strip()
        if not s or _SKIPPED_LINE_RE.match(s):
            continue
        if "Fayette County" in s and not re.search(r"\d", s):
            continue
        toks = [t.group() for t in re.finditer(r"\S+", s)]
        has_pct = any(pct_re.fullmatch(t) for t in toks)
        ints = [t for t in toks if _INTEGER_RE.fullmatch(t)]
        if has_pct:
            buf.extend(t.replace(",", "") for t in ints)
            text = " ".join(t for t in toks if not (pct_re.fullmatch(t)
                               or _INTEGER_RE.fullmatch(t)))
        else:
            text = s
        if text.strip():
            pend.append(text)
        joined = re.sub(r",", "", " ".join(pend))
        m = _CODE_RE.search(joined)
        if m and len(buf) >= 3:
            code = "%s-%s" % (m.group(1), m.group(2))
            name = re.sub(r"\s+", " ", joined[:m.start()]).strip()
            name = re.sub(r"[ \-]+$", "", name)
            name = re.sub(r"( - )+", " - ", name)
            name = name.replace(" ,", ",").strip()
            name = "%s - %s" % (name, code)
            names.append(name)
            vals[name] = tuple(int(v) for v in buf[:3])
            codes[code] = name
            pend, buf = [], []
    if pend:
        warnings.append("front: leftover fragments %r" % (pend,))
    return names, vals, codes


def _apply_values(rec, buf, title, cur, sums, named, named_party, ballot,
                  warnings):
    """Assign a block's (value, column) pairs."""
    for t, col in buf:
        val = int(t.replace(",", ""))
        kind, name, _rank = col["key"]
        if kind == "party":
            cname = _clean_name(name)
            if not cname:
                warnings.append("%s: candidate column without name"
                                % title)
                continue
            rec["named"][cname] = rec["named"].get(cname, 0) + val
            if cname not in named:
                named.append(cname)
                named_party[cname] = col["party"]
        elif kind == "total":
            if rec["tv"] is None:
                rec["tv"] = val
            else:
                warnings.append("%s: second Total Votes value %d for %s"
                                % (title, val, cur))
        elif kind == "writein":
            rec["wi"] += val
        elif kind == "qwi":
            rec["wq"] += val
        elif kind == "times":
            ballot["times"].setdefault(cur, val)
        elif kind == "rv":
            ballot["rv"].setdefault(cur, val)
        if kind not in ("times", "rv"):
            sums[col["key"]] = sums.get(col["key"], 0) + val


def parse_contest(page_lines, title, party, codes, warnings):
    """Parse one contest's page range from the pdftotext extraction."""
    office, district = normalize_office(title)
    records = {}
    printed, sums = {}, {}
    named, named_party = [], {}
    ballot = {"times": {}, "rv": {}}

    for pg in page_lines:
        # header zone: everything before the first line carrying a column
        # value (an integer token ending at x>=30)
        first_val = len(pg)
        for i, line in enumerate(pg):
            if "Vote for" in line:
                continue
            if any(_INTEGER_RE.fullmatch(t.group()) and t.end() >= 30
                   for t in re.finditer(r"\S+", line)):
                first_val = i
                break
        cols = collect_columns(pg[:first_val])
        if not cols:
            continue
        pend, buf = [], []
        done = False
        for line in pg:
            s = line.strip()
            if not s:
                continue
            if not done and "Fayette County - Total" in s:
                for tok in re.finditer(r"\S+", line):
                    if not _INTEGER_RE.fullmatch(tok.group()):
                        continue
                    col = _col_for_x(cols, tok.end())
                    if col is None:
                        continue        # drifting totals cells of other
                        # panels; the per-column check catches losses
                    v = int(tok.group().replace(",", ""))
                    if col["key"] in printed and printed[col["key"]] != v and \
                            col["kind"] != "qwi":
                        warnings.append(
                            "%s: conflicting county total for %r: %d vs %d"
                            % (title, col["key"], printed[col["key"]], v))
                    printed.setdefault(col["key"], v)
                    sums.setdefault(col["key"], 0)
                done = True
                continue
            if done or _SKIPPED_LINE_RE.match(s):
                continue
            if "Fayette County" in s and not re.search(r"\d", s):
                continue
            if "Vote for" in s:
                continue
            tokinfo = []
            for tok in re.finditer(r"\S+", line):
                t = tok.group()
                col = None
                if _INTEGER_RE.fullmatch(t):
                    col = _col_for_x(cols, tok.end())
                tokinfo.append((t, col, col is None))
            if not any(not frag for _t, _c, frag in tokinfo):
                pend.append(s)
            else:
                buf.extend((t, col) for t, col, frag in tokinfo if not frag)
                pend.append(" ".join(t for t, _c, frag in tokinfo if frag))
            joined = " ".join(pend)
            m = _CODE_RE.search(joined)
            if m and buf:
                code = "%s-%s" % (m.group(1), m.group(2))
                cur = codes.get(code)
                if cur is None:
                    warnings.append("%s: unmatched precinct code %r"
                                    % (title, joined))
                    cur = None
                rec = records.setdefault(
                    cur, {"named": {}, "tv": None, "wi": 0, "wq": 0})
                _apply_values(rec, buf, title, cur, sums, named, named_party,
                              ballot, warnings)
                buf, pend = [], []
    if pend:
        warnings.append("%s: leftover fragments %r" % (title, pend))
    return {
        "title": title, "office": office, "district": district,
        "party": party, "named": named, "named_party": named_party,
        "records": records, "printed": printed, "sums": sums,
        "ballot": ballot,
    }


def _rotated_pages(pdf_path, warnings):
    """Pages whose panel content is drawn rotated -> pre-extracted words.

    Returns {page index: words list} for pages with >=50% non-upright
    glyphs; pdftotext loses those panels (vertical text), so they are
    reparsed here from coordinates.  Words are (x0, x1, top, text, rot)
    with rot True for the rotated name/marker glyph stacks.
    """
    try:
        import pdfplumber
    except ImportError:
        warnings.append("pdfplumber unavailable; rotated panels skipped")
        return {}
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            if not page.chars:
                continue
            non = sum(1 for c in page.chars if not c["upright"])
            if non <= len(page.chars) * 0.5:
                continue
            uw = []
            for w in page.extract_words(extra_attrs=["upright"]):
                if w["upright"]:
                    uw.append([w["x0"], w["x1"], w["top"], w["text"], False])
            stacks = collections.defaultdict(list)
            for ch in page.chars:
                if ch["upright"]:
                    continue
                stacks[round(ch["x0"])].append(ch)
            for x, glyphs in sorted(stacks.items()):
                glyphs.sort(key=lambda c: c["top"])
                text = "".join(g["text"] for g in glyphs)[::-1]
                uw.append([x, max(g["x1"] for g in glyphs),
                           min(g["top"] for g in glyphs), text, True])
            uw.sort(key=lambda w: (w[2], w[0]))
            out[i] = uw
    return out


def parse_rotated_page(words, title, party, codes, warnings):
    """Parse one rotated panel page from pre-extracted words (see
    _rotated_pages).

    Rows (labels + values) are upright; candidate names and column markers
    are drawn rotated 90 degrees (one glyph per text column, reading
    bottom-to-top, so glyph texts concatenated in top order and string
    reversed give the word; wrapped names join in x order).
    """
    uw = words

    # columns: marker stacks in x order; names = text stacks to their left
    marks = sorted((w[0], w[4], w[3]) for w in uw
                   if w[4] and _marker_kind(w[3]))
    cols = []
    for i, (x, _rot, text) in enumerate(marks):
        if cols and abs(cols[-1]["x"] - x) <= 6:
            continue
        kind = _marker_kind(text)
        prev = marks[i - 1][0] if i else 0.0
        name_toks = []
        for jx0, _jx1, jtop, jtext, jrot in uw:
            if not jrot or jx0 > x + 2 or jx0 <= prev or \
                    _marker_kind(jtext):
                continue
            name_toks.append((jx0, jtext))
        name_toks.sort()
        cols.append({"x": x, "kind": kind,
                     "name": _clean_name(functools.reduce(
                         lambda a, b: _join_name(a, b),
                         [t for _x, t in name_toks], ""))})
    for c in cols:
        c["party"] = party
    ranks = {}
    for c in cols:
        n = ranks.get((c["kind"], c["name"]), 0)
        c["key"] = (c["kind"], c["name"], n)
        ranks[(c["kind"], c["name"])] = n + 1

    records, printed, sums = {}, {}, {}
    named, named_party = [], {}
    # rows along top (values + label fragments)
    rows = []
    for w in uw:
        if rows and w[2] - rows[-1]["top"] < 6:
            rows[-1]["toks"].append(w)
        else:
            rows.append({"top": w[2], "toks": [w]})
    pend, buf = [], []
    done = False
    for row in rows:
        s = " ".join(w[3] for w in row["toks"])
        if not s.strip():
            continue
        if not done and "Fayette County - Total" in s:
            for w in row["toks"]:
                if w[4] or not _INTEGER_RE.fullmatch(w[3]):
                    continue
                col = None
                for i, c in enumerate(cols):
                    nxt = cols[i + 1]["x"] if i + 1 < len(cols) else 10 ** 6
                    if c["x"] - 3 <= (w[0] + w[1]) / 2.0 < nxt:
                        col = c
                        break
                if col is not None:
                    v = int(w[3].replace(",", ""))
                    if col["key"] in printed and printed[col["key"]] != v \
                            and col["kind"] != "qwi":
                        warnings.append(
                            "%s: conflicting county total for %r" %
                            (title, col["key"]))
                    printed.setdefault(col["key"], v)
                    sums.setdefault(col["key"], 0)
            done = True
            continue
        if done or _SKIPPED_LINE_RE.match(s):
            continue
        if "Vote for" in s:
            continue
        tokinfo = []
        for w in row["toks"]:
            t = w[3]
            col = None
            if _INTEGER_RE.fullmatch(t):
                for i, c in enumerate(cols):
                    nxt = cols[i + 1]["x"] if i + 1 < len(cols) else 10 ** 6
                    if c["x"] - 3 <= w[1] + 2 < nxt:
                        col = c
                        break
            tokinfo.append((t, col, col is None))
        if not any(not frag for _t, _c, frag in tokinfo):
            pend.append(s)
        else:
            buf.extend((t, col) for t, col, frag in tokinfo if not frag)
            pend.append(" ".join(t for t, _c, frag in tokinfo if frag))
        joined = " ".join(pend)
        m = _CODE_RE.search(joined)
        if m and buf:
            code = "%s-%s" % (m.group(1), m.group(2))
            cur = codes.get(code)
            if cur is None:
                warnings.append("%s: unmatched precinct code %r"
                                % (title, joined))
                cur = None
            rec = records.setdefault(
                cur, {"named": {}, "tv": None, "wi": 0, "wq": 0})
            _apply_values(rec, buf, title, cur, sums, named, named_party,
                          {"times": {}, "rv": {}}, warnings)
            buf, pend = [], []
    for key, val in sorted(printed.items()):
        if sums.get(key, 0) != val:
            warnings.append("%s: rotated panel column %r printed %d vs %d"
                            % (title, key, val, sums.get(key, 0)))
    return {"named": named, "named_party": named_party, "records": records,
            "printed": printed, "sums": sums}


def parse_input(txt_path, pdf_path=None):
    with open(txt_path, errors="replace") as f:
        data = f.read()
    parts = re.split(r"\x0c?Page: (\d+) of \d+[^\n]*", data)
    all_pages = {}
    for i in range(1, len(parts), 2):
        all_pages[int(parts[i])] = parts[i + 1]
    warnings = []
    plumb = {}
    if pdf_path:
        plumb = _rotated_pages(pdf_path, warnings)

    # contest start pages (a contest's title page always carries a plain
    # '(Vote for N)' line; scan all pages first)
    headers = []
    for p in sorted(all_pages):
        for l in all_pages[p].split("\n"):
            m = re.match(r"^\s*(.+?)\s+\((Dem|Rep)\)\s*\(Vote for (\d+)\)$",
                         l)
            if m:
                headers.append((p, m.group(1), m.group(2), int(m.group(3))))
                break
    if not headers:
        warnings.append("no contest headers found")
        return [], {}, {}, [], warnings

    # pages whose panels are drawn rotated are reparsed from the PDF below;
    # drop their text so values with different x placement are never
    # counted twice
    pages = {p: txt for p, txt in all_pages.items()
             if p not in plumb or p in {h[0] for h in headers}}

    names, vals, codes = parse_front(pages, headers[0][0], warnings)
    contests = []
    for i, (start, title, party, vote_for) in enumerate(headers):
        end = headers[i + 1][0] if i + 1 < len(headers) else max(pages) + 1
        page_lines = [pg.split("\n") for p, pg in sorted(pages.items())
                      if start <= p < end]
        c = parse_contest(page_lines, title, _PARTY_CODE.get(party, party),
                          codes, warnings)
        c["start"], c["end"] = start, end
        contests.append(c)

    # rotated pages: merge their panels into the contest covering them
    merged = 0
    for p in sorted(plumb):
        cont = None
        for c in contests:
            if c["start"] <= p < c["end"]:
                cont = c
                break
        if cont is None:
            continue
        r = parse_rotated_page(plumb[p], cont["title"], cont["party"],
                               codes, warnings)
        for prec, rec in r["records"].items():
            dst = cont["records"].setdefault(
                prec, {"named": {}, "tv": None, "wi": 0, "wq": 0})
            for name, v in rec["named"].items():
                dst["named"][name] = dst["named"].get(name, 0) + v
            dst["wi"] += rec["wi"]
            dst["wq"] += rec["wq"]
        merged += 1
    if merged != len(plumb):
        warnings.append("rotated pages not merged: %d"
                        % (len(plumb) - merged))
    return names, vals, codes, contests, warnings


def verify_contests(contests, warnings):
    """Per-contest sanity summary (also used by test scripts)."""
    out = []
    for c in contests:
        if not c["records"]:
            continue
        for key, val in sorted(c["printed"].items()):
            if c["sums"].get(key, 0) != val:
                warnings.append(
                    "CONTEST %s: column %r printed total %d vs parsed %d"
                    % (c["title"], key, val, c["sums"].get(key, 0)))
        tv = sum(r["tv"] or 0 for r in c["records"].values())
        named = sum(sum(r["named"].values()) for r in c["records"].values())
        wi = sum(r["wi"] + r["wq"] for r in c["records"].values())
        out.append("%s: precinct rows %d, named %d, write-ins %d, "
                   "total votes %d, diff %d"
                   % (c["title"], len(c["records"]), named, wi, tv,
                      tv - (named + wi)))
    return out


def build_rows(names, vals, contests):
    rows = []
    for p in names:
        rv, cards, _voters = vals.get(p, ("", "", ""))
        rows.append([COUNTY, p, "Registered Voters", "", "", "", rv,
                     "", "", ""])
        rows.append([COUNTY, p, "Ballots Cast", "", "", "", cards,
                     "", "", ""])
    for c in contests:
        if not c["records"]:
            continue
        for p in names:
            rec = c["records"].get(p)
            if rec is None:
                continue
            for name in c["named"]:
                rows.append([COUNTY, p, c["office"], c["district"],
                             c["party"], name, rec["named"].get(name, 0),
                             "", "", ""])
            rows.append([COUNTY, p, c["office"], c["district"], c["party"],
                         "Write Ins", rec["wq"] + rec["wi"], "", "", ""])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path")
    ap.add_argument("output_path")
    ap.add_argument("--pdf", default=None,
                    help="the source PDF, for the rotated panel pages")
    args = ap.parse_args(argv)

    names, vals, codes, contests, warnings = parse_input(args.input_path,
                                                         args.pdf)
    summary = verify_contests(contests, warnings)

    rows = build_rows(names, vals, contests)
    with open(args.output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDNAMES)
        w.writerows(rows)

    print("precincts: %d, contests: %d, rows: %d, warnings: %d"
          % (len(names), len(contests), len(rows), len(warnings)),
          file=sys.stderr)
    for line in summary:
        print("CONTEST", line, file=sys.stderr)
    for w in warnings[:80]:
        print("WARNING:", w, file=sys.stderr)
    if len(warnings) > 80:
        print("... %d more warnings" % (len(warnings) - 80), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())