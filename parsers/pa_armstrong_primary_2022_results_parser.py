#!/usr/bin/env python3
"""
Parser for Armstrong County 2022 General Primary (May 17, 2022) precinct results.

Input: "Armstrong PA Precinct.pdf" / "Armstrong PA 2022 Primary Precinct.pdf"
(content-identical Electionware exports dated 2022-07-13, after the June
U.S. Senate recount): 62 per-precinct "Election Summary Report" (Closed
Primary) reports, one section per precinct, each printing every contest with
its ballot-party sub-contests (US Senate, Governor, Lieutenant Governor,
U.S. House 15, State House 60/63, Member of Democratic/Republican State
Committee, Committeeperson) plus per-precinct Registered Voters / Ballots Cast
elector-group metadata.

Candidate rows carry a single "Total" vote count only (no Election Day /
Mail / Provisional breakdown per candidate), so contest rows use the 7-column
schema
    county,precinct,office,district,party,candidate,votes
with party = the ballot party of the contest (write-in rows carry the
contest's party too). "Registered Voters" and "Ballots Cast" rows are emitted
per precinct (candidate=null, party empty). Where the source's elector-group
table gives ballot-count breakdowns (Election Day / Mail / Provisional), they
are written on the Ballots Cast row only.

Written as a standalone adaptation of
pa_armstrong_general_2022_results_parser.py (same Electionware
Election-Summary-Report source family, pdfplumber line tokenizer). Ballot-page
report headers that can interrupt the previous contest are closed when
detected. Every contest's rows are validated against the printed
"Total Votes" line (ballot candidates + Scattered/Scattered1 + Unresolved
Write-In), with mismatches reported as warnings.

Usage:
    python parsers/pa_armstrong_primary_2022_results_parser.py <pdf> <output_csv>

Notes:
- Recount: the GOP U.S. Senate race was recounted statewide in June 2022. All
  Armstrong source files are the same two 2022-07-13 exports (after the
  recount, titled "Official Results"); the 6-candidate named residual vs the
  ENR anchor is +6 votes (McCormick +3/Barnette +1/Sands +1/Gale +1), same
  size and direction as the DEM-side residuals — ENR-feed late-canvas, kept
  as the county's own official values.
- Precinct names are normalized from Electionware abbreviations ("Boro",
  "Twp", "Wd") to the spellings of the repo's 2022 general file (exact 62/62
  set match); unmapped names are kept and warned.
- All-zero contest sections (13 Committeeperson sections) produce no rows,
  matching the 2022-general convention. "Gilpin Township-#2"'s source report
  is truncated after its DEM Committeeperson section; no REP Committeeperson
  section exists in the source and none is invented.
- ENR stamps district "<county code 3>" on State Committee rows; ours is
  blank (Lebanon/Adams/Centre precedent, the source title has none).
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
    "mail",
    "provisional",
]

NUM_RE = re.compile(r"^\d+$")
PCT_RE = re.compile(r"^\d+(\.\d+)?%$")
VOTE_FOR_SPLIT_RE = re.compile(r"\(\s*Vote\s+for\s+\d+\s*\)")
SUMMARY_FOR_RE = re.compile(
    r"Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators"
)
PARTY_TOKENS = {"DEM", "REP", "WRITE-IN"}
MAX_FRAG_DIST = 20.0

OFFICE_MAP = [
    (re.compile(r"^UNITED STATES SENATOR", re.I), ("U.S. Senate", "")),
    (re.compile(r"^GOVERNOR$", re.I), ("Governor", "")),
    (re.compile(r"^LIEUTENANT GOVERNOR", re.I), ("Lieutenant Governor", "")),
    (
        re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\S+)\s+CONGRESSIONAL DISTRICT", re.I),
        ("U.S. House", None),
    ),
    (
        re.compile(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\S+)\s+DISTRICT", re.I),
        ("State House", None),
    ),
    (
        re.compile(r"^MEMBER OF DEMOCRATIC STATE COMMITTEE", re.I),
        ("Member of Democratic State Committee", ""),
    ),
    (
        re.compile(r"^MEMBER OF REPUBLICAN STATE COMMITTEE", re.I),
        ("Member of Republican State Committee", ""),
    ),
    (re.compile(r"^COMMITTEEPERSON", re.I), ("Committeeperson", "")),
]

# Electionware report chrome that can appear after one precinct's last contest
# and before the next precinct's "Summary for:" line.
REPORT_HEAD_RE = re.compile(
    r"^(Election Summary Report|Closed Primary|Armstrong County|"
    r"May 17, 2022|2022 General Primary Official Results|Elector Group)\b",
    re.I,
)


def roman_or_int(token):
    m = re.match(r"^(\d+)", token)
    return m.group(1) if m else token


def split_office(office_raw):
    office_raw = re.sub(r"\s+", " ", office_raw).strip()
    for pattern, (office, district) in OFFICE_MAP:
        m = pattern.match(office_raw)
        if m:
            if district is None:
                district = roman_or_int(m.group(1))
            return office, district
    return office_raw, ""


# Electionware prints abbreviated municipality names ("Boro"/"Twp"/"Wd");
# normalize to the spellings used by the Armstrong rows of the repo's
# 2022 general statewide file (exact 1:1 set match, 62/62).
PRECINCT_NAME_MAP = {
    "Apollo Boro": "Apollo Borough",
    "Applewold Boro": "Applewold Borough",
    "Atwood Boro": "Atwood Borough",
    "Bethel Twp": "Bethel Township",
    "Boggs Twp": "Boggs Township",
    "Bradys Bend Twp": "Bradys Bend Township",
    "Burrell Twp": "Burrell Township",
    "Cadogan Twp": "Cadogan Township",
    "Cowanshannock Twp-East": "Cowanshannock Township-East",
    "Cowanshannock Twp-West": "Cowanshannock Township-West",
    "Dayton Boro": "Dayton Borough",
    "East Franklin Twp-East": "East Franklin Township-East",
    "East Franklin Twp-North": "East Franklin Township-North",
    "East Franklin Twp-West": "East Franklin Township-West",
    "Elderton Boro": "Elderton Borough",
    "Ford City Boro-1st Wd": "Ford City Borough-1st Ward",
    "Ford City Boro-2nd Wd": "Ford City Borough-2nd Ward",
    "Ford Cliff Boro": "Ford Cliff Borough",
    "Freeport Boro": "Freeport Borough",
    "Gilpin Twp-#1": "Gilpin Township-#1",
    "Gilpin Twp-#2": "Gilpin Township-#2",
    "Hovey Twp": "Hovey Township",
    "Kiski Twp-Horrell": "Kiski Township-Horrell",
    "Kiski Twp-Orchard Hills North": "Kiski Township-Orchard Hills North",
    "Kiski Twp-Orchard Hills South": "Kiski Township-Orchard Hills South",
    "Kiski Twp-Spring Church": "Kiski Township-Spring Church",
    "Kittanning Boro-1st Wd": "Kittanning Borough-1st Ward",
    "Kittanning Boro-2nd Wd": "Kittanning Borough-2nd Ward",
    "Kittanning Boro-3rd Wd": "Kittanning Borough-3rd Ward",
    "Kittanning Boro-4th Wd": "Kittanning Borough-4th Ward",
    "Kittanning Twp": "Kittanning Township",
    "Leechburg Boro": "Leechburg Borough",
    "Madison Twp": "Madison Township",
    "Mahoning Twp": "Mahoning Township",
    "Manor Twp-North": "Manor Township-North",
    "Manor Twp-South #1": "Manor Township-South #1",
    "Manor Twp-South #2": "Manor Township-South #2",
    "Manorville Boro": "Manorville Borough",
    "North Apollo Boro": "North Apollo Borough",
    "North Buffalo Twp-East": "North Buffalo Township-East",
    "North Buffalo Twp-West": "North Buffalo Township-West",
    "Parker City-1st Wd": "Parker City-1st Ward",
    "Parker City-2nd Wd": "Parker City-2nd Ward",
    "Parks Twp-Kepple Hill": "Parks Township-Kepple Hill",
    "Parks Twp-West": "Parks Township-West",
    "Perry Twp": "Perry Township",
    "Pine Twp": "Pine Township",
    "Plumcreek Twp": "Plumcreek Township",
    "Rayburn Twp": "Rayburn Township",
    "Redbank Twp": "Redbank Township",
    "Rural Valley Boro": "Rural Valley Borough",
    "South Bend Twp": "South Bend Township",
    "South Bethlehem Boro": "South Bethlehem Borough",
    "South Buffalo Twp-East": "South Buffalo Township-East",
    "South Buffalo Twp-West": "South Buffalo Township-West",
    "Sugarcreek Twp": "Sugarcreek Township",
    "Valley Twp": "Valley Township",
    "Washington Twp": "Washington Township",
    "Wayne Twp": "Wayne Township",
    "West Franklin Twp": "West Franklin Township",
    "West Kittanning Boro": "West Kittanning Borough",
    "Worthington Boro": "Worthington Borough",
}


def title_case(name):
    """Title-case an ALL-CAPS candidate name, keeping single-letter initials upper."""
    out = name.title()
    out = re.sub(r"\b([A-Za-z])\b", lambda m: m.group(1).upper(), out)
    out = re.sub(r"\bGt\b", "GT", out)
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


def parse_pdf(pdf, warnings):
    """Return standardized row dicts (metadata first, then contest rows)."""
    rows = []
    meta = []

    precinct = ""
    contest_raw = None  # office text without (DEM)/(REP)/(Vote for N)
    contest_party = ""
    contest_anchors = []  # {page, top, name, votes}
    contest_frags = []  # wrapped name fragments
    total_printed = None
    scattered = {}  # label ("Scattered"/"Scattered1") -> count
    unresolved = 0
    rv = None
    bc = None
    bc_ed = None
    bc_mail = None
    bc_prov = None
    header_buffer = None

    def close_contest():
        nonlocal contest_raw, contest_anchors, contest_frags, total_printed
        nonlocal scattered, unresolved
        if contest_raw is None:
            return
        office, district = split_office(contest_raw)
        # attach any bare name-fragment lines to the vertically nearest anchor,
        # ordering the pieces by top so a wrapped first line comes first
        unassigned = []
        for frag in contest_frags:
            if not contest_anchors:
                unassigned.append(frag)
                continue
            best = min(contest_anchors, key=lambda a: abs(a["top"] - frag["top"]))
            if abs(best["top"] - frag["top"]) > MAX_FRAG_DIST:
                unassigned.append(frag)
                warnings.append(
                    "fragment '%s' far from anchor (d=%.1f) in %s / %s"
                    % (frag["text"], abs(best["top"] - frag["top"]), precinct, office)
                )
                continue
            best.setdefault("frags", []).append((frag["top"], frag["text"]))
        wi_total = sum(scattered.values()) + unresolved
        computed = sum(a["votes"] for a in contest_anchors) + wi_total
        if unassigned:
            warnings.append(
                "unassigned fragment(s) %s in %s / %s"
                % ([f["text"] for f in unassigned], precinct, office)
            )
        for a in contest_anchors:
            pieces = sorted(
                [(a["top"], a["name"])] + a.get("frags", []), key=lambda p: p[0]
            )
            name = " ".join(t for _, t in pieces)
            rows.append(
                {
                    "county": "Armstrong",
                    "precinct": precinct,
                    "office": office,
                    "district": a["district"],
                    "party": contest_party,
                    "candidate": title_case(re.sub(r"\s+", " ", name).strip()),
                    "votes": a["votes"],
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                }
            )
        if wi_total:
            rows.append(
                {
                    "county": "Armstrong",
                    "precinct": precinct,
                    "office": office,
                    "district": district,
                    "party": contest_party,
                    "candidate": "Write Ins",
                    "votes": wi_total,
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                }
            )
        if total_printed is not None and computed != total_printed:
            warnings.append(
                "%s / %s (%s): row sum %s != printed Total Votes %s"
                % (precinct, office, contest_party, computed, total_printed)
            )
        contest_raw = None
        contest_anchors = []
        contest_frags = []
        total_printed = None
        scattered = {}
        unresolved = 0

    def flush_meta():
        nonlocal rv, bc, bc_ed, bc_mail, bc_prov
        if not precinct:
            return
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
                    "mail": "",
                    "provisional": "",
                }
            )
        if bc is not None:
            breakdown = {
                "election_day": bc_ed,
                "mail": bc_mail,
                "provisional": bc_prov,
            }
            if None in breakdown.values():
                breakdown = {k: v for k, v in breakdown.items() if v is not None}
            if breakdown and sum(breakdown.values()) != bc:
                warnings.append(
                    "%s: BC counting groups %s != Ballots Cast %s"
                    % (precinct, breakdown, bc)
                )
            meta.append(
                {
                    "county": "Armstrong",
                    "precinct": precinct,
                    "office": "Ballots Cast",
                    "district": "",
                    "party": "",
                    "candidate": "",
                    "votes": bc,
                    "election_day": "" if bc_ed is None else bc_ed,
                    "mail": "" if bc_mail is None else bc_mail,
                    "provisional": "" if bc_prov is None else bc_prov,
                }
            )
        rv = None
        bc = None
        bc_ed = None
        bc_mail = None
        bc_prov = None

    def new_section(name):
        nonlocal precinct, rv, bc, bc_ed, bc_mail, bc_prov
        close_contest()
        flush_meta()
        if name not in PRECINCT_NAME_MAP:
            warnings.append("unmapped precinct name kept as printed: %r" % name)
        precinct = PRECINCT_NAME_MAP.get(name, name)
        rv = None
        bc = None
        bc_ed = None
        bc_mail = None
        bc_prov = None

    def start_contest(header_text):
        nonlocal contest_raw, contest_party, contest_anchors, contest_frags
        nonlocal total_printed, scattered, unresolved
        close_contest()
        m_party = re.search(r"\((DEM|REP)\)", header_text)
        contest_party = m_party.group(1) if m_party else ""
        h = re.sub(r"\((?:DEM|REP)\)", " ", header_text)
        h = VOTE_FOR_SPLIT_RE.sub(" ", h)
        contest_raw = re.sub(r"\s+", " ", h).strip()
        contest_anchors = []
        contest_frags = []
        total_printed = None
        scattered = {}
        unresolved = 0

    for page_no, page in enumerate(pdf.pages, start=1):
        lines = page_lines(page)
        i = 0
        n = len(lines)
        while i < n:
            top, toks = lines[i]
            texts = [t for _, t in toks]
            line_text = " ".join(texts)

            if header_buffer is not None and re.match(r"^Page:\s*\d+ of \d+$", line_text.strip()):
                i += 1
                continue  # page footer/header never belongs to a contest title

            if REPORT_HEAD_RE.match(line_text.strip()):
                # report chrome: close any contest carried over
                close_contest()
                header_buffer = None
                i += 1
                continue

            m = SUMMARY_FOR_RE.search(line_text)
            if m:
                new_section(m.group(1).strip())
                i += 1
                continue

            low = line_text.lower()

            if low.startswith("registered voters:"):
                # "Registered Voters: <ballots> of <registered> (pct%)" — the
                # electionware convention puts ballots cast BEFORE "of"
                m2 = re.search(r"Registered Voters:\s*[\d,]+\s+of\s+([\d,]+)", line_text)
                if m2:
                    rv = int(m2.group(1).replace(",", ""))
                i += 1
                continue
            if low.startswith("ballots cast:"):
                m2 = re.search(r"Ballots Cast:\s*([\d,]+)", line_text)
                if m2:
                    bc = int(m2.group(1).replace(",", ""))
                i += 1
                continue
            if contest_raw is None:
                # elector-group counting table lines (before any contest):
                # "Total Election Day 187 187 22.21%", "Mail 48 48 5.70%",
                # "Provisional 0 0 0.00%"
                if texts[0] == "Total" and len(texts) >= 4 and " ".join(texts[1:3]) == "Election Day":
                    bc_ed = int(texts[3].replace(",", ""))
                    i += 1
                    continue
                if texts[0] == "Mail" and len(texts) >= 2 and NUM_RE.match(texts[1]):
                    bc_mail = int(texts[1].replace(",", ""))
                    i += 1
                    continue
                if texts[0] == "Provisional" and len(texts) >= 2 and NUM_RE.match(texts[1]):
                    bc_prov = int(texts[1].replace(",", ""))
                    i += 1
                    continue

            if header_buffer is not None:
                # a contest header is being accumulated until "(Vote for N)"
                if VOTE_FOR_SPLIT_RE.search(header_buffer + " " + line_text):
                    header_buffer += " " + line_text
                    start_contest(header_buffer)
                    header_buffer = None
                else:
                    header_buffer += " " + line_text
                i += 1
                continue

            if re.search(r"\((?:DEM|REP)\)", line_text):
                # ballot-party contest header (may wrap across lines)
                if VOTE_FOR_SPLIT_RE.search(line_text):
                    start_contest(line_text)
                else:
                    header_buffer = line_text
                i += 1
                continue

            first = texts[0]

            if line_text.strip() in ("DEM", "REP", "Total"):
                i += 1
                continue
            if first == "Candidate":
                i += 1
                continue
            if first == "Times":
                i += 1
                continue
            if first == "Total":
                m2 = re.search(r"Total\s+Votes\s+([\d,]+)", line_text)
                if m2:
                    total_printed = int(m2.group(1).replace(",", ""))
                    i += 1
                    continue
            if first.startswith("Scattered"):
                nums = [t for t in texts if NUM_RE.match(t)]
                scattered[first] = int(nums[0].replace(",", "")) if nums else 0
                i += 1
                continue
            if first == "Unresolved":
                nums = [t for t in texts if NUM_RE.match(t)]
                unresolved = int(nums[0].replace(",", "")) if nums else 0
                i += 1
                continue

            # candidate rows: trailing integer preceded by a party token
            if contest_raw is not None and len(texts) >= 3 and NUM_RE.match(texts[-1]):
                party_m = None
                for idx in range(len(texts) - 2, 0, -1):
                    if texts[idx] in PARTY_TOKENS:
                        party_m = idx
                        break
                if party_m and not any(NUM_RE.match(t) for t in texts[:party_m]):
                    name = " ".join(texts[:party_m])
                    printed_party = texts[party_m]
                    if printed_party not in PARTY_TOKENS:
                        warnings.append(
                            "odd party token '%s' in %s / %s"
                            % (printed_party, precinct, contest_raw)
                        )
                    contest_anchors.append(
                        {
                            "page": page_no,
                            "top": top,
                            "name": name,
                            "votes": int(texts[-1].replace(",", "")),
                            "district": split_office(contest_raw)[1],
                        }
                    )
                    i += 1
                    continue
                warnings.append(
                    "unexpected numeric row skipped: '%s' (%s / %s)"
                    % (line_text, precinct, contest_raw)
                )
                i += 1
                continue

            # bare name-fragment line (possibly a wrapped candidate name)
            if (
                contest_raw is not None
                and not any(NUM_RE.match(t) or PCT_RE.match(t) for _, t in toks)
                and not low.startswith(("page:", "candidate", "registered voters:", "ballots cast:"))
                and "(vote for" not in low
            ):
                contest_frags.append({"page": page_no, "top": top, "text": line_text})
                i += 1
                continue

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