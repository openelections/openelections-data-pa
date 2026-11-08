#!/usr/bin/env python3
"""Parse Monroe County 2026 primary election results.

2026 General Primary (2026-05-19), Monroe uses 3 digit-precinct "X" splits of
municipalities; the county's own export prints precincts as e.g. "Barrett One".

Sources (in parse order):

1. "Monroe County 2026 Primary Official Precinct Results.csv" (county Board of
   Elections export; primary source).  Four-column rows Contest,Precinct,Party,
   Candidate,Votes for all 18 countywide contests (Governor, Lieutenant
   Governor, U.S. House 7/8, State Senate 40, State House 115/176/189,
   Democratic and Republican State Committee) across all 55 precincts,
   including one "Write-in (PARTY)" row per (contest, precinct) ballot party.
   No vote breakdowns and no Registered Voters / Ballots Cast rows are
   carried, so the output is a 7-column file.  State House 176 (DEM) was a
   write-in-only contest (no candidate on the Democratic ballot); its
   "Write-in (DEMOCRATIC)" rows are kept at party DEM.

2. "Monroe County Summary PrimaryElectionData9.xlsx" (county ElectionData
   summary workbook) -> countywide CSV.  Its first 18 contest titles are the
   countywide contests with contest-level Total Votes / Candidate Votes (no
   breakdowns); the remaining rows are per-precinct Democratic Committee
   person / Republican County Committee races (single-precinct contests), which
   are not countywide and are omitted here (the precinct export carries no
   per-precinct detail for them either, so they are not in the precinct file;
   documented as an omission).  Each contest's printed Total Votes is checked
   to equal the sum of its Candidate Votes rows.

DOS certified returns: the county slice (work2026p/dos/
monroe__dos_precinctreturns.txt, identical to the "Monroe County DOS Official
Precinct Returns 2026 Primary.csv" copy) is used ONLY as a cross-check of the
county export's named-candidate totals (Governor through State House; the DOS
file carries no write-in rows and no State Committee contests, per the 2022/
2024 DOS quirks).  No contest needed filling from DOS: the county export
carries every contest.

Verification performed on each run:
  - every (office, party, candidate) precinct-row sum equals the countywide
    2026 anchor file row ("2026/20260519__pa__primary__county.csv", matched
    case-insensitively on the candidate's last name) where the anchor lists
    the candidate, and equals the summary workbook's Candidate Votes
    otherwise (HD 176 DEM write-in-only, and every Write-In Totals row);
  - per-prefiled contest totals in the workbook equal candidate-sum rows;
  - named-candidate sums equal the DOS county slice sums.

Usage:
    uv run python parsers/pa_monroe_primary_2026_results_parser.py \
        [src_csv] [summary_xlsx] [dos_csv] [out_dir]
"""

import csv
import os
import re
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(os.path.expanduser("~"), "code", "openelections-sources-pa", "2026", "primary")

DEFAULT_CSV = os.path.join(SRC_DIR, "Monroe County 2026 Primary Official Precinct Results.csv")
DEFAULT_XLSX = os.path.join(SRC_DIR, "Monroe County Summary PrimaryElectionData9.xlsx")
DEFAULT_DOS = os.path.join(SRC_DIR, "Monroe County DOS Official Precinct Returns 2026 Primary.csv")
DEFAULT_OUT = os.path.join(REPO, "2026", "counties")
DOS_FALLBACK_TXT = os.path.join(REPO, "work2026p", "dos", "monroe__dos_precinctreturns.txt")

COUNTY = "Monroe"

ROMAN = {"JR", "SR", "II", "III", "IV", "VI", "VII", "VIII", "IX"}


# --------------------------------------------------------------------------- helpers
def title_name(name):
    """Title-case an ALLCAPS name, preserving suffixes/initials sensibly."""
    out = []
    for tok in name.split():
        if re.fullmatch(r"[A-Z](\.?)", tok):
            out.append(tok)  # single-letter initials stay as printed
        elif tok in ROMAN and tok in ("JR", "SR"):
            out.append(tok.capitalize())
        elif tok in ROMAN:  # roman numerals stay all caps (IV, VIII, ...)
            out.append(tok)
        elif tok[:2].upper() == "MC" and len(tok) > 2:
            out.append("Mc" + tok[2].upper() + tok[3:].lower())
        else:
            parts = []
            for piece in re.split(r"(-)", tok):
                parts.append(piece[0].upper() + piece[1:].lower() if piece else piece)
            out.append("".join(parts))
    return " ".join(out)


