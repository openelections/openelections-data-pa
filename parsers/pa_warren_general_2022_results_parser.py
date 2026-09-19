#!/usr/bin/env python3
"""
Parse Warren County PA 2022 General Election precinct results.

Source: "Warren County PA 2022 General Precinct Results.pdf" (48 pages,
Dominion "Statement of Votes Cast", OFFICIAL RESULTS, report run 11/14/2022).

Layout: 48 pages = 6-page turnout summary + 4 contests of 12/12/12/6 pages.
Every contest page carries two side-by-side panels (sometimes only one):

  * a "Times Cast | Registered Voters" panel -- per-precinct turnout:
        Election Day  <cast>  <registered>
        Mail-In       <cast>  <registered>
        Provisional   <cast>  <registered>
        Total         <cast>  <registered>
  * candidate panels: the same precinct rows with one column per candidate,
    plus a "Write-In" and (on the contest's final page) a "Total Votes"
    column.  Candidate names/party tags are printed VERTICALLY; pdfplumber
    sees those glyphs as non-upright chars, which this script regroups into
    vertical lines (bucketed by x, read bottom-to-top) to rebuild the names.

Pages pair up: (Times Cast + first candidate group), then (remaining
candidates + Write-In + Total Votes) for the same batch of precincts; each
pair covers a fresh batch of precincts.  A "(Vote for N)" title line on each
contest's first page is what ties pages to contests.

Voter-privacy suppression: for some precincts Dominion prints "****" instead
of the Election Day / Mail-In / Provisional counts of every candidate column
("**** - Insufficient Turnout to Protect Voter Privacy", header note).  The
Total row is NEVER suppressed, so per-candidate totals are always available;
suppressed breakdown cells are emitted as empty breakdown columns.

Contests covered (the source contains ONLY these four):
  United States Senator                             -> U.S. Senate
  Governor and Lieutenant Governor                  -> Governor
      (source prints running-mate pairs "Josh Shapiro / Austin Davis";
       we keep the gubernatorial candidate only, matching the other
       2022 county files, e.g. venango/mercer)
  Representative in Congress - 15th District        -> U.S. House, district 15
  Representative in the General Assembly - 65th District
                                                    -> State House, district 65

Usage:
    uv run python parsers/pa_warren_general_2022_results_parser.py \
        <input.pdf> <output.csv> [--debug-headers]
"""

import csv
import re
import sys
from collections import defaultdict

import pdfplumber

COUNTY = "Warren"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "early_voting",
              "provisional"]

ROW_LABEL_RE = re.compile(r"^(Election(?:\s*Day)?|Mail-In|Provisional|Total)$")
STAR_RE = re.compile(r"^\*+$")
PARTY_TAG_RE = re.compile(r"\(([A-Z]{2,4})\)\s*$")
PARTY_TAG_START_RE = re.compile(r"^\(([A-Z]{2,4})\)\s*")
NUM_RE = re.compile(r"^[\d,]+$")


def clean_name(s):
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# low-level extraction


def page_words(page):
    """Return (upright_words, rotated_lines).

    upright_words: list of {'top','x0','x1','text'} like extract_words().
    rotated_lines: list of {'top','x0','x1','text'} for each vertical text
    line, text read bottom-to-top (un-reversed).
    """
    upright, rotated = [], []
    for c in page.chars:
        (upright if c.get("upright", True) else rotated).append(c)

    # upright words: group by line (top), then by x gap
    words = []
    by_top = {}
    for c in upright:
        key = round(c["top"] / 2)
        by_top.setdefault(key, []).append(c)
    for key in sorted(by_top):
        prev = None
        for c in sorted(by_top[key], key=lambda c: c["x0"]):
            if (prev is not None and c["x0"] - prev["x1"] < 1.5
                    and c.get("upright", True)):
                prev["text"] += c["text"]
                prev["x1"] = c["x1"]
            else:
                prev = {"top": c["top"], "x0": c["x0"], "x1": c["x1"],
                        "text": c["text"]}
                words.append(prev)

    # rotated lines: bucket by x0, read chars bottom-to-top; space glyphs in
    # the rotated text provide the word separators
    lines = []
    by_x = {}
    for c in rotated:
        key = round(c["x0"] / 4)
        by_x.setdefault(key, []).append(c)
    for key in sorted(by_x):
        cs = sorted(by_x[key], key=lambda c: -c["top"])
        text = "".join(c["text"] for c in cs)
        if not cs:
            continue
        lines_obj = {
            "top": min(c["top"] for c in cs),
            "bottom": max(c["bottom"] for c in cs),
            "x0": min(c["x0"] for c in cs),
            "x1": max(c["x1"] for c in cs),
            "text": re.sub(r"\s+", " ", text).strip(),
        }
        lines.append(lines_obj)
    return words, lines


