#!/usr/bin/env python3
"""Sullivan County, PA 2022 General — precinct-level results parser.

Source: "Sullivan PA OFFICIAL+Results+General+2022.pdf" — an "OFFICIAL
Election Results" crosstab (25 pages, but pages 3-25 are empty grid
templates; all data is on pages 1-2).  Layout:

  * Rotated (90-degree) column headers on page 1: 15 precinct columns +
    a horizontal TOTALS column.  Each rotated label is several words
    stacked vertically in one x-band ("Laporte" over "Borough"), read
    top-down (descending 'top').
  * Contests as ALL-CAPS header lines (some wrap onto two lines:
    "REPRESENTATIVE IN CONGRESS 9TH" / "DISTRICT").
  * Candidate rows: label left of the grid, one number per precinct cell
    (right-aligned, so the x1 edge identifies the column) plus TOTALS.
    Two Governor rows wrap their name AROUND the number block
    ("DOUGLAS MASTRIANO & CARRIE LEWIS" / numbers / "DELROSSO").
  * Write-ins are itemized by name under a "Write In:" marker (or a
    "Write-In - <Name>" lead row in the State House contest); they are
    aggregated into one "Write Ins" row per contest.
  * A final "EAGLES MERE BORO / Borough Council Referendum" section with
    Yes/No rows (Eagles Mere column only).
  * No Registered Voters / Ballots Cast rows; no election-day/mail/
    provisional breakdown -> 7-column output.

Parties are not printed in the source; they are filled from the
ENR-derived statewide county file (2022/20221108__pa__general__county.csv),
whose Sullivan totals match this PDF's TOTALS column exactly.

Usage: python parsers/pa_sullivan_general_2022_results_parser.py <pdf> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

import pdfplumber

COUNTY = "Sullivan"

NUM_RE = re.compile(r"^\d{1,3}(,\d{3})*$")

# contest header (joined across wrapped lines) -> (office, district)
CONTESTS = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR & LIEUTENANT GOVERNOR": ("Governor", ""),
    "REPRESENTATIVE IN CONGRESS 9TH DISTRICT": ("U.S. House", "9"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 84TH DISTRICT": ("State House", "84"),
}
# headers that continue onto a second line -> full header once joined
CONTEST_PREFIXES = {
    "REPRESENTATIVE IN CONGRESS 9TH",
    "REPRESENTATIVE IN THE GENERAL",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 84TH",
}
SECTION_HEADERS = {"EAGLES MERE BORO"}

NAME_FIX = {
    "Douglas Mastriano & Carrie Lewis Delrosso":
        "Douglas Mastriano & Carrie Lewis DelRosso",
    "Christina Digiulio & Michael Bagdes- Canning":
        "Christina DiGiulio & Michael Bagdes-Canning",
    "Christina Digiulio & Michael Bagdes-Canning":
        "Christina DiGiulio & Michael Bagdes-Canning",
}

# from 2022/20221108__pa__general__county.csv (ENR) — Sullivan totals match
PARTY = {
    "John Fetterman": "DEM",
    "Mehmet Oz": "REP",
    "Erik Gerhardt": "LIB",
    "Richard L. Weiss": "GRN",
    "Daniel Wassmer": "KEY",
    "Josh Shapiro & Austin Davis": "DEM",
    "Douglas Mastriano & Carrie Lewis DelRosso": "REP",
    "Matt Hackenburg & Tim McMaster": "LIB",
    "Christina DiGiulio & Michael Bagdes-Canning": "GRN",
    "Joe Soloski & Nichole Shultz": "KEY",
    "Amanda R. Waldman": "DEM",
    "Dan Meuser": "REP",
    "Joe Hamm": "REP",
}


def title_case(text: str) -> str:
    """ALL CAPS -> Title Case, handling initials, hyphens, apostrophes, Mc/O'."""
    def cap_word(w: str) -> str:
        lw = w.lower()
        if lw.startswith("mc") and len(lw) > 2 and lw[2].isalpha():
            return "Mc" + lw[2].upper() + lw[3:]
        if lw.startswith("o'") and len(lw) > 2:
            return "O'" + lw[2].upper() + lw[3:]
        out = []
        cap = True
        for ch in w:
            if ch.isalpha():
                out.append(ch.upper() if cap else ch.lower())
                cap = False
            else:
                out.append(ch.lower())
                if ch in ".-'":
                    cap = True
        return "".join(out)
    return " ".join(cap_word(w) for w in text.split())