def slug(last_first):
    """Last-name key for verification: lowercase last meaningful token.

    Suffix-like tokens (Jr/Sr, roman numerals) are dropped so that sources
    printing them (county export "ROB BRESNAHAN JR") key the same as sources
    omitting them (DOS "ROBERT BRESNAHAN", anchor "ROB BRESNAHAN JR").
    """
    tail = re.sub(r"[^A-Za-z ]", "", last_first).strip().lower()
    toks = [t for t in tail.split() if t not in {"jr", "sr", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix"}]
    return (toks[-1] if toks else tail) or tail


def parse_office(contest):
    """Return (standard office, district) for a county-export contest title."""
    c = contest.strip()
    if c == "GOVERNOR":
        return "Governor", ""
    if c == "LIEUTENANT GOVERNOR":
        return "Lieutenant Governor", ""
    if c == "MEMBER OF DEMOCRATIC STATE COMMITTEE":
        return "Member of Democratic State Committee", ""
    if c == "MEMBER OF REPUBLICAN STATE COMMITTEE":
        return "Member of Republican State Committee", ""
    m = re.match(r"REPRESENTATIVE IN CONGRESS (\d+)TH? DISTRICT$", c)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"SENATOR IN THE GENERAL ASSEMBLY (\d+)TH? DISTRICT$", c)
    if m:
        return "State Senate", m.group(1)
    m = re.match(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY (\d+)TH? DISTRICT$", c)
    if m:
        return "State House", m.group(1)
    raise ValueError("unknown contest title: %r" % contest)


PARTY_CODE = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}


def read_county_export(path):
    """Read Contest,Precinct,Party,Candidate,Votes rows -> normalized tuples."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            office, district = parse_office(rec["Contest"])
            party = PARTY_CODE[rec["Party"].strip().upper()]
            cand = rec["Candidate"].strip()
            if re.match(r"^Write-in \(DEMOCRATIC\)$", cand, re.I):
                cand = "Write-In Totals"
            elif cand.upper().startswith("WRITE-IN"):
                cand = "Write-In Totals"
            else:
                cand = title_name(cand)
            rows.append(
                {
                    "county": COUNTY,
                    "precinct": rec["Precinct"].strip(),
                    "office": office,
                    "district": district,
                    "party": party,
                    "candidate": cand,
                    "votes": int(rec["Votes"]),
                }
            )
    return rows


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# --------------------------------------------------------------------------- county csv
def countywide_rows_from_xlsx(path):
    """18 countywide contests from the summary workbook -> county CSV rows."""
    import openpyxl

    ws = openpyxl.load_workbook(path).active
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    data = [dict(zip(hdr, r)) for r in rows[1:]]

    out = []
    for r in data:
        contest = str(r["Contest"]).strip()
        if re.search(r"COMMITTEEMAN|COMMITTEEWOMAN|COUNTY COMMITTEE", contest, re.I):
            continue  # single-precinct party committee races, not countywide
        office, district = parse_office(re.sub(r"\s*\((DEM|REP)\)$", "", contest))
        party = PARTY_CODE[r["Party"].strip().upper()]
        cand = str(r["Candidate"]).strip()
        if cand.lower() == "write-in":
            cand = "Write-In Totals"
        else:
            m = re.match(r"^(.+), (.+)$", cand)  # "SHAPIRO, JOSH" -> "Josh Shapiro"
            cand = title_name("%s %s" % (m.group(2), m.group(1)))
        out.append(
            {
                "county": COUNTY,
                "office": office,
                "district": district,
                "party": party,
                "candidate": cand,
                "votes": int(r["Candidate Votes"]),
            }
        )
    # order rows like the workbook (contest blocks, named then Write-In Totals)
    return out, data


# --------------------------------------------------------------------------- checks
def sum_precinct_rows(precinct_rows):
    totals = defaultdict(int)
    for r in precinct_rows:
        key = (r["office"], r["district"], r["party"], r["candidate"])
        totals[key] += r["votes"]
    return totals


def verify_against_anchor_and_xlsx(precinct_rows, county_rows, anchor_path):
    """Precinct sums must equal the statewide anchor (where listed) or xlsx."""
    party_map = {"democratic": "DEM", "dem": "DEM", "republican": "REP", "rep": "REP"}
    with open(anchor_path, newline="", encoding="utf-8-sig") as fh:
        anchor = defaultdict(dict)
        for rec in csv.DictReader(fh):
            if rec["county"].strip().upper() != COUNTY.upper():
                continue
            last = slug(rec["candidate"])
            party = party_map.get(rec["party"].strip().lower(), rec["party"].strip().upper())
            anchor[(rec["office"].strip(), party)][last] = int(rec["votes"])

    totals = sum_precinct_rows(precinct_rows)
    xlsx_by_key = defaultdict(dict)
    for r in county_rows:
        xlsx_by_key[(r["office"], r["district"], r["party"])][slug(r["candidate"])] = r["votes"]

    problems = []
    for key in sorted(totals):
        office, district, party, candidate = key
        last = slug(candidate)
        a = anchor.get((office, party), {})
        x = xlsx_by_key.get((office, district, party), {})
        if last in a:
            if totals[key] != a[last]:
                problems.append("anchor mismatch %s/%s/%s: precinct sum %d vs anchor %d"
                                % (office, party, candidate, totals[key], a[last]))
        elif last in x:
            if totals[key] != x[last]:
                problems.append("workbook mismatch %s/%s/%s: precinct sum %d vs workbook %d"
                                % (office, party, candidate, totals[key], x[last]))
        else:
            problems.append("no verification target for %s/%s/%s" % (office, party, candidate))
    # anchor rows we did not cover (contest in statewide file but missing here)
    covered = set()
    for (o, d, p, c) in totals:
        covered.add((o, p, slug(c)))
    missed = []
    for (office, party), cands in anchor.items():
        for last, votes in cands.items():
            if (office, party, last) not in covered:
                missed.append("anchor row not covered: %s/%s/%s=%d" % (office, party, last, votes))
    return problems, missed


def verify_dos(rows, dos_path):
    """Named-candidate sums must equal the DOS certified county slice."""
    totals = defaultdict(int)
    with open(dos_path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            office = rec["office_code"].strip()
            party = rec["party"].strip().upper()
            last = slug(rec["candidate"])
            totals[(office, party, last)] += int(rec["votes"])
    ours = defaultdict(int)
    for (office, _district, party, candidate), votes in sum_precinct_rows(rows).items():
        ours[(office, party, slug(candidate))] += votes
    mapping = {"GOV": "Governor", "LTG": "Lieutenant Governor", "USC": "U.S. House",
               "STS": "State Senate", "STH": "State House"}
    problems = []
    for (code, party, last), dos_votes in sorted(totals.items()):
        office = mapping.get(code, code)
        got = ours.get((office, party, last))
        if got != dos_votes:
            problems.append("DOS mismatch %s/%s/%s: county export %r vs DOS %d"
                            % (office, party, last, got, dos_votes))
    return problems, len(totals)


def verify_workbook_totals(data):
    """Summary workbook contest Total Votes must equal sum of Candidate Votes."""
    problems = []
    per = defaultdict(lambda: [0, None])
    for r in data:
        c = str(r["Contest"]).strip()
        if re.search(r"COMMITTEEMAN|COMMITTEEWOMAN|COUNTY COMMITTEE", c, re.I):
            continue
        per[c][0] += int(r["Candidate Votes"])
        per[c][1] = int(r["Total Votes"])
    for c, (cand_sum, total) in per.items():
        if cand_sum != total:
            problems.append("workbook %s: candidate sum %d != Total Votes %d" % (c, cand_sum, total))
    return problems, len(per)


def main(argv):
    src = argv[1] if len(argv) > 1 else DEFAULT_CSV
    xlsx = argv[2] if len(argv) > 2 else DEFAULT_XLSX
    dos = argv[3] if len(argv) > 3 else DEFAULT_DOS
    out_dir = argv[4] if len(argv) > 4 else DEFAULT_OUT
    if not os.path.exists(dos) and os.path.exists(DOS_FALLBACK_TXT):
        dos = DOS_FALLBACK_TXT

    os.makedirs(out_dir, exist_ok=True)
    precinct_rows = read_county_export(src)
    precinct_path = os.path.join(out_dir, "20260519__pa__primary__monroe__precinct.csv")
    write_csv(precinct_path, precinct_rows,
              ["county", "precinct", "office", "district", "party", "candidate", "votes"])

    county_rows, xlsx_data = countywide_rows_from_xlsx(xlsx)
    county_path = os.path.join(out_dir, "20260519__pa__primary__monroe__county.csv")
    write_csv(county_path, county_rows,
              ["county", "office", "district", "party", "candidate", "votes"])

    anchor_path = os.path.join(REPO, "2026", "20260519__pa__primary__county.csv")
    problems, missed = verify_against_anchor_and_xlsx(precinct_rows, county_rows, anchor_path)
    wb_problems, n_wb = verify_workbook_totals(xlsx_data)
    problems += wb_problems
    dos_problems, n_dos = verify_dos(precinct_rows, dos)
    # DOS candidates must also have anchor/workbook coverage problems none above
    problems += dos_problems

    contests = sorted({(r["office"], r["district"], r["party"]) for r in precinct_rows})
    print("precinct rows: %d over %d precincts, contests: %d"
          % (len(precinct_rows), len({r["precinct"] for r in precinct_rows}), len(contests)))
    for o, d, p in contests:
        print("  %s %s %s" % (o, d, p))
    print("county rows: %d (workbook contests verified: %d)" % (len(county_rows), n_wb))
    print("DOS named-candidate keys cross-checked: %d" % n_dos)
    for p in problems:
        print("PROBLEM:", p)
    for p in missed:
        print("UNCOVERED:", p)
    print("verification:", "OK" if not problems else "FAILED (%d)" % len(problems),
          "| anchor-uncovered: %d" % len(missed))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
