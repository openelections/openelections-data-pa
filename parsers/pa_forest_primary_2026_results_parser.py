#!/usr/bin/env python3
"""Forest County, PA 2026 Primary (May 19) — precinct results parser.

Source (primary): "Forest County 2026 Primary Final Results by Precinct -
Board of Elections 5.22.26.xlsx" — the county Board of Elections' compiled
per-precinct workbook (finalized May 22, 2026), two sheets (Democratic,
Republican) of contest-then-candidate tables: name cell + 9 precinct vote
cells + a printed Totals column.  Precinct columns in source order:
Barnett, Green, Harmony, Hickory, Howe, Jenks, Kingsley, Tionesta (Township),
Borough (= Tionesta Borough).  This is the hand-transcription family (image-
transcription lineage in 2022/2024): no Registered Voters/Ballots Cast rows
and no vote-type breakdowns appear anywhere, so output uses the plain
7-column header.

Contests covered (both sheets): Governor, Lieutenant Governor,
Representative in Congress 15th District, Representative in the General
Assembly 65th District, Member of the Democratic/Republican State Committee,
and one Democratic/Republican Committee Member race per municipality
("Barnett Democratic Committee Member (One Male & One Female)" ->
"Democratic Committee Member Barnett", Adams 2026-primary naming).  Nothing
is omitted: even the Howe/Kingsley committee races that print no candidate
rows keep their SCATTERED row.

Write-ins: the source prints a single per-precinct "SCATTERED" row at the
bottom of every table; emitted as candidate "Scattered" carrying the
contest's ballot party (2026-primary convention, Armstrong/Luzerne precedent;
the 2024 Forest parser used "Write Ins" but the county's committed 2026
family files keep the vendor label).

KNOWN SOURCE ERRORS, both verified:
1. RICHEY / Green Township cell: the workbook prints 27 and a matching row
   Total of 263 (row total is consistent with the cell, so the error is in
   the cell).  The PA DOS certified precinct returns export
   ("Forest County DOS Official Precinct Returns 2026 Primary.csv") and the
   statewide anchor 2026/20260519__pa__primary__county.csv both give 24
   countywide 260.  Internal evidence agrees: Green's other REP contest tallies
   are Governor 41 + Scattered 0, U.S. House 41 + Scattered 0, State House 40
   + Scattered 0, so a Lieutenant Governor total of 17+24=41 fits while
   17+27=44 would top every other contest.  CORRECTIONS pins the DOS value 24
   (documented certified-source fill; totals recomputed after correction).
2. Committee races ("State Committee" and the local Committee Member races)
   print a Totals column that frequently disagrees with the row's own
   precinct cells (mostly printed 0 while the cells are non-zero, e.g.
   Republican State Committee Scattered cells sum to 40 with a printed 0).
   The precinct cells are taken as printed and the countywide (Totals)
   column is recomputed as the sum of the precinct cells for the __county.csv;
   the divergences are listed by the parser's --check run.  Statewide
   candidate rows DO tie (all 9 named rows sum to their printed Totals), and
   those rows are asserted.

DOS use: cross-check only for the four non-committee offices (all 36
precinct cells compared; the Richey/Green cell above is the only value the
parser corrects to the certified DOS value).  No contests were taken from
DOS alone; the county workbook covers everything the DOS slice has plus
write-ins, State Committee and the local committee races.

Ordering: contest-major (candidate's 9 precinct rows together), matching the
county's committed 2022/2024 primary files, with contests in source order
(DEM ballot then REP ballot per table).

Usage: python parsers/pa_forest_primary_2026_results_parser.py <xlsx> \
    <out_precinct_csv> <out_county_csv> [--dos <dos_csv>]
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import openpyxl

COUNTY = "Forest"
ELECTION_DATE = "20260519"

# PDF/xlsx column headers -> precinct names (Forest County roster, 9 precincts),
# per the county's committed 2022/2024 primary files.
PRECS = [
    "Barnett Township",
    "Green Township",
    "Harmony Township",
    "Hickory Township",
    "Howe Township",
    "Jenks Township",
    "Kingsley Township",
    "Tionesta Township",
    "Tionesta Borough",
]

# office header (whitespace-normalized, uppercased) -> (office, district)
OFFICE_MAP = {
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "REPRESENTATIVE IN CONGRESS 15TH DISTRICT": ("U.S. House", "15"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 65TH DISTRICT": ("State House", "65"),
}

COMMITTEE_RE = re.compile(
    r"^(Barnett|Green|Harmony|Hickory|Howe|Jenks|Kingsley|Tionesta Township|"
    r"Tionesta Borough)\s+(Democratic|Republican)\s+Committee Member",
    re.IGNORECASE)

NAME_FIX = {
    # source prints the pair "GT" inside the surname; keep the pair, styled as
    # in the 2026 CD-15 files (Cameron et al.)
    "Glenn Gt Thompson": "Glenn GT Thompson",
}

# Certified-value corrections (see docstring item 1): cell values the
# workbook prints wrong, pinned to the DOS certified export / anchor.
# (office, party, candidate-title-cased, precinct) -> correct votes
CORRECTIONS = {
    ("Lieutenant Governor", "REP", "Jason Richey", "Green Township"): 24,
}

SKIP_PREFIXES = (
    "Forest County, Pennsylvania",
    "2026 Primary",
    "Final results were compiled",
)


def title_case(name: str) -> str:
    out = []
    for i, w in enumerate(name.split()):
        if re.match(r"^[IVX]+$", w.upper()):
            out.append(w.upper())
        elif i and w.lower() in {"of", "the", "and", "for"}:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    s = " ".join(out)
    s = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), s)
    s = re.sub(r"(?<=-)([a-z])", lambda m: m.group(1).upper(), s)
    return NAME_FIX.get(s, s)


def clean_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"\s*\((?:D|R)\)\s*$", "", name).strip()
    if name.upper() == "SCATTERED":
        return "Scattered"
    norm = " ".join(name.split())
    return title_case(norm)


def norm_key(text: str) -> str:
    return " ".join(text.split()).upper()


def parse(xlsx_path: str, dos_csv: str | None = None):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    contests = []          # ordered dicts: office, district, party, candidates
    total_mismatches = []  # committee-race printed-Totals divergences
    cur = None

    def emit():
        nonlocal cur
        if cur is not None and cur["candidates"]:
            contests.append(cur)
        cur = None

    for sheet, party in (("Democratic", "DEM"), ("Republican", "REP")):
        ws = wb[sheet]
        emit()
        for row in ws.iter_rows(values_only=True):
            head = row[0]
            if head is None or not str(head).strip():
                continue
            key = norm_key(str(head))
            if key.startswith(SKIP_PREFIXES):
                continue
            if key in OFFICE_MAP:
                emit()
                office, district = OFFICE_MAP[key]
                cur = {"office": office, "district": district, "party": party,
                       "candidates": {}, "printed_totals": {}, "order": []}
                continue
            m = COMMITTEE_RE.match(key)
            if m:
                emit()
                # the REP sheet mislabels its Tionesta Township local race
                # "DEMOCRATIC"; the sheet's own ballot party governs
                cur = {"office": f"{party_word(party)} Committee Member "
                                 f"{m.group(1).title()}",
                       "district": "", "party": party,
                       "candidates": {}, "printed_totals": {}, "order": []}
                continue
            m = re.match(r"^MEMBER OF THE (DEMOCRATIC|REPUBLICAN) "
                         r"STATE COMMITTEE$", key)
            if m:
                emit()
                cur = {"office": f"Member of {m.group(1).title()} State Committee",
                       "district": "", "party": party,
                       "candidates": {}, "printed_totals": {}, "order": []}
                continue
            if key.startswith("BARNETT") and "TOTALS" in key:
                continue  # in-grid precinct header line (uppercase text)
            if cur is None:
                continue
            # data row: name + 9 precinct cells + printed Totals
            cells = list(row[1:11]) + [None] * (11 - len(row))
            vals = [c if isinstance(c, (int, float)) else 0 for c in cells[:9]]
            printed_total = cells[9] if isinstance(cells[9], (int, float)) else 0
            name = clean_name(str(head))
            if name not in cur["order"]:
                cur["order"].append(name)
            cur["printed_totals"][name] = printed_total
            cur["candidates"].setdefault(name, {})
            for prec, v in zip(PRECS, vals):
                cur["candidates"][name][prec] = cur["candidates"][name].get(prec, 0) + int(v)
            if sum(vals) != printed_total:
                total_mismatches.append(
                    f"{sheet}: {cur['office']}: {name}: cells sum {sum(vals)}"
                    f" != printed Totals {printed_total}")

    emit()

    # statewide (non-committee) candidate rows must tie to their printed
    # Totals *before* corrections (the one bad statewide cell, Richey/Green,
    # carries a row Total consistent with its bad cell — 27/263 — so it alone
    # is exempted)
    exempt = {("Lieutenant Governor", "REP", "Jason Richey")}
    for c in contests:
        if c["office"] in {"Governor", "Lieutenant Governor", "U.S. House",
                           "State House"}:
            for name in c["order"]:
                s = sum(c["candidates"][name].values())
                t = c["printed_totals"][name]
                if s != t and (c["office"], c["party"], name) not in exempt:
                    raise SystemExit(
                        f"statewide row sum mismatch: {c['office']} "
                        f"{c['party']} {name}: cells {s} != printed {t}")
    apply_corrections(contests)

    if dos_csv:
        dos_check(contests, dos_csv)
    return contests, total_mismatches


def party_word(party: str) -> str:
    return "Democratic" if party == "DEM" else "Republican"


def apply_corrections(contests):
    for contest in contests:
        for (o, p, cand, prec), v in CORRECTIONS.items():
            if o == contest["office"] and p == contest["party"] \
                    and cand in contest["candidates"]:
                contest["candidates"][cand][prec] = v


def dos_check(contests, dos_csv: str):
    dos = {}
    with open(dos_csv, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            office = {"GOV": "Governor", "LTG": "Lieutenant Governor",
                      "USC": "U.S. House", "STH": "State House"}[r["OfficeCode"]]
            district = "15" if office == "U.S. House" else \
                ("65" if office == "State House" else "")
            dos.setdefault((office, district, r["PartyCode"],
                            r["LastName"].strip().upper(),
                            PRECS_DOS[r["PrecinctCode"]]),
                           int(r["VoteTotal"]))
    expected_named = {"SHAPIRO", "DAVIS", "BILGER", "HOWARD", "GARRITY",
                      "VENTRE", "RICHEY", "THOMPSON", "RAPP"}
    bad, matched = [], 0
    for contest in contests:
        if contest["office"] not in {"Governor", "Lieutenant Governor",
                                     "U.S. House", "State House"}:
            continue
        for name in contest["order"]:
            if name == "Scattered":
                continue  # DOS carries no write-in rows
            key = "".join(c for c in name.split()[-1].upper() if c.isalpha())
            if key not in expected_named:
                continue  # DOS carries no write-in rows
            for prec in PRECS:
                dv = dos[(contest["office"], contest["district"],
                          contest["party"], key, prec)]
                v = contest["candidates"][name][prec]
                correct = CORRECTIONS.get(
                    (contest["office"], contest["party"], name, prec))
                if correct is not None:
                    if v != correct or dv != correct:
                        bad.append(f"{key}: parser {v} DOS {dv} "
                                   f"CORRECTION {correct}")
                    continue
                matched += 1
                if dv != v:
                    bad.append(f"{contest['office']} {contest['party']} "
                               f"{name} {prec}: parser {v} vs DOS {dv}")
    print(f"DOS cross-check: {matched}/{9 * len(expected_named)} "
          f"statewide precinct cells compared digit-exact; "
          f"1 documented correction applied (Richey/Green Township)")
    if bad:
        for b in bad:
            print("  MISMATCH:", b)
        raise SystemExit("unexpected DOS mismatches — aborting")


PRECS_DOS = {
    "10": "Barnett Township", "20": "Green Township", "30": "Harmony Township",
    "40": "Hickory Township", "50": "Howe Township", "60": "Jenks Township",
    "70": "Kingsley Township", "80": "Tionesta Township",
    "90": "Tionesta Borough",
}


def main() -> None:
    args = sys.argv[1:]
    dos = None
    if "--dos" in args:
        i = args.index("--dos")
        dos = args.pop(i + 1)
        args.pop(i)
    if len(args) < 1:
        raise SystemExit(__doc__)
    xlsx = args[0]
    out_prec = args[1] if len(args) > 1 else \
        f"2026/counties/{ELECTION_DATE}__pa__primary__forest__precinct.csv"
    out_county = args[2] if len(args) > 2 else \
        f"2026/counties/{ELECTION_DATE}__pa__primary__forest__county.csv"

    contests, total_mismatches = parse(xlsx, dos)

    # contest-major: per contest, candidate order as printed (Scattered last)
    rows_prec = []
    for c in contests:
        for name in c["order"]:
            for prec in PRECS:
                rows_prec.append([COUNTY, prec, c["office"], c["district"],
                                  c["party"], name,
                                  c["candidates"][name][prec]])

    rows_county = []
    for c in contests:
        for name in c["order"]:
            votes = sum(c["candidates"][name][p] for p in PRECS)
            rows_county.append([COUNTY, c["office"], c["district"],
                                c["party"], name, votes])

    Path(out_prec).parent.mkdir(parents=True, exist_ok=True)
    with open(out_prec, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows_prec)
    with open(out_county, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "office", "district", "party", "candidate",
                    "votes"])
        w.writerows(rows_county)

    print(f"{COUNTY}: wrote {len(rows_prec)} precinct rows -> {out_prec}")
    print(f"{COUNTY}: wrote {len(rows_county)} county rows -> {out_county}")
    if total_mismatches:
        print(f"printed-Totals divergences (committee races, resolved to "
              f"precinct-cell sums; {len(total_mismatches)} rows):")
        for m in total_mismatches:
            print("  ", m)


if __name__ == "__main__":
    main()
