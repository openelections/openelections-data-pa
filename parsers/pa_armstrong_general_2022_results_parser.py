#!/usr/bin/env python3
"""
Parser for Armstrong County 2022 General Election precinct results.

Input: the county-wide "Armstrong PA general precinct.pdf" (Electionware
"Election Summary Report", 62 per-precinct sections, one per precinct, each
printing contests with Election Day / Mail / Provisional / Total columns).

The PDF is parsed geometrically with pdfplumber: multi-line candidate name
cells are reassembled by assigning each bare name-fragment line to the
vertically nearest vote-counts line (the party + numbers line sits midway
inside its candidate's cell), so wrapped names such as
'DOUGLAS V / MASTRIANO/CARRIE LEWIS [REP 17437 ...] / DELROSSO' come out whole.

Usage:
    python parsers/pa_armstrong_general_2022_results_parser.py <pdf> <output_csv>

Office normalization: UNITED STATES SENATOR -> U.S. Senate,
GOVERNOR/LIEUTENANT GOVERNOR -> Governor, REPRESENTATIVE IN CONGRESS <n>th
CONGRESSIONAL DISTRICT -> U.S. House / district <n>, REPRESENTATIVE IN THE
GENERAL ASSEMBLY <n>th DISTRICT -> State House / district <n>.
All Scattered / Unresolved write-in rows of a contest are aggregated into a
single 'Write Ins' row. Registered Voters / Ballots Cast metadata rows are
emitted per precinct (Ballots Cast keeps its ED/mail/provisional breakdown,
mapped to the 2022 election_day/early_voting/provisional columns; this source
has no separate absentee column).
"""

import argparse
import csv
import os
import re
import sys

import pdfplumber

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "early_voting",
    "provisional",
]

PARTIES = {"DEM/REP", "DEM", "REP", "LIB", "GRN", "KEY", "CON", "IND"}
PARTY_X = (140, 180)  # x-range of the party column on candidate rows
NUM_RE = re.compile(r"^[\d,]+$")
PCT_RE = re.compile(r"^[\d,]+\.\d+%$")
VOTE_FOR_RE = re.compile(r"\(Vote for (\d+)\)\s*$")
SUMMARY_FOR_RE = re.compile(r"Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators")
MAX_FRAG_DIST = 20.0  # pt; fragments further than this from any anchor are suspicious

OFFICE_MAP = [
    (re.compile(r"^UNITED STATES SENATOR", re.I), ("U.S. Senate", "")),
    (re.compile(r"^GOVERNOR/?LIEUTENANT GOVERNOR", re.I), ("Governor", "")),
    (re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\S+)\s+CONGRESSIONAL DISTRICT", re.I), ("U.S. House", None)),
    (re.compile(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\S+)\s+DISTRICT", re.I), ("State House", None)),
]


def roman_or_int(token):
    m = re.match(r"^(\d+)", token)
    return m.group(1) if m else token


def split_office(office_raw):
    for pattern, (office, district) in OFFICE_MAP:
        m = pattern.match(office_raw)
        if m:
            if district is None:
                district = roman_or_int(m.group(1))
            return office, district
    return office_raw, ""


def title_case(name):
    """Title-case an ALL-CAPS candidate name, keeping single-letter initials upper."""
    out = name.title()
    out = re.sub(r"\b([A-Za-z])\b", lambda m: m.group(1).upper(), out)
    out = re.sub(r"\bGt\b", "GT", out)
    out = re.sub(r"\bDigiulio\b", "DiGiulio", out)
    out = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)
    return out