def build_columns(page):
    """(precinct_names[15], col_centers[16]) from page 1."""
    # rotated header: non-upright chars, grouped by x band, read top-down
    bands: dict[int, list] = {}
    for c in page.chars:
        if not c["upright"]:
            bands.setdefault(round(c["x0"]), []).append(c)
    names = []
    for x in sorted(bands):
        cs = sorted(bands[x], key=lambda c: -c["top"])  # rotated: top-down
        words: list[str] = []
        cur = cs[0]["text"]
        prev_top = cs[0]["top"]
        for c in cs[1:]:
            if prev_top - c["top"] > 8:
                words.append(cur)
                cur = c["text"]
            else:
                cur += c["text"]
            prev_top = c["top"]
        words.append(cur)
        names.append(" ".join(words))
    if len(names) != 16 or names[-1].replace(" ", "") != "TOTALS":
        raise ValueError(f"unexpected header columns: {names}")
    names = names[:15]

    # numeric cells are right-aligned: cluster body numbers by x1
    xs = sorted(w["x1"] for w in page.extract_words()
                if NUM_RE.match(w["text"]) and w["top"] >= 138)
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < 3:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    nums = [sum(c) / len(c) for c in clusters]
    if len(nums) != 16:
        raise ValueError(f"expected 16 numeric columns, got {len(nums)}")
    return names, nums


