#!/usr/bin/env python3
"""Parser for the Montgomery County PA 2022 General Primary "results book"
(Hart eSlate Election Summary Report + Statement of Votes Cast).

Source layout (single PDF, ~4300 pages, report generated 6/21/2022 —
POST-recount for the GOP U.S. Senate race):
  * 225-page Election Summary Report: county totals per contest.  Supplies the
    ordered candidate lists and the verification totals.
  * Turnout section: per precinct Registered Voters / Cards Cast / Voters Cast
    by counting group (Election Day / Mail-in / Provisional).
  * Per-precinct Statement of Votes Cast (~4000 pages): one report page per
    contest chunk.  Report pages are printed split in halves (a left half with
    Precinct + Times Cast + Registered Voters + first candidate columns, a
    right half repeating the precinct name with the rest of the row) or full
    width (remaining candidates + Write-in + Total Votes + Write-in
    Unresolved), so each text line can carry two label groups.  Candidate name
    glyphs in SOVC headers render REVERSED, so columns are identified
    geometrically instead of sequentially: candidate columns from the
    "(DEM)"/"(REP)" party tokens in the header band; the Write-in / Total
    Votes / Write-in Unresolved / Times Cast / Registered Voters columns from
    their header tokens; every numeric cell is mapped to the nearest header
    column x.  Chunk subsets of the candidate list cycle 1,2,1,2 through the
    precinct blocks; the final chunk completes the list from the end.
  * Closed primary: contest titles carry a -DEM/-REP suffix (committee races
    carry Democratic/Republican in the title) and rows take the contest's
    party, Write Ins rows included.  Named write-in candidates
    (e.g. "DAN RONCA WRITE-IN") are listed in the summary but are NOT columns
    in the SOVC — their votes are part of the contest's Write-in column, so
    they are folded into the Write Ins row and not emitted separately
    (Allegheny/Indiana 2022 precedent).  The "Write-in Unresolved" column is
    not votes (Vote-for-2 committee contests make Total Votes exceed ballots)
    and is skipped.

Usage:
    uv run python parsers/pa_montgomery_primary_2022_results_parser.py \
        [input.pdf [output.csv]] [--pages=START:END]
"""

import re
import sys
from collections import OrderedDict, defaultdict

import pdfplumber

COUNTY = "Montgomery"