# ---------------------------------------------------------------------------
# panels


class Panel:
    def __init__(self, x_start, x_end, up_words, rot_lines, prec_top):
        self.x_start = x_start
        self.up = [w for w in up_words if x_start - 1 <= w["x0"] < x_end]
        self.rot = [r for r in rot_lines if x_start - 1 <= r["x0"] < x_end]
        self.prec_top = prec_top  # top of the panel's "Precinct" header

    def is_turnout_panel(self):
        return any(r["text"] == "Times Cast" for r in self.rot)

    def columns(self):
        """Value columns from rotated header lines, clustered into columns."""
        hdr = [r for r in self.rot if r["top"] < self.prec_top - 1]
        if not hdr:
            return []
        lines = sorted(hdr, key=lambda r: r["x0"])
        cols = []
        for ln in lines:
            if cols and ln["x0"] - cols[-1]["_last_x"] < 40:
                cols[-1]["lines"].append(ln)
                cols[-1]["_last_x"] = ln["x0"]
            else:
                cols.append({"lines": [ln], "_last_x": ln["x0"]})
        out = []
        for c in cols:
            c["lines"].sort(key=lambda r: r["x0"])
            # join vertical text lines; no space after a hyphen
            parts = []
            for ln in c["lines"]:
                if parts and parts[-1].endswith("-"):
                    parts[-1] += ln["text"]
                else:
                    parts.append(ln["text"])
            text = clean_name(" ".join(parts))
            xs = [ln["x0"] for ln in c["lines"]]
            center = sum(xs) / len(xs)
            if text == "Times Cast":
                out.append({"x": center, "kind": "times", "name": "", "party": ""})
            elif text == "Registered Voters":
                out.append({"x": center, "kind": "registered", "name": "", "party": ""})
            elif text == "Total Votes":
                out.append({"x": center, "kind": "total_votes", "name": "", "party": ""})
            elif text == "Write-in":
                out.append({"x": center, "kind": "writein", "name": "", "party": ""})
            else:
                tag_m = PARTY_TAG_RE.search(text)
                start_m = PARTY_TAG_START_RE.match(text)
                if tag_m:
                    party = tag_m.group(1)
                    name = text[:tag_m.start()].strip()
                elif start_m:
                    party = start_m.group(1)
                    name = text[start_m.end():].strip()
                else:
                    party, name = "", text.strip()
                name = re.sub(r"\s*/\s*$", "", name).strip()
                out.append({"x": center, "kind": "candidate",
                            "name": name, "party": party})
        return out


def split_panels(up_words, rot_lines):
    prec_xs = sorted(w["x0"] for w in up_words if w["text"] == "Precinct")
    if not prec_xs:
        return []
    prec_tops = {w["x0"]: w["top"] for w in up_words if w["text"] == "Precinct"}
    panels = []
    for i, x in enumerate(prec_xs):
        x_end = prec_xs[i + 1] if i + 1 < len(prec_xs) else 10 ** 6
        panels.append(Panel(x, x_end, up_words, rot_lines, prec_tops[x]))
    return panels


def group_rows(words, y_tol=3.5):
    rows = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1][0] <= y_tol:
            rows[-1][1].append(w)
        else:
            rows.append([w["top"], [w]])
    return rows


def parse_value(tok):
    if STAR_RE.match(tok):
        return None, True
    try:
        return int(tok.replace(",", "")), False
    except ValueError:
        return None, False


def detect_contest_title(row_words):
    texts = [w["text"] for w in sorted(row_words, key=lambda w: w["x0"])
             if w["x0"] < 400]
    joined = " ".join(texts)
    m = re.match(r"^(.*?)\s*\(\s*Vote\s+for\s+(\d+)\s*\)", joined)
    if not m:
        return None
    title = m.group(1).strip()
    if title == "United States Senator":
        return ("U.S. Senate", "")
    if title == "Governor and Lieutenant Governor":
        return ("Governor", "")
    m2 = re.match(r"^Representative in Congress\s*-\s*(\d+)(?:st|nd|rd|th)?\s+District$",
                  title, re.I)
    if m2:
        return ("U.S. House", m2.group(1))
    m2 = re.match(
        r"^Representative in the General Assembly\s*-\s*(\d+)(?:st|nd|rd|th)?\s+District$",
        title, re.I)
    if m2:
        return ("State House", m2.group(1))
    return (title, "")


# ---------------------------------------------------------------------------
# main parse


