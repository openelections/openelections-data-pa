#!/usr/bin/env python3
"""Parser for the Montgomery County PA 2022 General Election "ge220 results book"
(Hart eSlate Election Summary Report + Statement of Votes Cast).

Source layout (single PDF):
  * 8-page Election Summary Report: county totals per contest. Used for the
    ordered candidate lists and for internal verification.
  * Turnout section: per precinct Registered Voters / Cards Cast / Voters Cast
    by counting group (Election Day / Mail-in / Provisional).
  * Per-precinct Statement of Votes Cast: each contest prints candidate columns
    in "chunks"; a chunk shows a subset of candidates (each with Election Day /
    Mail-in / Provisional / Total rows) for a block of precincts, and the chunks
    cycle 1,2,1,2... through the precinct list.  The final chunk adds Write-in
    and Total Votes columns and a county-total row.  Candidate name glyphs in
    the SOVC headers render reversed, so names come from the Summary section
    and chunk columns are mapped to candidates via party tokens + column counts.

Usage:
    uv run python parsers/pa_montgomery_general_2022_results_parser.py <input.pdf> <output.csv>
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
    "Groups", "MONTGOMERY", "Summary", "Results",
}
NUM_RE = re.compile(r"^[\d,]+$")
PCT_RE = re.compile(r"^[\d.]+%$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
DATEISH_RE = re.compile(r"^\d{1,2}/\d{2}/\d{4}$|^PM$|^AM$")
# reversed renderings of SOVC header words — never part of a precinct name
REV_JUNK = {"setoV", "latoT", "ni-etirW", "semiT", "tsaC", "deretsigeR",
            "sretoV", "Times", "Votes", "Total", "Write-in", "Registered",
            "Cast", "Voters"}


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
    return [sorted(lines[top], key=lambda w: w["x0"]) for top in sorted(lines)]


def is_page_header(ws):
    return any(w["text"] == "Page:" for w in ws)


# --------------------------------------------------------------------------
# 1. Summary section
# --------------------------------------------------------------------------

def parse_summary(pdf, max_page):
    """OrderedDict title -> {candidates: [(name, party, (ed, mail, prov, total))],
    writein, total_votes}.

    Wrapped candidate names appear as alpha-only lines adjacent to (above or
    below) the party+numbers row; each alpha line belongs to the nearest
    numbers row (ties go to the earlier row)."""
    NUMT = re.compile(r"\d[\d,]*(?![\d/%])")
    BIG_NUM = re.compile(r"\d,\d|\d{4,}")

    def nums_in(s):
        return NUMT.findall(s)

    raw = []
    for i in range(max_page):
        for ws in page_lines(pdf.pages[i]):
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
            cur = {"candidates": [], "writein": None, "total_votes": None}
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
    # name line directly above a numbers row belongs to it).  Write-in /
    # Total Votes rows are never name targets, so a fragment adjacent to one
    # falls back to its next-nearest row.
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
        else:
            cont["candidates"].append((full, party, vals))
    return contests


# --------------------------------------------------------------------------
# 2. Turnout section
# --------------------------------------------------------------------------

def parse_turnout(pdf, start, end, warnings):
    """Precinct -> {rv, ed, mail, prov, total} from the turnout-by-precinct
    pages."""
    turnout = OrderedDict()
    cur_name = None
    pending_names = []
    for idx in range(start, end):
        for ws in page_lines(pdf.pages[idx]):
            if is_page_header(ws):
                continue
            toks = [w["text"] for w in ws]
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
# 3. SOVC detail section
# --------------------------------------------------------------------------

class ContestState:
    def __init__(self, title, candidates):
        self.title = title
        self.candidates = candidates      # [(name, party)] from summary
        self.chunks = OrderedDict()       # sig -> chunk dict
        self.ptr = 0


def parse_detail(pdf, start, end, contests_summary, warnings):
    results = defaultdict(dict)        # (title, precinct) -> col_i -> {ed, mail, prov, total}
    tv_col = defaultdict(dict)         # (title, precinct) -> page -> total-votes value
    registered = {}                    # precinct -> rv
    county_chunk_totals = defaultdict(list)  # (title, sig) -> [right_vals]
    states = {}
    cur_title = None
    cur_precinct = None
    cur_chunk = None
    prev_sig = None

    for idx in range(start, end):
        lines = page_lines(pdf.pages[idx])
        # ---- pass 1: page header
        title = None
        parties = []
        has_wi = has_tv = has_left = False
        for ws in lines:
            if is_page_header(ws):
                continue
            texts = [w["text"] for w in ws]
            joined = " ".join(texts)
            if "(Vote" in joined and "Page:" not in joined and \
                    not any(is_num(t) for t in texts) and title is None:
                title = re.sub(r"\s+", " ", joined)
            for w in ws:
                rt = rev(w["text"])
                m = re.fullmatch(r"\(([A-Z]{2,4})\)", rt)
                if not m:
                    m = re.fullmatch(r"\(([A-Z]{2,4})\)", w["text"])
                if m:
                    parties.append((round(w["x0"]), m.group(1)))
                if w["text"] in ("ni-etirW", "Write-in"):
                    has_wi = True
                if w["text"] in ("setoV", "latoT"):
                    has_tv = True
                if rt in ("semiT", "tsaC", "deretsigeR") or w["text"] in ("Times", "Registered"):
                    has_left = True
        parties = [p for _, p in sorted(parties)]
        if title:
            norm = normalize_title(title)
            if norm not in states:
                if norm not in contests_summary:
                    warnings.append(f"contest title not in summary: {norm!r}")
                    cur_title = None
                    continue
                cands = contests_summary[norm]["candidates"]
                states[norm] = ContestState(norm, [(c, p) for c, p, _ in cands])
            cur_title = norm
            prev_sig = None
            cur_chunk = None
        if cur_title is None:
            continue
        st = states[cur_title]

        # ---- pass 2: rows
        rows = []       # (label, left_vals, right_vals, precinct)
        pending = []
        last_name = None
        county_seen = False
        for ws in lines:
            if is_page_header(ws) or county_seen:
                continue
            toks = [(w["x0"], w["text"]) for w in ws]
            texts = [t for _, t in toks]
            if any(t == "Cumulative" for t in texts):
                continue
            pseudo = ("Total" in texts and any(t in ("PA", "County") for t in texts)
                      and "Election" not in texts and "Mail-in" not in texts
                      and "Provisional" not in texts)
            anchors = []
            for k, t in enumerate(texts):
                if t == "Election" and k + 1 < len(texts) and texts[k + 1] == "Day":
                    anchors.append((k, 2))
                elif t in ("Mail-in", "Provisional", "Total"):
                    anchors.append((k, 1))
            nums = [(x, t) for x, t in toks if is_num(t)]
            if "N/A" in texts:
                pending = []   # Cumulative junk block
                continue
            if anchors and nums and not pseudo:
                a0, l0 = anchors[0]
                if len(anchors) >= 2:
                    a1, l1 = anchors[1]
                    left_vals = [t for x, t in toks[a0 + l0:a1] if is_num(t)]
                    right_vals = [t for x, t in toks[a1 + l1:] if is_num(t)]
                else:
                    after = [t for x, t in toks[a0 + l0:] if is_num(t)]
                    if has_left:
                        left_vals, right_vals = after[:2], after[2:]
                    else:
                        left_vals, right_vals = [], after
                label = "ed" if texts[a0] == "Election" else \
                    {"Mail-in": "mail", "Provisional": "prov", "Total": "total"}[texts[a0]]
                pname = " ".join(pending) if pending else last_name
                rows.append((label, left_vals, right_vals, pname))
                pending = []
                continue
            if pseudo:
                a0 = [k for k, t in enumerate(texts) if t == "Total"][-1]
                right_vals = [t for x, t in toks[a0 + 1:] if is_num(t)]
                if right_vals:
                    sig = (len(right_vals), has_wi, has_tv, tuple(parties))
                    county_chunk_totals[(cur_title, sig)].append(
                        [to_int(v) for v in right_vals])
                county_seen = True   # everything below the county row is junk
                pending = []
                continue
            if anchors:
                pending = []
                continue
            # name line: tokens left of the right block, not header words,
            # must contain a lowercase token (reversed header names are ALL CAPS)
            name_toks = [t for x, t in toks if x < 380 and t not in HEADER_WORDS
                         and t != "Page:" and not TIME_RE.match(t) and not DATEISH_RE.match(t)]
            # split pages duplicate the precinct name (left half | right half)
            h = len(name_toks) // 2
            if len(name_toks) % 2 == 0 and h and name_toks[:h] == name_toks[h:]:
                name_toks = name_toks[:h]
            joined = " ".join(name_toks)
            if "(Vote" in joined or not name_toks or \
                    not any(re.search(r"[a-z]", t) for t in name_toks) or \
                    all(t in REV_JUNK or is_num(t) for t in name_toks):
                pending = []
                continue
            if re.search(r"Statement|Report|Summary|Election Results", joined):
                pending = []
                continue
            pending.append(joined)
            last_name = " ".join(pending)

        if not rows:
            continue
        right_counts = [len(r[2]) for r in rows if r[2]]
        n_right = max(set(right_counts), key=right_counts.count) if right_counts else 0
        bad = [c for c in right_counts if c != n_right]
        if bad:
            warnings.append(f"page {idx+1} ({cur_title}): rows with {bad} != {n_right} right values")
        sig = (n_right, has_wi, has_tv, tuple(parties))
        if sig != prev_sig:
            if sig not in st.chunks:
                k = n_right - int(has_wi) - int(has_tv)
                if has_tv:
                    cands = st.candidates[len(st.candidates) - k:] if k <= len(st.candidates) else []
                    if len(cands) != k:
                        warnings.append(
                            f"{cur_title}: final chunk expects {k} candidates, "
                            f"{len(st.candidates) - k} remain")
                else:
                    cands = st.candidates[st.ptr:st.ptr + k]
                    want = [p for _, p in cands]
                    if parties and want != parties:
                        warnings.append(
                            f"{cur_title}: chunk party mismatch {parties} vs candidates {want}")
                st.chunks[sig] = {"cands": cands, "has_tv": has_tv, "has_wi": has_wi,
                                  "n": n_right,
                                  "start": (len(st.candidates) - k) if has_tv else st.ptr}
                if not has_tv:
                    st.ptr += k
            cur_chunk = st.chunks[sig]
            prev_sig = sig

        for label, left_vals, right_vals, pname in rows:
            if not right_vals:
                if label == "total" and len(left_vals) == 2 and pname:
                    rv = to_int(left_vals[1])
                    if registered.get(pname, rv) != rv:
                        warnings.append(
                            f"registered mismatch {pname}: {registered.get(pname)} vs {rv}")
                    registered[pname] = rv
                continue
            if not pname:
                warnings.append(f"page {idx+1} ({cur_title}): data row with no precinct")
                continue
            cur_precinct = pname
            if len(right_vals) != cur_chunk["n"]:
                warnings.append(
                    f"page {idx+1} ({cur_title}/{pname}): {len(right_vals)} values, "
                    f"expected {cur_chunk['n']}")
                continue
            vals = [to_int(v) for v in right_vals]
            rec = results[(cur_title, pname)]
            start = cur_chunk["start"]
            ncols = cur_chunk["n"] - (1 if cur_chunk["has_tv"] else 0)
            for ci, v in enumerate(vals[:ncols]):
                rec.setdefault(start + ci, {})[label] = v
            if cur_chunk["has_tv"]:
                tv_col[(cur_title, pname)][idx] = vals[-1]
    return results, tv_col, registered, county_chunk_totals, states


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
    (re.compile(r"^Governor and Lieutenant Governor", re.I), ("Governor", "")),
    (re.compile(r"^Representative in Congress \((\d+)\w\w Congressional District\)", re.I),
     ("U.S. House", "D")),
    (re.compile(r"^Senator in the General Assembly \((\d+)\w\w Senatorial District\)", re.I),
     ("State Senate", "D")),
    (re.compile(r"^Representative in the General Assembly \((\d+)\w\w Legislative District\)", re.I),
     ("State House", "D")),
]


def office_district(title):
    for rx, (office, dist) in OFFICE_MAP:
        m = rx.match(title)
        if m:
            return office, (m.group(1) if dist == "D" else dist)
    return title, ""


def q(s):
    s = str(s)
    return '"' + s.replace('"', '""') + '"' if ('"' in s or "," in s) else s


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else \
        "/Users/dwillis/code/openelections-sources-pa/2022/general/Montgomery PA 2022 ge220-results-book.pdf"
    out_path = sys.argv[2] if len(sys.argv) > 2 else \
        "2022/counties/20221108__pa__general__montgomery__precinct.csv"

    warnings = []
    pdf = pdfplumber.open(src)
    total_pages = len(pdf.pages)

    sovc_start = None
    for i in range(min(20, total_pages)):
        t = pdf.pages[i].extract_text() or ""
        if "Statement of Votes Cast" in t:
            sovc_start = i
            break
    detail_start = None
    for i in range(sovc_start, total_pages):
        t = pdf.pages[i].extract_text() or ""
        if "United States Senator" in t and "Cards Cast" not in t:
            detail_start = i
            break

    print(f"pages={total_pages} turnout=[{sovc_start},{detail_start}) detail=[{detail_start},{total_pages})")
    summary = parse_summary(pdf, max_page=sovc_start)
    print(f"summary contests: {len(summary)}")
    for t, c in summary.items():
        print(f"  {t!r}: {len(c['candidates'])} cands, writein={c['writein'] is not None}")
    turnout = parse_turnout(pdf, sovc_start, detail_start, warnings)
    print(f"turnout precincts: {len(turnout)}")
    results, tv_col, registered, county_totals, states = parse_detail(
        pdf, detail_start, total_pages, summary, warnings)
    print(f"contests parsed: {len(set(t for t, _ in results))}; (contest,precinct) pairs: {len(results)}")

    # ---- verification: contest sums vs the book's own summary totals
    print("\n== internal verification ==")
    ok = True
    for title, cont in summary.items():
        st = states.get(title)
        if st is None:
            print(f"  MISSING contest: {title}")
            ok = False
            continue
        got = [c for ch in st.chunks.values() for c in ch["cands"]]
        if got != st.candidates:
            ok = False
            print(f"  COVERAGE {title}: {got} != {st.candidates}")
        for ci, (name, party, sums) in enumerate(cont["candidates"]):
            agg = {k: 0 for k in ("total", "ed", "mail", "prov")}
            for (t2, p), rec in results.items():
                if t2 != title:
                    continue
                for k in agg:
                    agg[k] += rec.get(ci, {}).get(k, 0)
            status = "OK " if tuple(agg[k] for k in ("ed", "mail", "prov", "total")) == sums else "MISMATCH"
            if status != "OK ":
                ok = False
                print(f"  {status} {title} | {name} ({party}): parsed ed={agg['ed']} mail={agg['mail']} "
                      f"prov={agg['prov']} tot={agg['total']} vs summary {sums}")
        wi_parts = {k: 0 for k in ("total", "ed", "mail", "prov")}
        wi_gidx = len(cont["candidates"])
        parsed_county = 0
        for (t2, p), rec in results.items():
            if t2 != title:
                continue
            s = 0
            for ci in range(wi_gidx):
                s += rec.get(ci, {}).get("total", 0)
            if cont["writein"]:
                wv = rec.get(wi_gidx, {})
                s += wv.get("total", 0)
                for k in wi_parts:
                    wi_parts[k] += wv.get(k, 0)
            parsed_county += s
            tvv = tv_col.get((t2, p))
            if tvv and max(tvv.values()) != s:
                ok = False
                print(f"  MISMATCH {title}/{p}: candidates+writein {s} != Total Votes {max(tvv.values())}")
        if cont["writein"]:
            exp = cont["writein"]
            if tuple(wi_parts[k] for k in ("ed", "mail", "prov", "total")) != exp:
                ok = False
                print(f"  MISMATCH {title} write-in: parsed {wi_parts} vs summary {exp}")
        for (t2, sig), rws in county_totals.items():
            if t2 != title or sig[2] is not True:  # final-chunk rows only
                continue
            for v in rws:
                # county row: candidate cols (+ write-in) + Total Votes column
                if v[-1] != parsed_county:
                    ok = False
                    print(f"  MISMATCH {title}: book county-total row TotalVotes {v[-1]} != parsed {parsed_county}")
    print("internal sums:", "ALL OK" if ok else "SEE MISMATCHES ABOVE")
    for w in warnings:
        print("  WARN:", w)

    # ---- build rows
    header = "county,precinct,office,district,party,candidate,votes,election_day,early_voting,provisional"
    out_lines = [header]
    contest_order = list(summary.keys())
    all_precincts = list(turnout.keys())
    extra = sorted(set(p for _, p in results) - set(all_precincts))
    if extra:
        warnings.append(f"precincts in detail but not turnout: {extra[:5]}...")
        all_precincts += extra
    for p in all_precincts:
        rec = turnout.get(p, {})
        rv = rec.get("rv", registered.get(p, ""))
        out_lines.append(f"{COUNTY},{q(p)},Registered Voters,,,,{rv},,,")
        if rec.get("total") is not None:
            out_lines.append(
                f"{COUNTY},{q(p)},Ballots Cast,,,,{rec['total']},{rec.get('ed','')},"
                f"{rec.get('mail','')},{rec.get('prov','')}")
        elif p in registered:
            out_lines.append(f"{COUNTY},{q(p)},Ballots Cast,,,,,,,")
        for title in contest_order:
            r = results.get((title, p))
            if not r:
                continue
            office, dist = office_district(title)
            cont = summary[title]
            st = states[title]
            wi = r.get(len(cont["candidates"]), {}) if cont["writein"] else {}
            for ci, (cand, party, _s) in enumerate(cont["candidates"]):
                v = r.get(ci, {})
                pr = "GRN" if party == "GRE" else party
                out_lines.append(
                    f"{COUNTY},{q(p)},{q(office)},{dist},{pr},{q(cand)},"
                    f"{v.get('total', 0)},{v.get('ed', '')},{v.get('mail', '')},{v.get('prov', '')}")
            if wi:
                out_lines.append(
                    f"{COUNTY},{q(p)},{q(office)},{dist},,Write Ins,"
                    f"{wi.get('total', 0)},{wi.get('ed', '')},{wi.get('mail', '')},{wi.get('prov', '')}")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nwrote {out_path}: {len(out_lines) - 1} rows")
    print(f"warnings: {len(warnings)}")


if __name__ == "__main__":
    main()