def body_lines(page, y_min=125.0):
    """Group body words into visual lines (top within 3pt)."""
    ws = [w for w in page.extract_words() if w["top"] >= y_min]
    ws.sort(key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in ws:
        if lines and abs(w["top"] - lines[-1][-1]["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
        yield ln


def parse(pdf_path: str):
    rows: list[list] = []
    problems: list[str] = []

    pdf = pdfplumber.open(pdf_path)
    precincts, centers = build_columns(pdf.pages[0])
    grid_left = min(  # label area is left of the first numeric cell
        w["x0"] for w in pdf.pages[0].extract_words()
        if NUM_RE.match(w["text"]) and w["top"] >= 138) - 4

    office = district = None
    section = ""                    # e.g. "EAGLES MERE BORO"
    writein_mode = False
    pending_parts: list[str] = []   # caps name lines awaiting numbers
    held = None                     # (name, votes) awaiting possible suffix
    cands: list[tuple] = []         # (name, votes-by-col) regular rows
    wins: list[tuple] = []          # write-in rows

    def flush_held():
        nonlocal held
        if held is not None:
            cands.append(held)
            held = None

    def flush_contest():
        nonlocal cands, wins, writein_mode
        flush_held()
        for name, bycol in cands:
            total = bycol.get(15)
            s = sum(v for k, v in bycol.items() if k < 15)
            if total is not None and total != s:
                problems.append(
                    f"{office}: {name} precinct sum {s} != TOTALS {total}")
            name = NAME_FIX.get(title_case(name), title_case(name))
            party = PARTY.get(name, "")
            for i in range(15):
                # emit every cell the source prints — including explicit 0s;
                # cells the source leaves blank (e.g. the referendum outside
                # Eagles Mere) are omitted
                if i in bycol:
                    rows.append([COUNTY, precincts[i], office, district,
                                 party, name, str(bycol[i])])
        if wins:
            agg: dict[int, int] = {}
            for _name, bycol in wins:
                for k, v in bycol.items():
                    agg[k] = agg.get(k, 0) + v
            for i in range(15):
                if agg.get(i):
                    rows.append([COUNTY, precincts[i], office, district, "",
                                 "Write Ins", str(agg[i])])
        cands, wins = [], []
        writein_mode = False

    for page in pdf.pages:
        for ln in body_lines(page):
            text_ws = [w for w in ln if not NUM_RE.match(w["text"])
                       and w["x1"] < grid_left + 10]
            num_ws = [w for w in ln if NUM_RE.match(w["text"])]
            label = " ".join(w["text"] for w in text_ws).strip()
            bycol: dict[int, int] = {}
            for w in num_ws:
                c = min(range(16),
                        key=lambda i: abs(w["x1"] - centers[i]))
                bycol[c] = bycol.get(c, 0) + int(w["text"].replace(",", ""))

            # ---- numbers-only lines -------------------------------------
            if num_ws and not text_ws:
                if not pending_parts:
                    problems.append(
                        f"numbers-only line with no pending name: {label!r}")
                    continue
                name = " ".join(pending_parts)
                pending_parts = []
                rec = (name, bycol)
                if writein_mode:
                    wins.append(rec)
                else:
                    flush_held()
                    held = rec
                continue

            # ---- text-only lines -----------------------------------------
            if not num_ws:
                low = label.lower()
                if re.fullmatch(r"write\s?in:?", low):
                    flush_held()
                    writein_mode = True
                    continue
                if low.startswith("write-in") or low.startswith("write in -"):
                    flush_held()
                    writein_mode = True
                    rest = re.sub(r"^write-?\s?in\s*[-–]?\s*", "", label,
                                  flags=re.I)
                    if bycol:                      # "Write-In - Name  2  2"
                        wins.append((rest, bycol))
                    else:
                        pending_parts = [rest] if rest else []
                    continue
                if label.isupper():
                    joined = " ".join(pending_parts + [label])
                    if joined in CONTESTS or joined in CONTEST_PREFIXES:
                        pending_parts.append(label)
                        if joined in CONTESTS:
                            flush_contest()
                            office, district = CONTESTS[joined]
                            pending_parts = []
                            section = ""
                        continue
                    if joined in SECTION_HEADERS:
                        flush_contest()
                        pending_parts = []
                        section = label
                        office = district = None
                        continue
                    # not a header: a (possibly wrapped) candidate name part
                    pending_parts.append(label)
                    continue
                # mixed-case text-only line: the referendum title
                if section and "referendum" in low:
                    flush_contest()
                    sec = re.sub(r"\s+BORO$", "", section, flags=re.I)
                    office = f"{title_case(sec)} {label}"
                    district = ""
                    section = ""
                    continue
                if label:
                    problems.append(f"unhandled text line: {label!r}")
                continue

            # ---- lines with both label and numbers -----------------------
            low = label.lower()
            if low.startswith("write-in") or low.startswith("write in -"):
                flush_held()
                writein_mode = True
                rest = re.sub(r"^write-?\s?in\s*[-–]?\s*", "", label, flags=re.I)
                wins.append((rest, bycol))
                continue
            if pending_parts:
                # pending caps lines complete the held candidate's name
                # (name wrapped around the number block)
                if held is not None:
                    name = (held[0] + " " + " ".join(pending_parts)).strip()
                    held = (name, held[1])
                pending_parts = []
            rec = (label, bycol)
            if writein_mode:
                wins.append(rec)
            elif office is None:
                problems.append(f"row before any contest: {label!r}")
            else:
                flush_held()
                held = rec

    flush_contest()
    return rows, problems


def main() -> None:
    pdf, out = sys.argv[1], sys.argv[2]
    rows, problems = parse(pdf)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")
    if problems:
        sys.stderr.write("SULLIVAN PROBLEMS:\n" + "\n".join(problems) + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()