def parse_pdf(pdf_path, debug_headers=False):
    pdf = pdfplumber.open(pdf_path)
    results = []            # dicts: precinct, office, district, party,
                            # candidate, votes, row_label
    stats_rows = {}         # precinct -> {rv, ed, mi, pr, total}
    stats_conflicts = []
    county_totals = {}      # office -> {'candidates': {(name,party): v}, ...}
    printed_tv = {}         # (precinct, office) -> {label: total_votes}
    current_office, current_district = None, ""

    for page in pdf.pages:
        up, rot = page_words(page)
        page_top = min(w["top"] for w in up) if up else 0
        up = [w for w in up if w["top"] > page_top + 8]
        rot = [r for r in rot if r["top"] > page_top + 8]

        for top, rwords in group_rows(up):
            t = detect_contest_title(rwords)
            if t:
                current_office, current_district = t
                break

        if current_office is None:
            continue  # turnout-summary pages 1-6

        panels = split_panels(up, rot)
        for panel in panels:
            columns = panel.columns()
            if debug_headers:
                print(f"page {page.page_number} panel@{panel.x_start:.0f} "
                      f"cols={[(c['kind'], c['name'], c['party']) for c in columns]}")
            if not columns:
                continue
            col_x = [c["x"] for c in columns]

            def col_for(tok):
                x = (tok["x0"] + tok["x1"]) / 2
                best, bestd = None, 1e9
                for i, cx in enumerate(col_x):
                    d = abs(x - cx)
                    if d < bestd:
                        best, bestd = i, d
                return best if bestd < 45 else None

            body = [w for w in panel.up if w["top"] > panel.prec_top + 1]
            current_precinct = None
            for top, rwords in group_rows(body):
                texts = [w["text"] for w in sorted(rwords, key=lambda w: w["x0"])]
                label = next((t for t in texts if ROW_LABEL_RE.match(t)), None)
                if label and label.startswith("Election"):
                    label = "Election"
                if label is None:
                    name = clean_name(" ".join(texts))
                    if name.startswith("Warren County - Total"):
                        current_precinct = "@COUNTY_TOTAL@"
                        label = "Total"  # values live on this same row
                    elif name.startswith("Warren County") or name.startswith("Cumulative"):
                        current_precinct = None
                        continue
                    else:
                        current_precinct = name
                        continue

                if current_precinct is None:
                    continue

                value_tokens = [w for w in rwords
                                if (STAR_RE.match(w["text"]) or NUM_RE.match(w["text"]))
                                and w["x0"] > col_x[0] - 30]
                vals = {}
                for w in value_tokens:
                    idx = col_for(w)
                    if idx is None:
                        continue
                    vals[idx] = parse_value(w["text"])

                if panel.is_turnout_panel():
                    ti = next((i for i, c in enumerate(columns) if c["kind"] == "times"), None)
                    ri = next((i for i, c in enumerate(columns) if c["kind"] == "registered"), None)
                    t_val = vals.get(ti, (None, False))[0]
                    r_val = vals.get(ri, (None, False))[0]
                    if current_precinct == "@COUNTY_TOTAL@":
                        if label == "Total":
                            county_totals.setdefault(current_office, {})["tc"] = t_val
                        continue
                    rec = stats_rows.setdefault(current_precinct,
                                                {"rv": None, "ed": None,
                                                 "mi": None, "pr": None,
                                                 "total": None})
                    field = {"Election": "ed", "Mail-In": "mi",
                             "Provisional": "pr", "Total": "total"}.get(label)
                    if field:
                        if rec[field] is not None and rec[field] != t_val:
                            stats_conflicts.append((current_precinct, label))
                        else:
                            rec[field] = t_val
                    if r_val is not None:
                        if rec["rv"] is not None and rec["rv"] != r_val:
                            stats_conflicts.append((current_precinct, "rv"))
                        else:
                            rec["rv"] = r_val
                    continue

                # candidate panel
                if current_precinct == "@COUNTY_TOTAL@":
                    ct = county_totals.setdefault(current_office, {})
                    for i, c in enumerate(columns):
                        v = vals.get(i, (None, False))[0]
                        if v is None:
                            continue
                        if c["kind"] == "candidate":
                            ct.setdefault("candidates", {})[(c["name"], c["party"])] = v
                        elif c["kind"] == "writein" and label == "Total":
                            ct["writein"] = v
                        elif c["kind"] == "total_votes" and label == "Total":
                            ct["tv"] = v
                    continue

                for i, c in enumerate(columns):
                    v, supp = vals.get(i, (None, False))
                    if v is None:
                        continue
                    if c["kind"] == "total_votes":
                        printed_tv.setdefault((current_precinct, current_office), {})[label] = v
                        continue
                    if c["kind"] == "candidate":
                        name, party = c["name"], c["party"]
                        if current_office == "Governor":
                            name = re.split(r"\s*/\s*", name)[0].strip()
                        results.append({
                            "precinct": current_precinct, "office": current_office,
                            "district": current_district, "party": party,
                            "candidate": name, "votes": str(v), "row_label": label,
                        })
                    elif c["kind"] == "writein":
                        results.append({
                            "precinct": current_precinct, "office": current_office,
                            "district": current_district, "party": "",
                            "candidate": "Write Ins", "votes": str(v),
                            "row_label": label,
                        })
    pdf.close()
    return results, stats_rows, stats_conflicts, county_totals, printed_tv


