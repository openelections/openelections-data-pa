#!/usr/bin/env python3
"""Sullivan County, PA 2023 General — county-level results parser.

Source: "Sullivan County Official Results 2023 General.pdf" — a Dominion
"Statement of Votes Cast" report (13 pages, COUNTYWIDE totals only; Precinct:
All).  Recovered via PaddleOCR-VL; page 1's table body was dropped by the
200-dpi pass and recovered with a 300-dpi re-OCR of that page
(work2023/txt/Sullivan__ocr.txt = 200-dpi pages 1-13; the 300-dpi page 1 is
verified against the rendered page).  This parser consumes the OCR text.

Structure:
  * One flat Choice/Votes table across all pages: contest header lines end
    with "(Vote for N)"; candidate rows are "<NAME> <votes>"; the aggregate
    "Write-in <n>" row appears once per write-in slot (sum the slots);
    "[Write-in] <Name> <n>" itemize the write-ins; "[Write-in] Invalid" /
    "Duplicates" rows account for the rest.  The aggregate Write-in figure
    INCLUDES invalid write-ins (verified: Sheriff 22 named + 5 invalid = 27).
    Named write-ins are folded into a single "Write-ins" row; the parser
    checks named + Invalid + Duplicates == aggregate per contest.
  * Page breaks may split a contest; state carries across pages.  The
    200-dpi page 1 is replaced by the 300-dpi version (see REPLACEMENTS).
  * No Registered Voters / Ballots Cast totals exist in this source.
  * Retention questions report Yes/No rows; office names are canonicalized
    in work2023/assemble.py.

Usage: python parsers/pa_sullivan_general_2023_results_parser.py <ocr.txt> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Sullivan"

# The 200-dpi OCR dropped the body of page 1's table (verified against the
# rendered page and a 300-dpi re-OCR, which agree exactly).  Splice the
# 300-dpi rows in; the 200-dpi page 1 contributes nothing else.
PAGE1_300 = """\
JUSTICE OF THE SUPREME COURT (Vote for 1)
CAROLYN CARLUCCIO 1,331
DANIEL MCCAFFERY 545
Write-in 3
[Write-in] Invalid 3
JUDGE OF THE SUPERIOR COURT (Vote for 2)
MARIA BATTISTA 1,217
HARRY F. SMAIL JR. 1,030
JILL BECK 584
TIMIKA LANE 432
Write-in 1
Write-in 0
[Write-in] Invalid 1
JUDGE OF THE COMMONWEALTH COURT (Vote for 1)
MEGAN MARTIN 1,313
MATT WOLF 539
Write-in 1
[Write-in] JARED D. HOUCK 1
COUNTY SHERIFF (Vote for 1)
JARED D. HOUCK 1,696
Write-in 27
[Write-in] Bob Montgomery 6
[Write-in] GORAN LAZAREVIC 2
[Write-in] SCOTT FARRELL 2
[Write-in] Thomas Mumford 2
[Write-in] Adam Ritinksi 1
[Write-in] BILL STABRYLA 1
[Write-in] BURTON ADAMS 1
[Write-in] Dave Houseknecht 1
[Write-in] David M. Hink 1
"""

NAME_FIX = {
    "Harry F. Smail Jr.": "Harry F. Smail Jr.",
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


TOWNS = {
    "CHERRY": "Cherry Township", "COLLEY": "Colley Township",
    "DAVIDSON": "Davidson Township", "ELKLAND": "Elkland Township",
    "FORKS": "Forks Township", "FOX": "Fox Township",
    "HILLSGROVE": "Hillsgrove Township", "LAPORTE TWP": "Laporte Township",
    "SHREWSBURY": "Shrewsbury Township",
}
BOROS = {
    "DUSHORE BORO": "Dushore", "EAGLES MERE BORO": "Eagles Mere",
    "FORKSVILLE BORO": "Forksville", "FORKSVILLE": "Forksville",
    "LAPORTE BORO": "Laporte", "LAPORTE BOR": "Laporte",
}

STATEWIDE = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
}


def map_office(header: str) -> tuple[str, str]:
    """Contest header (Vote-for N stripped) -> (office, district)."""
    h = re.sub(r"\s+", " ", header).strip()
    if h.upper() in STATEWIDE:
        return STATEWIDE[h.upper()], ""
    if h.upper() == "COUNTY SHERIFF":
        return "County Sheriff", ""
    if h.upper() == "COUNTY TREASURER":
        return "County Treasurer", ""
    if h.upper() == "COUNTY COMMISSIONER":
        return "County Commissioner", ""
    if h.upper() == "COUNTY CORONER":
        return "County Coroner", ""
    if h.upper() == "COUNTY AUDITOR":
        return "County Auditor", ""
    if h.upper() == "PROTHONOTARY, REGISTER AND RECORDER, CLERK OF COURTS":
        return "Register, Recorder, Prothonotary, Clerk Of Courts", ""
    if h.upper() == "COUNTY SCHOOL DIRECTOR":
        return "School Director", "Sullivan County"
    m = re.match(r"SUPERIOR COURT RETENTION (PANELLA|STABILE)$", h.upper())
    if m:
        who = "Jack Panella" if m.group(1) == "PANELLA" else "Victor P. Stabile"
        return f"Superior Court Retention - {who}", ""
    # borough offices: "AUDITOR FORKSVILLE 6 YR" (try before township —
    # Forksville is a borough and is not in TOWNS)
    m = re.match(r"AUDITOR (FORKSVILLE)(?: BORO)?\s*(\d)?\s*Y?R?$", h, re.I)
    if m:
        suffix = f" ({m.group(2)} Year)" if m.group(2) else ""
        return f"Borough Auditor{suffix}", BOROS[m.group(1).upper()]
    # township offices: "SUPERVISOR CHERRY 6 YR", "AUDITOR DAVIDSON - 6YR"
    m = re.match(r"(SUPERVISOR|AUDITOR) (.+?)\s*(\d)\s*Y?R$", h, re.I)
    if m:
        kind, town, n = m.group(1).title(), m.group(2).strip(), m.group(3)
        town = re.sub(r"[-\s]+$", "", town)  # "DAVIDSON -" from "DAVIDSON - 6YR"
        office = f"Township {kind} ({n} Year)"
        return office, TOWNS[town.upper()]
    m = re.match(r"SUPERVISOR (.+)$", h, re.I)
    if m:
        return "Township Supervisor", TOWNS[m.group(1).strip().upper()]
    m = re.match(r"COUNCIL (.+)$", h, re.I)
    if m:
        rest = m.group(1).strip()
        term = ""
        mt = re.search(r"[-\s](\d)\s*Y?R$", rest, re.I)
        if mt:
            term = f" ({mt.group(1)} Year)"
            rest = rest[: mt.start()].strip()
            rest = re.sub(r"[-\s]+$", "", rest)  # "EAGLES MERE BORO -"
        return f"Borough Council{term}", BOROS[rest.upper()]
    raise ValueError(f"unmapped contest header: {header!r}")


CAND_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>[\d,]+)$")
HDR_RE = re.compile(r"^(?P<title>.+?)\s*\(Vote for (?P<n>\d+)\)$")


def parse(path: str):
    src = open(path, encoding="utf-8").read()
    # splice the 300-dpi page 1 in place of the (dropped-body) 200-dpi page 1
    pages = re.split(r"===== PAGE \d+ =====", src)
    body = "\n".join(pages[2:])  # skip preamble and 200-dpi page 1
    body = body.replace("Choice ___ Votes\n", "").replace("Choice Votes\n", "")
    body = re.sub(r"^#+ .*$", "", body, flags=re.M)  # trailing "## 1 to 381 of 381"
    lines = PAGE1_300.splitlines() + [l.rstrip() for l in body.splitlines()]

    rows: list[list] = []
    problems: list[str] = []
    office = district = None
    cands: list[tuple] = []          # (name, party, votes)
    wi_total = 0                      # aggregate of "Write-in N" slot rows
    wi_named = 0                      # sum of [Write-in] Name rows
    wi_invalid = 0                    # [Write-in] Invalid / Duplicates
    has_wi = False

    def flush():
        nonlocal wi_total, wi_named, wi_invalid, has_wi, cands
        if office is None:
            return
        for name, party, votes in cands:
            rows.append([COUNTY, office, district, party, name,
                         str(votes), "", "", ""])
        if has_wi:
            rows.append([COUNTY, office, district, "", "Write-ins",
                         str(wi_total), "", "", ""])
            expect = wi_named + wi_invalid
            if expect != wi_total:
                problems.append(
                    f"{office} ({district}): named {wi_named} + invalid/dup "
                    f"{wi_invalid} != Write-in aggregate {wi_total}")
        wi_total = wi_named = wi_invalid = has_wi = 0

    cands: list[tuple] = []
    for line in lines:
        s = re.sub(r"\s+", " ", line.strip())
        s = re.sub(r"_+", " ", s).strip()  # "Yes ___ 920" -> "Yes 920"
        s = re.sub(r"\s+", " ", s)
        if not s or s in ("Choice", "Votes", "Choice Votes"):
            continue
        m = HDR_RE.match(s)
        if m:
            flush()
            cands = []
            office, district = map_office(m.group("title"))
            continue
        m = re.match(r"^\[Write-in\] (?P<name>.+?)\s+(?P<votes>[\d,]+)$", s)
        if m:
            nm = m.group("name").strip()
            v = int(m.group("votes").replace(",", ""))
            if nm.lower() in ("invalid", "duplicates"):
                wi_invalid += v
            else:
                wi_named += v
            continue
        m = re.match(r"^Write-in\s+(?P<votes>[\d,]+)$", s)
        if m:
            has_wi = True
            wi_total += int(m.group("votes").replace(",", ""))
            continue
        m = CAND_RE.match(s)
        if m and office is not None:
            name = title_case(m.group("name").strip())
            name = NAME_FIX.get(name, name)
            v = int(m.group("votes").replace(",", ""))
            party = ""
            pm = re.search(r"\s*\((DEM|REP|IND|LIB|GRN)\)$", name)
            if pm:
                party, name = pm.group(1), name[: pm.start()].strip()
            cands.append((name, party, v))
            continue
        if office is not None:
            problems.append(f"unparsed line: {s!r}")
    flush()
    if problems:
        sys.stderr.write("SULLIVAN PROBLEMS:\n" + "\n".join(problems) + "\n")
    return rows


def main() -> None:
    inp, out = sys.argv[1], sys.argv[2]
    rows = parse(inp)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "office", "district", "party", "candidate",
                    "votes", "election_day", "mail", "provisional"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()