def page_lines(page):
    """Cluster a page's words into visual lines; returns [(top, [(x0, text)...])]."""
    words = page.extract_words()
    words.sort(key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in words:
        if lines and w["top"] - lines[-1][0] <= 4:
            lines[-1][1].append((w["x0"], w["text"]))
        else:
            lines.append([w["top"], [(w["x0"], w["text"])]])
    return [(top, sorted(ws, key=lambda p: p[0])) for top, ws in lines]


def is_num(t):
    return bool(NUM_RE.match(t))


def parse_pdf(pdf, warnings):
    """Yield standardized row dicts from the county PDF."""
    rows = []
    meta = []
    precinct = ""
    current_contest = None  # office raw text
    contest_anchors = []  # {page, top, party, name_part, nums}
    contest_frags = []  # {page, top, text}
    writeins = [0, 0, 0, 0]
    rv = None
    bc_total = None
    bc_breakdown = {}

    def close_contest():
        nonlocal current_contest, contest_anchors, contest_frags, writeins
        if current_contest is None:
            return
        office, district = split_office(current_contest)
        # assign each fragment to the nearest anchor on the same page
        unassigned = []
        for frag in contest_frags:
            cands = [
                a
                for a in contest_anchors
                if a["page"] == frag["page"] and a["party"] != "WRITE-IN"
            ]
            if not cands:
                unassigned.append(frag)
                continue
            best = min(cands, key=lambda a: abs(a["top"] - frag["top"]))
            if abs(best["top"] - frag["top"]) > MAX_FRAG_DIST:
                unassigned.append(frag)
                warnings.append(
                    "frag '%s' far from anchor (d=%.1f) in %s / %s"
                    % (frag["text"], abs(best["top"] - frag["top"]), precinct, office)
                )
                continue
            best.setdefault("frags", []).append(frag)
        for a in contest_anchors:
            if a["party"] == "WRITE-IN":
                writeins[0] += a["nums"][0]
                writeins[1] += a["nums"][1]
                writeins[2] += a["nums"][2]
                writeins[3] += a["nums"][3]
                continue
            pieces = sorted(a.get("frags", []), key=lambda f: f["top"])
            all_pieces = [(f["top"], f["text"]) for f in pieces]
            if a["name_part"]:
                all_pieces.append((a["top"], a["name_part"]))
            all_pieces.sort(key=lambda p: p[0])
            # join pieces; a piece ending in '-' (hyphen-wrapped surname) attaches
            # to the next piece without a space (e.g. 'BADGES-' + 'CANNING')
            name = ""
            for _, t in all_pieces:
                name = (name + t) if name.endswith("-") else (name + " " + t if name else t)
            if not name:
                warnings.append("anchor without name in %s / %s" % (precinct, office))
                name = ""
            rows.append(
                {
                    "county": "Armstrong",
                    "precinct": precinct,
                    "office": office,
                    "district": a["district"],
                    "party": a["party"],
                    "candidate": title_case(name),
                    "votes": a["nums"][3],
                    "election_day": a["nums"][0],
                    "early_voting": a["nums"][1],
                    "provisional": a["nums"][2],
                }
            )
        if any(writeins):
            rows.append(
                {
                    "county": "Armstrong",
                    "precinct": precinct,
                    "office": office,
                    "district": district,
                    "party": "",
                    "candidate": "Write Ins",
                    "votes": writeins[3],
                    "election_day": writeins[0],
                    "early_voting": writeins[1],
                    "provisional": writeins[2],
                }
            )
        for frag in unassigned:
            warnings.append("unassigned fragment '%s' dropped" % frag["text"])
        current_contest = None
        contest_anchors = []
        contest_frags = []
        writeins = [0, 0, 0, 0]

    def flush_meta():
        if precinct:
            if rv is not None:
                meta.append(
                    {
                        "county": "Armstrong",
                        "precinct": precinct,
                        "office": "Registered Voters",
                        "district": "",
                        "party": "",
                        "candidate": "",
                        "votes": rv,
                        "election_day": "",
                        "early_voting": "",
                        "provisional": "",
                    }
                )
            if bc_total is not None:
                meta.append(
                    {
                        "county": "Armstrong",
                        "precinct": precinct,
                        "office": "Ballots Cast",
                        "district": "",
                        "party": "",
                        "candidate": "",
                        "votes": bc_total,
                        "election_day": bc_breakdown.get("election_day", ""),
                        "early_voting": bc_breakdown.get("early_voting", ""),
                        "provisional": bc_breakdown.get("provisional", ""),
                    }
                )

    for page_no, page in enumerate(pdf.pages, start=1):
        lines = page_lines(page)
        i = 0
        n = len(lines)
        while i < n:
            top, toks = lines[i]
            texts = [t for _, t in toks]
            line_text = " ".join(texts)
            first = texts[0]

            m = SUMMARY_FOR_RE.search(line_text)
            if m:
                close_contest()
                flush_meta()
                precinct = m.group(1).strip()
                rv = None
                bc_total = None
                bc_breakdown = {}
                i += 1
                continue

            # contest header (may wrap)
            if "(Vote for" in line_text and first not in ("Times", "Total"):
                close_contest()
                header = line_text
                while not VOTE_FOR_RE.search(header) and i + 1 < n:
                    i += 1
                    header += " " + " ".join(t for _, t in lines[i][1])
                current_contest = re.sub(r"\s+", " ", VOTE_FOR_RE.sub("", header)).strip()
                office, district = split_office(current_contest)
                contest_anchors = []
                contest_frags = []
                writeins = [0, 0, 0, 0]
                i += 1
                continue

            # write-in rows
            if first == "Scattered" or first == "Unresolved":
                nums = [int(t.replace(",", "")) for t in texts if NUM_RE.match(t)][:4]
                if len(nums) == 4:
                    writeins[0] += nums[0]
                    writeins[1] += nums[1]
                    writeins[2] += nums[2]
                    writeins[3] += nums[3]
                i += 1
                continue

            # elector-group block (outside contests)
            if current_contest is None:
                if first == "Total" and len(texts) >= 3 and texts[1] == "Election":
                    bc_breakdown = {"election_day": int(texts[3].replace(",", ""))}
                    i += 1
                    continue
                if first in ("Mail", "Provisional") and len(texts) >= 3 and NUM_RE.match(texts[1]):
                    key = "early_voting" if first == "Mail" else "provisional"
                    bc_breakdown[key] = int(texts[1].replace(",", ""))
                    i += 1
                    continue
                if first == "Total" and len(texts) >= 5 and all(NUM_RE.match(t) or PCT_RE.match(t) for t in texts[1:]):
                    nums = [t for t in texts[1:] if NUM_RE.match(t)]
                    if len(nums) >= 3:
                        bc_total = int(nums[0].replace(",", ""))
                        rv = int(nums[2].replace(",", ""))
                    i += 1
                    continue
                if first == "Registered" and "Voters:" in line_text:
                    m2 = re.search(r"of\s+([\d,]+)", line_text)
                    if m2:
                        rv = int(m2.group(1).replace(",", ""))
                    i += 1
                    continue
                if first == "Ballots" and "Cast:" in line_text:
                    m2 = re.search(r"Cast:\s*([\d,]+)", line_text)
                    if m2:
                        bc_total = int(m2.group(1).replace(",", ""))
                    i += 1
                    continue

            # candidate anchor line: party token in party column + 4 counts
            anchor = None
            for idx, (x0, t) in enumerate(toks):
                if (t in PARTIES or t == "WRITE-IN") and PARTY_X[0] <= x0 <= PARTY_X[1]:
                    rest = [tt for _, tt in toks[idx + 1:]]
                    nums = [int(tt.replace(",", "")) for tt in rest if NUM_RE.match(tt)]
                    if len(nums) == 4:
                        name_part = " ".join(
                            tt for xx, tt in toks[:idx] if xx < PARTY_X[0] and tt != "WRITE-IN"
                        )
                        office, district = split_office(current_contest or "")
                        contest_anchors.append(
                            {
                                "page": page_no,
                                "top": top,
                                "party": t,
                                "name_part": name_part.strip(),
                                "nums": tuple(nums),
                                "district": district,
                            }
                        )
                        break
            else:
                # bare name-fragment line: all tokens left of the party column,
                # no digits, and not a known header/footer
                if (
                    all(x0 < PARTY_X[0] for x0, _ in toks)
                    and not any(NUM_RE.match(t) or PCT_RE.match(t) for _, t in toks)
                    and not line_text.startswith(
                        ("Page:", "Elector", "Candidate", "Summary", "Registered Voters:", "Ballots Cast:")
                    )
                    and "(Vote for" not in line_text
                ):
                    if current_contest is not None:
                        contest_frags.append({"page": page_no, "top": top, "text": line_text})
                    else:
                        warnings.append("stray fragment '%s' outside contest" % line_text)
            i += 1

    close_contest()
    flush_meta()
    return meta + rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_pdf")
    ap.add_argument("output_csv")
    ap.add_argument("--county", default="Armstrong")
    args = ap.parse_args(argv)

    if not os.path.exists(args.input_pdf):
        print("error: input PDF not found: %s" % args.input_pdf, file=sys.stderr)
        return 1

    import pdfplumber

    warnings = []
    with pdfplumber.open(args.input_pdf) as pdf:
        all_rows = parse_pdf(pdf, warnings)
    for w in warnings:
        print("warning: %s" % w, file=sys.stderr)

    out_dir = os.path.dirname(os.path.abspath(args.output_csv))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print("wrote {} rows to {}".format(len(all_rows), args.output_csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())