def pivot_rows(results, stats_rows):
    agg = {}
    order = []
    for r in results:
        key = (r["precinct"], r["office"], r["candidate"], r["party"], r["district"])
        if key not in agg:
            agg[key] = {"votes": None, "election_day": None,
                        "early_voting": None, "provisional": None}
            order.append(key)
        field = {"Total": "votes", "Election": "election_day",
                 "Election Day": "election_day", "Mail-In": "early_voting",
                 "Provisional": "provisional"}[r["row_label"]]
        a = agg[key]
        if field == "votes":
            a[field] = r["votes"]
        elif a[field] is None:
            a[field] = r["votes"]

    def emit(precinct, office, district, party, candidate, votes, ed, ev, pr):
        return {"county": COUNTY, "precinct": precinct, "office": office,
                "district": district, "party": party, "candidate": candidate,
                "votes": votes, "election_day": ed, "early_voting": ev,
                "provisional": pr}

    precinct_order = []
    for key in order:
        if key[0] not in precinct_order:
            precinct_order.append(key[0])

    for p in precinct_order:
        s = stats_rows.get(p)
        if s:
            yield {"county": COUNTY, "precinct": p, "office": "Registered Voters",
                   "district": "", "party": "", "candidate": "",
                   "votes": "" if s["rv"] is None else s["rv"],
                   "election_day": "", "early_voting": "", "provisional": ""}
            yield {"county": COUNTY, "precinct": p, "office": "Ballots Cast",
                   "district": "", "party": "", "candidate": "",
                   "votes": "" if s["total"] is None else s["total"],
                   "election_day": "" if s["ed"] is None else s["ed"],
                   "early_voting": "" if s["mi"] is None else s["mi"],
                   "provisional": "" if s["pr"] is None else s["pr"]}
        for key in order:
            if key[0] != p:
                continue
            a = agg[key]
            yield {"county": COUNTY, "precinct": key[0], "office": key[1],
                   "district": key[4], "party": key[3], "candidate": key[2],
                   "votes": a["votes"] or "", "election_day": a["election_day"] or "",
                   "early_voting": a["early_voting"] or "",
                   "provisional": a["provisional"] or ""}


def main():
    args = [a for a in sys.argv[1:]]
    debug = "--debug-headers" in args
    args = [a for a in args if a != "--debug-headers"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf> <output.csv> [--debug-headers]")
    src, dst = args

    results, stats_rows, stats_conflicts, county_totals, printed_tv = parse_pdf(src, debug)

    out_rows = list(pivot_rows(results, stats_rows))
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    print(f"wrote {len(out_rows)} rows -> {dst}")
    print(f"precincts with turnout stats: {len(stats_rows)}")
    if stats_conflicts:
        print(f"WARNING: turnout stat conflicts: {sorted(set(stats_conflicts))[:10]}")

    # verification 1: per (precinct, office): candidates+writeins (Total rows)
    # == printed Total Votes column
    summed = {}
    for r in results:
        if r["row_label"] != "Total":
            continue
        key = (r["precinct"], r["office"])
        s = summed.setdefault(key, {"c": 0, "w": 0})
        if r["candidate"] == "Write Ins":
            s["w"] += int(r["votes"])
        else:
            s["c"] += int(r["votes"])
    tv_checked = tv_mismatch = 0
    for (p, o), tvs in sorted(printed_tv.items()):
        if "Total" not in tvs or (p, o) not in summed:
            continue
        tv_checked += 1
        s = summed[(p, o)]
        if s["c"] + s["w"] != tvs["Total"]:
            tv_mismatch += 1
            if tv_mismatch <= 10:
                print(f"  TV MISMATCH {p} / {o}: summed {s['c']}+{s['w']}"
                      f" != Total Votes {tvs['Total']}")
    print(f"verification (printed Total Votes): {tv_checked} precinct-contests "
          f"checked, {tv_mismatch} mismatches")

    print("\ncontest countywide totals (source's own 'Warren County - Total'):")
    for o, ct in county_totals.items():
        print(f"  {o}: candidates={ct.get('candidates')} "
              f"writein={ct.get('writein')} total_votes={ct.get('tv')} "
              f"times_cast={ct.get('tc')}")

    return out_rows


if __name__ == "__main__":
    main()