PARTIES = {"DEM", "REP", "LIB", "GRN", "GRE", "KEY", "NOP", "CON", "IND"}
ROW_LABELS = {"Election", "Day", "Mail-in", "Provisional", "Total"}
HEADER_WORDS = {
    "Precinct", "County", "PA", "Cards", "Cast", "Voters", "Turnout", "%",
    "Registered", "Cumulative", "Candidate", "Party", "Times", "Statement",
    "of", "Votes", "General", "November", "OFFICIAL", "REPORT", "SOVC",
    "for:", "All", "Contests,", "Districts,", "Tabulators,", "Counting",
    "Groups", "MONTGOMERY", "Summary", "Results", "Closed", "Primary",
    "Ballots", "Page:", "-",
}
NUM_RE = re.compile(r"^[\d,]+$")
PCT_RE = re.compile(r"^[\d.]+%$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
DATEISH_RE = re.compile(r"^\d{1,2}/\d{2}/\d{4}$|^PM$|^AM$")
# reversed renderings of SOVC header words — never part of a precinct name
REV_JUNK = {"setoV", "latoT", "ni-etirW", "nI-etirW", "semiT", "tsaC",
            "deretsigeR", "sretoV", "Times", "Votes", "Total", "Write-in",
            "Registered", "Cast", "Voters", "Undervote", "devlosernU",
            "Cumulative"}
NAMED_WI_RE = re.compile(r"\bWRITE-IN\b", re.I)


def rev(s):
    """Undo the reversed glyph rendering used in SOVC headers."""
    s = s[::-1]
    if s.endswith("(") and s.startswith(")"):
        s = "(" + s[1:-1] + ")"
    return s


def is_num(tok):
    return bool(NUM_RE.match(tok))


def to_int(tok):
    return int(tok.replace(",", ""))


def page_lines(page):
    lines = defaultdict(list)
    for w in page.extract_words():
        lines[round(w["top"])].append(w)
    return [(top, sorted(lines[top], key=lambda w: w["x0"]))
            for top in sorted(lines)]


def is_page_header(ws):
    return any(w["text"] == "Page:" for w in ws)


# --------------------------------------------------------------------------
# 1. Summary section
# --------------------------------------------------------------------------

def parse_summary(pdf, max_page, warnings):
    """OrderedDict title -> {candidates: [(name, party, (ed, mail, prov, total))],
    writein, total_votes, named_wi}.

    Wrapped candidate names appear as alpha-only lines adjacent to (above or
    below) the party+numbers row; each alpha line belongs to the nearest
    numbers row.  Named write-in rows ("X WRITE-IN  n n n n") carry no party
    token; they are a breakdown of the contest's Write-in row, not SOVC
    columns, so they are kept aside as named_wi and excluded from candidates."""
    NUMT = re.compile(r"\d[\d,]*(?![\d/%])")
    BIG_NUM = re.compile(r"\d,\d|\d{4,}")

    def nums_in(s):
        return NUMT.findall(s)

    raw = []
    for i in range(max_page):
        for _top, ws in page_lines(pdf.pages[i]):
            if is_page_header(ws):
                continue
            raw.append(" ".join(w["text"] for w in ws))

    contests = OrderedDict()
    cur = None
    pending_title = ""
    rows = []    # (idx, party, same-line name, vals)
    frags = []   # (idx, text)
    for i, line in enumerate(raw):
        line = line.strip()
        if not line:
            continue
        trial = (pending_title + " " + line).strip() if pending_title else line
        m = re.match(r"^(.*?)\s*\(\s*Vote for (\d+)\s*\)\s*$", trial)
        if m and not BIG_NUM.search(m.group(1)):
            cur = {"candidates": [], "writein": None, "total_votes": None,
                   "named_wi": []}
            contests[m.group(1).strip()] = cur
            pending_title = ""
            continue
        if "(Vote" in trial and not re.search(r"Vote for \d+\)\s*$", trial):
            pending_title = trial
            continue
        pending_title = ""
        if cur is None:
            continue
        if "Times Cast" in line or "Registered Voters" in line or "Ballots Cast" in line:
            continue
        if line.startswith("Candidate"):
            continue
        if "Election Day" in line and not nums_in(line):
            continue
        nums = nums_in(line)
        if len(nums) == 4:
            left = line.split(nums[0])[0].strip()
            party = ""
            name_toks = []
            for t in left.split():
                if t in PARTIES and not party:
                    party = t
                else:
                    name_toks.append(t)
            vals = tuple(int(x.replace(",", "")) for x in nums)
            rows.append((i, party, " ".join(name_toks), vals, cur))
        elif not nums:
            bad = ("Candidate", "Election Day", "Party", "Times", "Registered",
                   "Ballots", "(Vote", "Statement", "Summary", "Report")
            if any(b in line for b in bad):
                continue
            frags.append((i, line))
    # assign fragments to nearest numbers row (ties -> the row BELOW: a wrapped
    # name line directly above a numbers row belongs to it).
    targets = [r for r in rows if r[2] not in ("Write-in", "Total Votes")]
    row_frags = defaultdict(lambda: ([], []))
    for fi, ftext in frags:
        best, bestd = None, None
        for r in targets:
            d = abs(fi - r[0])
            if bestd is None or d <= bestd:
                best, bestd = r[0], d
        if best is None or bestd > 3:
            continue
        pre, post = row_frags[best]
        (pre if fi < best else post).append((fi, ftext))
    for i, party, name, vals, cont in rows:
        pre, post = row_frags.get(i, ([], []))
        pre_txt = [t for _, t in sorted(pre)]
        post_txt = [t for _, t in sorted(post)]
        full = re.sub(r"\s+", " ", " ".join(pre_txt + [name] + post_txt)).strip()
        if full == "Write-in":
            cont["writein"] = vals
        elif full == "Total Votes":
            cont["total_votes"] = vals
        elif "WRITE" in full.upper() and "WRITE-IN" in full.upper():
            cont["named_wi"].append(full)
        else:
            cont["candidates"].append((full, party, vals))
    return contests


# --------------------------------------------------------------------------
# 2. Turnout section
# --------------------------------------------------------------------------

def parse_turnout(page_data, warnings):
    """Precinct -> {rv, ed, mail, prov, total} from the turnout-by-precinct
    pages."""
    turnout = OrderedDict()
    cur_name = None
    pending_names = []
    for idx, lines in page_data:
        for top, ws in lines:
            toks = [w["text"] for w in ws]
            if any(t == "Page:" for t in toks):
                continue
            if any(t == "Cumulative" for t in toks):
                continue
            # stop at the county-total row: everything below is junk that
            # would otherwise attach to the last real precinct
            if "Total" in toks and any(t in ("PA", "County") for t in toks) \
                    and "Election" not in toks and "Mail-in" not in toks:
                pending_names = []
                break
            nums = [t for t in toks if is_num(t)]
            labels = [t for t in toks if t in ROW_LABELS]
            if labels and nums:
                if pending_names or cur_name:
                    try:
                        rv, cards, voters = (to_int(v) for v in nums[:3])
                    except ValueError:
                        continue
                    key = ("ed" if "Election" in toks else
                           "mail" if "Mail-in" in toks else
                           "prov" if "Provisional" in toks else
                           "total" if "Total" in toks else None)
                    if key:
                        name = " ".join(pending_names) if pending_names else cur_name
                        rec = turnout.setdefault(name, {})
                        rec[key] = voters
                        rec["rv"] = rv
                        if pending_names:
                            cur_name = name
                            pending_names = []
                continue
            if labels:
                continue
            # name line (may contain small integers, e.g. "Bridgeport 2")
            name_toks = []
            for x, t in [(w["x0"], w["text"]) for w in ws]:
                if t in HEADER_WORDS or t == "Page:" or TIME_RE.match(t) \
                   or DATEISH_RE.match(t) or PCT_RE.match(t) or t in ("N/A", "-"):
                    continue
                name_toks.append(t)
            if name_toks:
                joined = " ".join(name_toks)
                if re.search(r"Statement|Report|Election Results|Summary", joined):
                    pending_names = []
                    continue
                if is_num(joined.replace(" ", "").replace("-", "")):
                    continue  # stray numeric junk
                pending_names.append(joined)
            else:
                pending_names = []
                cur_name = None
    out = OrderedDict()
    for name, rec in turnout.items():
        if rec and rec.get("total") is not None:
            out[name] = rec
    if not out:
        warnings.append("turnout section empty")
    return out


# --------------------------------------------------------------------------
# 3. SOVC detail section (geometric column mapping)
# --------------------------------------------------------------------------

# rev(header token) -> column kind for non-candidate columns
HEADER_SPECIALS = {
    "Write-in": "wi",
    "Total": "tv", "Votes": "tv",
    "Undervote": "uv", "Unresolved": "uv",
    "Times": "skip", "Cast": "skip",
    "Registered": "skip",
}


class ContestState:
    def __init__(self, title, candidates):
        self.title = title
        self.candidates = candidates      # [(name, party)] from summary
        self.chunks = OrderedDict()       # sig -> chunk dict
        self.ptr = 0


def build_columns(lines_page, cand_names=None):
    """(cand_col_xs, specials{kind: x}) for one SOVC page: candidate columns
    from the "(DEM)"/"(REP)" party tokens in the header band (lines above the
    first row holding vote numbers); the Write-in / Total Votes / Write-in
    Unresolved / Times Cast / Registered Voters columns from their header
    tokens.  Contests without party tokens (ballot questions with YES/NO) get
    their columns from the reversed candidate names in the header band."""
    cand_xs = []
    specials = {}
    und_tokens = []
    wi_tokens = []
    first_data_top = None
    for top, ws in lines_page:
        texts = [w["text"] for w in ws]
        if any(t == "Page:" for t in texts):
            continue
        nums = [t for t in texts if is_num(t)]
        if ("Election" in texts or "Mail-in" in texts or "Provisional" in texts
                or "Total" in texts) and len(nums) >= 2:
            first_data_top = top
            break
    header_band = []
    for top, ws in lines_page:
        if first_data_top is not None and top >= first_data_top:
            break
        texts = [w["text"] for w in ws]
        if any(t == "Page:" for t in texts):
            continue
        if any(is_num(t) for t in texts):
            continue
        if "Cumulative" in texts:
            continue
        for w in ws:
            t = w["text"]
            rt = rev(t)
            if re.fullmatch(r"\([A-Z]{2,4}\)", t) or re.fullmatch(r"\([A-Z]{2,4}\)", rt):
                cand_xs.append(round(w["x0"]))
                continue
            if rt in ("Voters",):
                specials["rv"] = round(w["x0"])
            elif rt in ("Undervote", "Unresolved"):
                und_tokens.append((round(w["x0"]), top))
            elif rt in ("Write-in",):
                wi_tokens.append((round(w["x0"]), top))
            elif rt in HEADER_SPECIALS:
                specials.setdefault(HEADER_SPECIALS[rt], round(w["x0"]))
            header_band.append((top, w["x0"], t, rt))
    # a "Write-in" token adjacent to an "Unresolved" token is the two-line
    # header of the "Write-in Unresolved" column, not the write-in column
    uxs = [ux for ux, _ in und_tokens]
    for wxt, _ in wi_tokens:
        if any(abs(wxt - ux) < 60 for ux in uxs):
            continue
        specials["wi"] = round(wxt)
    for ux, _ in und_tokens:
        specials.setdefault("uv", ux)
    if not cand_xs and cand_names:
        # ballot-question style: match summary candidate names, reversed,
        # as single-token headers (YES / NO)
        want = {c.upper(): c for c in cand_names if " " not in c.strip()}
        for top, x, t, rt in header_band:
            if rt.upper() in want:
                cand_xs.append(round(x))
    return sorted(cand_xs), specials


def map_value(x, cols_by_x, max_gap=45):
    """(kind, cand_i) of the nearest header column, or None if nothing within
    max_gap.  ("skip", None) covers Times Cast / Registered Voters / the
    Write-in Unresolved column."""
    best = (None, None, None)  # (dist, kind, idx)
    for cx, (kind, ci) in cols_by_x.items():
        d = abs(x - cx)
        if best[0] is None or d < best[0]:
            best = (d, kind, ci)
    if best[0] is None or best[0] > max_gap:
        return None
    if best[1] in ("skip", "uv", "rv"):
        return ("skip", None)
    return (best[1], best[2])


def parse_detail(page_data, lo, hi, contests_summary, warnings):
    results = defaultdict(dict)       # (title, precinct) -> cand_idx -> {label: v}
    wi_vals = defaultdict(dict)       # (title, precinct) -> {label: v}
    tv_vals = defaultdict(dict)       # (title, precinct) -> {label: v}
    registered = {}                   # precinct -> rv from SOVC Registered col
    # one record per county-total row printed in the SOVC (a chunk page's own
    # county row carries only that chunk's columns): {"cand": {ci: {label: v}},
    # "wi": {label: v}, "tv": {label: v}}
    county_rows = defaultdict(list)
    states = {}

    def state_for(title):
        if title not in states:
            cands = contests_summary[title]["candidates"]
            states[title] = ContestState(title, [(c, p) for c, p, _ in cands])
        return states[title]

    cur_title = None

    for idx in range(lo, hi):
        lines = page_data.get(idx)
        if lines is None:
            continue
        # ---- pass 1: contest title
        title = None
        for top, ws in lines:
            joined = " ".join(w["text"] for w in ws)
            if "(Vote" in joined:
                norm = normalize_title(joined)
                if norm in contests_summary:
                    title = norm
                else:
                    warnings.append(
                        f"page {idx+1}: contest title not in summary: {norm!r}")
                    title = "SKIP"
                break
        if title == "SKIP":
            cur_title = None   # drop until the next contest we recognize
            continue
        if title:
            cur_title = title
            state_for(title)
        if cur_title is None:
            continue
        st = states[cur_title]

        # ---- page columns
        cand_names = [c for c, _ in st.candidates]
        cand_xs, specials = build_columns(lines, cand_names)
        has_tv = "tv" in specials
        sig = (tuple(cand_xs), tuple(sorted(specials.items())))
        if sig not in st.chunks:
            k = len(cand_xs)
            if has_tv:
                if k == 0:
                    start_i = st.ptr
                else:
                    start_i = max(0, len(st.candidates) - k)
                    if k > len(st.candidates):
                        warnings.append(
                            f"{cur_title}: final chunk has {k} candidate columns "
                            f"but only {len(st.candidates)} candidates total")
                    elif start_i != st.ptr:
                        warnings.append(
                            f"{cur_title}: final chunk starts at {start_i} but ptr "
                            f"expects {st.ptr} (unmapped candidates in earlier chunks?)")
            else:
                start_i = st.ptr
                if start_i + k > len(st.candidates):
                    warnings.append(
                        f"{cur_title}: chunk columns {k} exceed remaining "
                        f"{len(st.candidates) - st.ptr}")
            chunk_cands = list(range(start_i, start_i + k))
            st.chunks[sig] = {"cands": chunk_cands, "cand_xs": cand_xs,
                              "has_tv": has_tv}
            if not has_tv:
                st.ptr = start_i + k
        chunk = st.chunks[sig]
        cols_by_x = {}
        for ci, cx in zip(chunk["cands"], chunk["cand_xs"]):
            cols_by_x[cx] = ("cand", ci)
        for kind, x in specials.items():
            cols_by_x[x] = (kind, None)

        # ---- pass 2: rows
        county_seen = False
        pending = []
        last_name = None
        page_rows = []   # (label_or_None, segs, pname_or_None, is_county)
        for top, ws in lines:
            if county_seen:
                break
            toks = [(w["x0"], w["text"]) for w in ws]
            texts = [t for _, t in toks]
            if any(t == "Page:" for t in texts):
                continue
            if any(t == "Cumulative" for t in texts):
                continue
            pseudo = ("Total" in texts and any(t in ("PA", "County") for t in texts)
                      and "Election" not in texts and "Mail-in" not in texts
                      and "Provisional" not in texts)
            anchors = []
            for k2, t in enumerate(texts):
                if t == "Election" and k2 + 1 < len(texts) and texts[k2 + 1] == "Day":
                    anchors.append((k2, 2))
                elif t in ("Mail-in", "Provisional", "Total"):
                    anchors.append((k2, 1))
            if pseudo:
                county_seen = True
                segs = []
                if anchors:
                    a0 = anchors[-1][0]
                    segs = [[(x, t) for x, t in toks[a0 + 1:] if is_num(t)]]
                else:
                    a0 = [k2 for k2, t in enumerate(texts) if t == "Total"][-1]
                    segs = [[(x, t) for x, t in toks[a0 + 1:] if is_num(t)]]
                page_rows.append(("total", segs, None, True))
                pending = []
                continue
            if anchors:
                if len(anchors) == 1:
                    a0, l0 = anchors[0]
                    segs = [[(x, t) for x, t in toks[a0 + l0:] if is_num(t)]]
                else:
                    segs = []
                    for j, (a0, l0) in enumerate(anchors):
                        nxt = anchors[j + 1][0] if j + 1 < len(anchors) else len(toks)
                        segs.append([(x, t) for x, t in toks[a0 + l0:a0 + nxt]
                                     if is_num(t)])
                label = ("ed" if texts[anchors[0][0]] == "Election" else
                         "mail" if texts[anchors[0][0]] == "Mail-in" else
                         "prov" if texts[anchors[0][0]] == "Provisional" else "total")
                pname = " ".join(pending) if pending else last_name
                page_rows.append((label, segs, pname, False))
                pending = []
                continue
            # name line: tokens left of the vote columns, not header words;
            # must contain a lowercase token (reversed header names are ALL CAPS)
            name_toks = [t for x, t in toks if x < 380 and t not in HEADER_WORDS
                         and t != "Page:" and not TIME_RE.match(t)
                         and not DATEISH_RE.match(t) and t not in REV_JUNK
                         and t not in ("DEM", "REP", "LIB", "NOP")]
            h = len(name_toks) // 2
            if len(name_toks) % 2 == 0 and h and name_toks[:h] == name_toks[h:]:
                name_toks = name_toks[:h]
            joined = " ".join(name_toks)
            if "(Vote" in joined or not name_toks or \
                    not any(re.search(r"[a-z]", t) for t in name_toks) or \
                    is_num(joined.replace(" ", "").replace("-", "")):
                pending = []
                continue
            if re.search(r"Statement|Report|Summary|Election Results", joined):
                pending = []
                continue
            pending.append(joined)
            last_name = " ".join(pending)

        # ---- assign rows
        for label, segs, pname, is_county in page_rows:
            if not is_county and not pname:
                warnings.append(f"page {idx+1} ({cur_title}): data row with no precinct")
                continue
            if is_county:
                rec = {"cand": defaultdict(dict), "wi": {}, "tv": {}}
                county_rows[cur_title].append(rec)
            for seg in segs:
                for x, tok in seg:
                    got = map_value(x, cols_by_x)
                    if got is None:
                        warnings.append(
                            f"page {idx+1} ({cur_title}{'' if is_county else '/' + str(pname)}): "
                            f"value {tok}@{x:.0f} unmapped")
                        continue
                    kind, ci = got
                    if kind == "cand":
                        ci_key = ci
                        if ci_key is not None and ci_key >= len(st.candidates):
                            warnings.append(
                                f"page {idx+1} ({cur_title}): value for column "
                                f"{ci_key} beyond summary candidates")
                            continue
                        if is_county:
                            target = rec["cand"][ci_key]
                            if label in target:
                                warnings.append(
                                    f"page {idx+1} ({cur_title}) county row: repeat {label}/{ci_key}")
                                continue
                            target[label] = to_int(tok)
                            continue
                        target = results[(cur_title, pname)].setdefault(ci_key, {})
                    elif kind == "wi":
                        target = (rec["wi"] if is_county
                                  else wi_vals[(cur_title, pname)])
                    elif kind == "tv":
                        target = (rec["tv"] if is_county
                                  else tv_vals[(cur_title, pname)])
                    elif kind == "rv":
                        if not is_county and pname:
                            registered[pname] = to_int(tok)
                        continue
                    else:
                        continue
                    if label in target:
                        warnings.append(
                            f"page {idx+1} ({cur_title}{'' if is_county else '/' + str(pname)}): "
                            f"repeat value for {label}/{kind}")
                        continue
                    target[label] = to_int(tok)
    return states, results, wi_vals, tv_vals, registered, county_rows


def normalize_title(title):
    t = re.sub(r"\s+", " ", title).strip()
    t = re.sub(r"\s*\(\s*Vote\s*(?:for)?\s*\d*\s*\)?\s*$", "", t)
    t = t.rstrip("( ").strip()
    return t


# --------------------------------------------------------------------------
# 4. Output
# --------------------------------------------------------------------------

OFFICE_MAP = [
    (re.compile(r"^United States Senator", re.I), ("U.S. Senate", "")),
    (re.compile(r"^Lieutenant Governor", re.I), ("Lieutenant Governor", "")),
    (re.compile(r"^Governor", re.I), ("Governor", "")),
    (re.compile(r"^Representative in Congress \((\d+)\w\w Congressional District\)", re.I),
     ("U.S. House", "D")),
    (re.compile(r"^Senator in the General Assembly \((\d+)\w\w Senatorial District\)", re.I),
     ("State Senate", "D")),
    (re.compile(r"^Representative in the General Assembly \((\d+)\w\w Legislative District\)", re.I),
     ("State House", "D")),
]


def contest_party(title):
    m = re.search(r"-(DEM|REP|LIB|GRN)$", title)
    if m:
        return m.group(1)
    if re.search(r"Democratic", title):
        return "DEM"
    if re.search(r"Republican", title):
        return "REP"
    return ""


def office_district(title):
    t = re.sub(r"-(DEM|REP|LIB|GRN)$", "", title)
    t = t.strip()
    for rx, (office, dist) in OFFICE_MAP:
        m = rx.match(t)
        if m:
            return office, (m.group(1) if dist == "D" else dist)
    return t, ""


def q(s):
    s = str(s)
    return '"' + s.replace('"', '""') + '"' if ('"' in s or "," in s) else s


# --------------------------------------------------------------------------
# multiprocessing page extraction
# --------------------------------------------------------------------------

_PDF = None


def _mp_init(path):
    global _PDF
    _PDF = pdfplumber.open(path)


def _mp_page(idx):
    return page_lines(_PDF.pages[idx])


# --------------------------------------------------------------------------

def main():
    src_default = ("/Users/dwillis/code/openelections-sources-pa/2022/primary/"
                   "Montgomery PA 2022 Primary Results Book.pdf")
    out_default = "2022/counties/20220517__pa__primary__montgomery__precinct.csv"
    args = [a for a in sys.argv[1:] if not a.startswith("--pages")]
    pages_arg = None
    for a in sys.argv[1:]:
        if a.startswith("--pages"):
            pages_arg = a.split("=", 1)[1]
    src = args[0] if len(args) > 0 else src_default
    out_path = args[1] if len(args) > 1 else out_default

    warnings = []
    pdf = pdfplumber.open(src)
    total_pages = len(pdf.pages)

    # summary = pages up to the "Statement of Votes Cast" title page
    sovc_start = None
    for i in range(2, min(400, total_pages)):
        t = pdf.pages[i].extract_text() or ""
        if "Votes Cast" in t and "Statement" in t:
            sovc_start = i
            break
    # turnout runs from the next page to the first page carrying a contest title
    detail_start = None
    for i in range(sovc_start + 1, min(sovc_start + 400, total_pages)):
        t = pdf.pages[i].extract_text() or ""
        if "(Vote for" in t:
            detail_start = i
            break
    print(f"pages={total_pages} summary=[2,{sovc_start}) "
          f"turnout=[{sovc_start + 1},{detail_start}) detail=[{detail_start},{total_pages})")

    summary = parse_summary(pdf, sovc_start, warnings)
    print(f"summary contests: {len(summary)}; named write-in candidates: "
          f"{sum(len(c['named_wi']) for c in summary.values())}")

    turnout_pages = [page_lines(pdf.pages[i])
                     for i in range(sovc_start + 1, detail_start)]
    turnout = parse_turnout(enumerate(turnout_pages), warnings)
    print(f"turnout precincts: {len(turnout)}")

    if pages_arg:
        lo, hi = (int(v) for v in pages_arg.split(":"))
    else:
        lo, hi = detail_start, total_pages

    page_data = {}
    if len(range(lo, hi)) > 100:
        try:
            from multiprocessing import Pool
            with Pool(processes=4, initializer=_mp_init, initargs=(src,)) as pool:
                got = pool.map(_mp_page, range(lo, hi), chunksize=16)
            page_data = dict(zip(range(lo, hi), got))
            print("parallel page extraction ok")
        except Exception as e:
            print("parallel extraction failed, sequential:", e)
    if not page_data:
        page_data = {i: page_lines(pdf.pages[i]) for i in range(lo, hi)}

    states, results, wi_vals, tv_vals, registered, county_rows = \
        parse_detail(page_data, lo, hi, summary, warnings)
    print(f"contests parsed: {len(states)}; (contest,precinct) pairs: {len(results)}")

    # ---- verification 1: per-contest sums vs the book's own summary totals
    print("\n== internal verification vs book summary ==")
    n_ok = n_bad = 0
    for title, cont in summary.items():
        st = states.get(title)
        if st is None:
            n_bad += 1
            print(f"  MISSING contest: {title}")
            continue
        ok = True
        for ci, (name, party, sums) in enumerate(cont["candidates"]):
            agg = dict(ed=0, mail=0, prov=0, total=0)
            for (t2, p), r in results.items():
                if t2 != title:
                    continue
                v = r.get(ci, {})
                for k2 in agg:
                    agg[k2] += v.get(k2, 0)
            if (agg["ed"], agg["mail"], agg["prov"], agg["total"]) != sums:
                ok = False
                print(f"  MISMATCH {title} | {name} ({party}): parsed "
                      f"{agg['ed']}/{agg['mail']}/{agg['prov']}/{agg['total']} "
                      f"vs summary {sums}")
        if cont["writein"]:
            agg = defaultdict(int)
            for (t2, p), v in wi_vals.items():
                if t2 != title:
                    continue
                for k2 in ("ed", "mail", "prov", "total"):
                    agg[k2] += v.get(k2, 0)
            if (agg["ed"], agg["mail"], agg["prov"], agg["total"]) != cont["writein"]:
                ok = False
                print(f"  MISMATCH {title} write-in: parsed "
                      f"{agg['ed']}/{agg['mail']}/{agg['prov']}/{agg['total']} "
                      f"vs summary {cont['writein']}")
        if cont["total_votes"]:
            agg = defaultdict(int)
            for (t2, p), v in tv_vals.items():
                if t2 != title:
                    continue
                for k2 in ("ed", "mail", "prov", "total"):
                    agg[k2] += v.get(k2, 0)
            if (agg["ed"], agg["mail"], agg["prov"], agg["total"]) != cont["total_votes"]:
                ok = False
                print(f"  MISMATCH {title} Total Votes: parsed "
                      f"{agg['ed']}/{agg['mail']}/{agg['prov']}/{agg['total']} "
                      f"vs summary {cont['total_votes']}")
        n_ok += 1 if ok else 0
        if not ok:
            n_bad += 1
    print(f"contest-level checks: {n_ok} OK, {n_bad} failed/missing")

    # ---- county-total rows seen in the SOVC must match the precinct sums
    c_ok = c_bad = 0
    for title in states:
        s_cand = defaultdict(lambda: defaultdict(int))
        s_wi = defaultdict(int)
        s_tv = defaultdict(int)
        pairs = set((t2, p) for t2, p in results if t2 == title)
        pairs |= set((t2, p) for t2, p in wi_vals if t2 == title)
        pairs |= set((t2, p) for t2, p in tv_vals if t2 == title)
        for (t2, p) in pairs:
            for ci, v in results.get((t2, p), {}).items():
                for k2 in ("ed", "mail", "prov", "total"):
                    s_cand[ci][k2] += v.get(k2, 0)
            for k2 in ("ed", "mail", "prov", "total"):
                s_wi[k2] += wi_vals.get((t2, p), {}).get(k2, 0)
                s_tv[k2] += tv_vals.get((t2, p), {}).get(k2, 0)
        for rec in county_rows[title]:
            for ci, vals in rec["cand"].items():
                for k2, v in vals.items():
                    if s_cand[ci][k2] != v:
                        c_bad += 1
                        print(f"  COUNTY-MISMATCH {title} cand#{ci} {k2}: "
                              f"{v} vs parsed {s_cand[ci][k2]}")
                    else:
                        c_ok += 1
            for k2, v in rec["wi"].items():
                if s_wi[k2] != v:
                    c_bad += 1
                    print(f"  COUNTY-MISMATCH {title} write-in {k2}: "
                          f"{v} vs parsed {s_wi[k2]}")
                else:
                    c_ok += 1
            for k2, v in rec["tv"].items():
                if s_tv[k2] != v:
                    c_bad += 1
                    print(f"  COUNTY-MISMATCH {title} Total Votes {k2}: "
                          f"{v} vs parsed {s_tv[k2]}")
                else:
                    c_ok += 1
    print(f"county-row checks: {c_ok} OK, {c_bad} failed")

    # ---- verification 2: per-(contest,precinct) cands+write-in == Total Votes
    v_ok = v_bad = 0
    for (title, p), tv in sorted(tv_vals.items()):
        s = sum(r.get("total", 0) for r in results[(title, p)].values())
        s += wi_vals[(title, p)].get("total", 0)
        tvt = tv.get("total")
        if tvt is not None and s != tvt:
            v_bad += 1
            print(f"  ROW-MISMATCH {title}/{p}: cands+wi {s} != Total Votes {tvt}")
        else:
            v_ok += 1
    print(f"per-precinct Total-Votes checks: {v_ok} OK, {v_bad} failed")

    # ---- turnout cross-checks
    n_rv = sum((t.get("rv") or 0) for t in turnout.values())
    n_bc = sum(t["total"] for t in turnout.values())
    print(f"RV across precincts: {n_rv}; Ballots Cast: {n_bc} "
          f"(book county: 505,661 / 199,383 = 117,775 DEM + 81,333 REP + 275 NP)")

    # ---- SOVC registered values vs turnout (spot check)
    mism = [p for p, rv in registered.items() if p in turnout
            and turnout[p].get("rv") not in (rv, None)]
    if mism:
        print(f"  SOVC-vs-turnout RV mismatches: {len(mism)} {mism[:5]}")

    # ---- build rows
    out_lines = ["county,precinct,office,district,party,candidate,votes,"
                 "election_day,mail,provisional"]
    contest_order = list(summary.keys())
    all_precincts = list(turnout.keys())
    extra = sorted(set(p for _, p in results) - set(all_precincts))
    if extra:
        print(f"precincts in detail but not turnout: {len(extra)} {extra[:5]}")
        all_precincts += extra
    n_rows = 0
    for p in all_precincts:
        rec = turnout.get(p, {})
        if rec.get("rv") is not None:
            out_lines.append(f"{COUNTY},{q(p)},Registered Voters,,,,{rec['rv']},,,")
            n_rows += 1
        if rec.get("total") is not None:
            out_lines.append(
                f"{COUNTY},{q(p)},Ballots Cast,,,,{rec['total']},{rec.get('ed', '')},"
                f"{rec.get('mail', '')},{rec.get('prov', '')}")
            n_rows += 1
        for title in contest_order:
            r = results.get((title, p))
            if not r:
                # all-write-in contests have no candidate rows, but still
                # carry the Write In rows
                if not wi_vals.get((title, p)):
                    continue
                r = {}
            office, dist = office_district(title)
            cont = summary[title]
            cparty = contest_party(title)
            wi = wi_vals.get((title, p), {})
            for ci, (cand, party, _s) in enumerate(cont["candidates"]):
                v = r.get(ci, {})
                pr = party or cparty
                cand_out = {"YES": "Yes", "NO": "No"}.get(cand.upper(), cand)
                out_lines.append(
                    f"{COUNTY},{q(p)},{q(office)},{dist},{pr},{q(cand_out)},"
                    f"{v.get('total', 0)},{v.get('ed', '')},{v.get('mail', '')},"
                    f"{v.get('prov', '')}")
                n_rows += 1
            if wi:
                out_lines.append(
                    f"{COUNTY},{q(p)},{q(office)},{dist},{cparty},Write Ins,"
                    f"{wi.get('total', 0)},{wi.get('ed', '')},{wi.get('mail', '')},"
                    f"{wi.get('prov', '')}")
                n_rows += 1
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nwrote {out_path}: {n_rows} rows")
    print(f"warnings: {len(warnings)}")
    dedup = defaultdict(int)
    for w in warnings:
        dedup[w[:90]] += 1
    for key, cnt in list(dedup.items())[:25]:
        print(f"   {cnt}x {key}")


if __name__ == "__main__":
    main()