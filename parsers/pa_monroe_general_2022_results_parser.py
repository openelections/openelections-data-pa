#!/usr/bin/env python3
"""Parse Monroe County 2022 general election precinct results.

Sources (in parse order):

1. PA Department of State bulk precinct-returns file for the 2022 General
   ("prcres.txt"; from
   https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/
   voting-and-election-statistics/election-data, file
   ElectionReturns_2022_General_PrecinctReturns.txt, county code 45 = Monroe).
   This is the only precinct-level source available: the county's own
   "Monroe PA 2022 General Official Certified Results.xlsx" contains countywide
   totals for the 8 contests with no precinct detail.  The DOS precinct-level
   returns match every countywide candidate total in that certified county
   workbook exactly (23/23 candidates verified).

2. "Monroe PA 2022 General Election Write-Ins Official.pdf" (county Board of
   Elections write-in review report, ES&S "Total Votes - visual inspection"
   style).  Per contest it lists precinct columns with two row categories:
   "Write-in Votes reviewed but not assigned to a candidate" and "Scattered".
   - A precinct column appears only when that contest had reviewed write-in
     candidates in the precinct; unlisted precincts get no Write Ins row.
   - Both categories are combined into one `Write Ins` row per (precinct,
     contest), party="", per repo convention.
   - The report's printed "Total" column values are verified against the sums
     of the parsed per-precinct values for every contest and category.

   Note: the county workbook's countywide "Write-in" row is SMALLER than the
   write-in report's category totals (e.g. U.S. Senate 106 vs 46 + 70 = 116);
   the difference (9-10 votes per contest) is documented as a source-level
   discrepancy.  The workbook's write-in row is NOT emitted.

   Quirk: Ross Township is split between the 7th and 8th congressional
   districts.  In the certified returns "ROSS (USC 8)" reports votes ONLY for
   the CD-8 contest and zero for every other contest, while "ROSS (USC 7)"
   carries all of Ross Township's other contests (the county workbook counts
   Ross as one precinct, hence "51 precincts" vs 52 DOS precinct rows).
   Accordingly the single "Ross" write-in column in the statewide tables is
   attached to Ross (USC 7); the "Ross" column inside the CD-8 table is
   Ross (USC 8).

Not available in any source: Registered Voters / Ballots Cast, county row
offices, municipal contests, judicial retentions, vote-type breakdowns
(election day / mail / provisional) -> the output is a 7-column file.

Usage:
  python pa_monroe_general_2022_results_parser.py <prcres.txt> <writeins.pdf> <out.csv> [certified.xlsx]

The optional certified.xlsx only triggers verification of candidate countywide
totals; it does not change the output.
"""
from __future__ import annotations

import csv
import re
import sys
from collections import OrderedDict

import pdfplumber

COUNTY = "Monroe"
COUNTY_CODE = "45"
YEAR = "2022"
ETYPE = "G"

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# ---------------------------------------------------------------- precincts
ORDINALS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven"}
WARDORDINALS = {1: "First", 2: "Second", 3: "Third", 4: "Fourth",
                5: "Fifth", 6: "Sixth", 7: "Seventh"}

MUNI_NAMES = {
    "BARRETT": "Barrett", "CHESTNUTHILL": "Chestnuthill", "COOLBAUGH": "Coolbaugh",
    "DELAWARE WATER GAP": "Delaware Water Gap", "EAST STROUDSBURG": "East Stroudsburg",
    "ELDRED": "Eldred", "HAMILTON": "Hamilton", "JACKSON": "Jackson",
    "MIDDLE SMITHFIELD": "Middle Smithfield", "MOUNT POCONO": "Mount Pocono",
    "MT. POCONO": "Mount Pocono", "PARADISE": "Paradise", "POCONO": "Pocono",
    "POLK": "Polk", "PRICE": "Price", "SMITHFIELD": "Smithfield", "STROUD": "Stroud",
    "STROUDSBURG": "Stroudsburg", "TOBYHANNA": "Tobyhanna", "TUNKHANNOCK": "Tunkhannock",
}

# (muni base name, breakdown name uppercase) -> county's printed-style suffix
_X_NAMES = {
    ("Hamilton", "NORTH"): "Northern", ("Hamilton", "SOUTH"): "Southern",
    ("Jackson", "NORTH"): "North", ("Jackson", "SOUTH"): "South",
    ("Middle Smithfield", "EAST"): "Eastern", ("Middle Smithfield", "WEST"): "Western",
    ("Tobyhanna", "EAST"): "Eastern", ("Tobyhanna", "WEST"): "Western",
}


def precinct_name(_code: str, muni: str, b1c: str, b1n: str) -> str:
    """Output precinct name from the DOS full precinct description."""
    muni = muni.strip()
    if muni in ("ROSS (USC 7)", "ROSS (USC 8)"):
        return "Ross (USC 7)" if muni.endswith("7)") else "Ross (USC 8)"
    core, xpart = (muni.split(" X ", 1) + [""])[:2]
    if xpart:
        xpart = xpart.strip()
    core = core.strip()
    m = re.match(r"^(.*\S)\s+[DW]\s+(\d+)$", core)
    if m:
        base = MUNI_NAMES.get(m.group(1).strip().upper())
        if base is None:
            raise ValueError(f"unknown Monroe municipality/precinct description: {muni!r}")
        n = int(m.group(2))
        # county convention: East Stroudsburg and Stroudsburg breakdowns wards,
        # township districts by ordinal word ("Barrett One")
        if base in ("East Stroudsburg", "Stroudsburg"):
            return f"{base} {WARDORDINALS[n]} Ward"
        return f"{base} {ORDINALS[n]}"
    base = MUNI_NAMES.get(core.upper())
    if base is None:
        raise ValueError(f"unknown Monroe municipality/precinct description: {muni!r}")
    if b1c == "X" and b1n:
        suffix = _X_NAMES.get((base, b1n.upper()))
        if suffix is None:
            raise ValueError(f"unmapped X breakdown {b1c}/{b1n} for {muni!r}")
        return f"{base} {suffix}"
    return base


# ---------------------------------------------------------------- contests
OFFICE_MAP = {
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "USC": "U.S. House",   # district comes from the row's district field
    "STS": "State Senate",
    "STH": "State House",
}
OFFICE_RANK = {"USS": 1, "GOV": 2, "USC": 3, "STS": 4, "STH": 5}
OFFICE_BY_NAME = {name: code for code, name in OFFICE_MAP.items()}

# DOS candidate number -> (office code, party, output name).  Names follow the
# dominant forms already used in the 2022 data (statewide consolidated file /
# ENR anchor; Monroe's own 2024/2020 files use the same short forms).
CANDIDATES = {
    "2022C0203": ("USS", "DEM", "John Fetterman"),
    "2022C0147": ("USS", "REP", "Mehmet Oz"),
    "2022C1609": ("USS", "LIB", "Erik Gerhardt"),
    "2022C1613": ("USS", "GRN", "Richard L. Weiss"),
    "2022C1605": ("USS", "KEY", "Daniel Wassmer"),
    "2022C0206": ("GOV", "DEM", "Josh Shapiro"),
    "2022C0139": ("GOV", "REP", "Douglas V. Mastriano"),
    "2022C1618": ("GOV", "LIB", "Matt Hackenburg"),
    "2022C1611": ("GOV", "GRN", "Christina DiGiulio"),
    "2022C1606": ("GOV", "KEY", "Joe Soloski"),
    "2022C0200": ("USC", "DEM", "Susan Wild"),
    "2022C0202": ("USC", "REP", "Lisa Scheller"),
    "2022C0183": ("USC", "DEM", "Matt Cartwright"),
    "2022C0157": ("USC", "REP", "Jim Bognet"),
    "2022C1120": ("STS", "DEM", "Jennifer Shukaitis"),
    "2022C0274": ("STS", "REP", "Rosemary Brown"),
    "2022C1119": ("STH", "DEM", "Maureen Madden"),
    "2022C0452": ("STH", "REP", "Krista Paolucci"),
    "2022C1117": ("STH", "DEM", "Hope Christman"),
    "2022C1113": ("STH", "REP", "Jack Rader"),
    "2022C1597": ("STH", "LIB", "Autumn Pangia"),
    "2022C1051": ("STH", "DEM", "Tarah Probst"),
    "2022C1132": ("STH", "REP", "Steve Ertle"),
}

# PDF contest title -> (office code, district)
PDF_CONTESTS = {
    "US SENATOR": ("USS", ""),
    "GOVERNOR AND LIEUTENANT GOVERNOR": ("GOV", ""),
    "REPRESENTATIVE IN CONGRESS 7TH DISTRICT": ("USC", "7"),
    "REPRESENTATIVE IN CONGRESS 8TH DISTRICT": ("USC", "8"),
    "SENATOR IN THE GENERAL ASSEMBLY 40TH DISTRICT": ("STS", "40"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 115TH DISTRICT": ("STH", "115"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 176TH DISTRICT": ("STH", "176"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 189TH DISTRICT": ("STH", "189"),
}

# Write-in PDF column header -> output precinct name ("__ROSS__" resolved per contest).
PDF_NAMES = {
    "Barrett One": "Barrett One", "Barrett Two": "Barrett Two",
    "Chestnuthill One": "Chestnuthill One", "Chestnuthill Two": "Chestnuthill Two",
    "Chestnuthill Three": "Chestnuthill Three", "Chestnuthill Four": "Chestnuthill Four",
    "Coolbaugh One": "Coolbaugh One", "Coolbaugh Two": "Coolbaugh Two",
    "Coolbaugh Three": "Coolbaugh Three", "Coolbaugh Four": "Coolbaugh Four",
    "Delaware Water Gap": "Delaware Water Gap",
    "Eas Str First Ward": "East Stroudsburg First Ward",
    "Eas Str Second Ward": "East Stroudsburg Second Ward",
    "Eas Str Third Ward": "East Stroudsburg Third Ward",
    "Eas Str Fourth Ward": "East Stroudsburg Fourth Ward",
    "Eas Str Fifth Ward": "East Stroudsburg Fifth Ward",
    "Eas Str Sixth Ward": "East Stroudsburg Sixth Ward",
    "Eldred": "Eldred",
    "Hamilton Northern": "Hamilton Northern", "Hamilton Southern": "Hamilton Southern",
    "Jackson North": "Jackson North", "Jackson South": "Jackson South",
    "Mid Smi Eastern": "Middle Smithfield Eastern",
    "Mid Smi Western": "Middle Smithfield Western",
    "Mount Pocono": "Mount Pocono", "Paradise": "Paradise",
    "Pocono One": "Pocono One", "Pocono Two": "Pocono Two",
    "Pocono Three": "Pocono Three", "Pocono Four": "Pocono Four",
    "Polk": "Polk", "Price": "Price",
    "Ross": "__ROSS__",
    "Smithfield One": "Smithfield One", "Smithfield Two": "Smithfield Two",
    "Smithfield Three": "Smithfield Three",
    "Str First Ward": "Stroudsburg First Ward",
    "Str Second Ward": "Stroudsburg Second Ward",
    "Str Third Ward": "Stroudsburg Third Ward",
    "Str Fourth Ward": "Stroudsburg Fourth Ward",
    "Str Fifth Ward": "Stroudsburg Fifth Ward",
    "Str Sixth Ward": "Stroudsburg Sixth Ward",
    "Stroud One": "Stroud One", "Stroud Two": "Stroud Two",
    "Stroud Three": "Stroud Three", "Stroud Four": "Stroud Four",
    "Stroud Five": "Stroud Five", "Stroud Six": "Stroud Six",
    "Stroud Seven": "Stroud Seven",
    "Tobyhanna Eastern": "Tobyhanna Eastern", "Tobyhanna Western": "Tobyhanna Western",
    "Tunkhannock": "Tunkhannock",
}

# rotated "OFFICIAL" watermark letters that get glued onto cell text
_WATERMARK = set("IFOACL")


def _clean_cell(cell: str | None) -> str:
    s = " ".join(cell.split()) if cell else ""
    # drop leading rotated-watermark letters: glued ('ICoolbaugh Two',
    # 'OScattered') or as their own token ('I Coolbaugh Two')
    while s and s[0].upper() in _WATERMARK and (
            len(s) == 1 or s[1].isupper() or s[1] == " "):
        s = s[1:].lstrip()
    return s.strip()


def _parse_num(cell: str | None) -> int | None:
    """Parse a vote cell; tolerates glued watermark letters ('C0', 'C\\n0' -> 0)."""
    s = (cell or "").strip()
    if not s:
        return None
    compact = re.sub(r"\s+", "", s)
    if not re.fullmatch(r"[IFOACL]*\d{1,4}", compact):
        return None
    return int(re.sub(r"[^0-9]", "", compact))


# ------------------------------------------------------------ prcres reader
def read_prcres(path: str):
    """Read Monroe 2022 general rows from the DOS bulk precinct returns file."""
    contests: OrderedDict = OrderedDict()  # (precinct, off, district) -> entry
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 27 or r[0] != YEAR or r[1] != ETYPE or r[2] != COUNTY_CODE:
                continue
            off_code, cand_no = r[8], r[10]
            if off_code not in OFFICE_MAP:
                raise ValueError(f"unhandled office code {off_code!r} ({r[22]})")
            cand = CANDIDATES.get(cand_no)
            if cand is None:
                raise ValueError(f"unhandled candidate number {cand_no}")
            if cand[0] != off_code:
                raise ValueError(f"contest mismatch for {cand_no} in {off_code}")
            district = "" if off_code in ("USS", "GOV") else str(int(r[5]))
            prec = precinct_name(str(int(r[3])), r[22], r[23], r[24])
            key = (prec, off_code, district)
            entry = contests.setdefault(key, {"office": OFFICE_MAP[off_code], "cands": []})
            entry["cands"].append((cand[2], cand[1], int(r[15])))
    return contests


def contest_reported(contests) -> dict:
    """(office code, district) -> set of precincts reporting that contest."""
    reported: dict = {}
    for (pre, off, dist) in contests:
        reported.setdefault((off, dist), set()).add(pre)
    return reported


# ------------------------------------------------------------ write-in PDF
def _is_numbers_row(row) -> bool:
    if not row:
        return False
    hits = sum(1 for c in row if _parse_num(c) is not None)
    return hits >= 2


def parse_writeins(path: str):
    """Per-precinct write-in votes from the county write-in review report.

    Returns (out, contest_check) where
      out = {(precinct, off, district): votes}  (not-assigned + scattered),
      contest_check = {(off, district): {"not_assigned": int, "scattered": int,
                                         "sum": int}}  (sum = parsed precinct sum).
    """
    out: dict = {}
    contest_check: dict = {}
    contest = None
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages):
            for t_i, table in enumerate(page.extract_tables()):
                nums_idx = [i for i, row in enumerate(table) if _is_numbers_row(row)]
                if not nums_idx:
                    continue
                if len(nums_idx) != 2:
                    raise ValueError(f"page {pno} table {t_i}: expected 2 data rows, "
                                     f"got {len(nums_idx)}")
                for row in table:
                    for c in row:
                        cc = _clean_cell(c)
                        if cc in PDF_CONTESTS:
                            contest = PDF_CONTESTS[cc]
                if contest is None:
                    raise ValueError(f"page {pno} table {t_i}: no contest seen yet")
                # names row sits directly above the "Write-in Votes reviewed..."
                # label row, i.e. two rows above the first numbers row (one in
                # reports without the label row); detect either layout
                names_row = None
                for cand_idx in (nums_idx[0] - 2, nums_idx[0] - 1):
                    if cand_idx < 0:
                        continue
                    cleaned = [_clean_cell(c) for c in table[cand_idx]]
                    if any(n in PDF_NAMES or n == "Total" for n in cleaned):
                        names_row = cleaned
                        break
                if names_row is None:
                    raise ValueError(f"page {pno} table {t_i}: no header row found")
                # first data row of a table is "not assigned", second "scattered";
                # cross-check against the labels the report prints
                for cat, nidx in (("not_assigned", nums_idx[0]),
                                  ("scattered", nums_idx[1])):
                    row = table[nidx]
                    if len(row) != len(names_row):
                        raise ValueError(f"page {pno} table {t_i}: width mismatch")
                    label = " ".join(_clean_cell(c) for c in table[nidx - 1])
                    if ("assigned" in label.lower() or "reviewed" in label.lower()) \
                            and cat != "not_assigned":
                        raise ValueError(f"page {pno} table {t_i}: label {label!r} "
                                         f"but expected {cat}")
                    if "scattered" in label.lower() and cat != "scattered":
                        raise ValueError(f"page {pno} table {t_i}: label {label!r} "
                                         f"but expected {cat}")
                    values: OrderedDict = OrderedDict()
                    for name, cell in zip(names_row, row):
                        if name == "":
                            if _parse_num(cell) is not None:
                                raise ValueError(
                                    f"page {pno} table {t_i}: number under empty header")
                            continue
                        v = _parse_num(cell)
                        if v is None:
                            raise ValueError(
                                f"page {pno} table {t_i}: missing number for {name!r}")
                        values[name] = v
                    total = values.pop("Total", None)
                    ch = contest_check.setdefault(contest, {"not_assigned": None,
                                                            "scattered": None, "sum": 0})
                    if total is not None:
                        ch[cat] = total
                    if len(set(values)) != len(values):
                        raise ValueError(f"page {pno} table {t_i}: duplicate column names")
                    for name, v in values.items():
                        real = resolve_pdf_precinct(name, contest)
                        k = (real, contest[0], contest[1])
                        out[k] = out.get(k, 0) + v
                    ch["sum"] += sum(values.values())
    # verify: per contest, parsed precinct sums == printed totals
    for contest, ch in contest_check.items():
        for cat in ("not_assigned", "scattered"):
            if ch[cat] is None:
                raise ValueError(f"contest {contest}: no printed Total for {cat}")
    return out, contest_check


def resolve_pdf_precinct(name: str, contest) -> str:
    """Map a write-in PDF column header to the output precinct name."""
    mapped = PDF_NAMES.get(name.strip())
    if mapped is None:
        raise ValueError(f"unknown write-in column header {name!r}")
    if mapped == "__ROSS__":
        # Ross (USC 8) reports values only in the CD-8 contest; "Ross" columns
        # in every other contest are Ross (USC 7).
        return "Ross (USC 8)" if contest == ("USC", "8") else "Ross (USC 7)"
    return mapped


# ---------------------------------------------------------------- output
def build_rows(prcres_path: str, writeins_path: str):
    contests = read_prcres(prcres_path)
    wi, contest_check = parse_writeins(writeins_path)
    reported = contest_reported(contests)

    # every write-in column must be a precinct that reports that contest
    for (pre, off, dist) in wi:
        allowed = reported.get((off, dist))
        if allowed is None or pre not in allowed:
            raise ValueError(f"write-in column {pre!r} not among precincts "
                             f"reporting {off} {dist}")

    # every contest with write-ins must have a (precinct, contest) write-in
    # entry summing to its printed totals
    for contest, ch in contest_check.items():
        got = sum(v for (p, off, d), v in wi.items()
                  if off == contest[0] and d == contest[1])
        want = ch["not_assigned"] + ch["scattered"]
        if got != want:
            raise ValueError(f"contest {contest}: write-in precinct sum {got} "
                             f"!= printed not_assigned {ch['not_assigned']} + "
                             f"scattered {ch['scattered']} = {want}")

    rows = []
    for (pre, off, dist), entry in contests.items():
        for name, party, votes in entry["cands"]:
            rows.append({"county": COUNTY, "precinct": pre, "office": entry["office"],
                         "district": dist, "party": party, "candidate": name,
                         "votes": votes})
        wv = wi.get((pre, off, dist))
        if wv is not None and wv > 0:
            rows.append({"county": COUNTY, "precinct": pre, "office": entry["office"],
                         "district": dist, "party": "", "candidate": "Write Ins",
                         "votes": wv})
    return rows, contest_check, wi


def write_csv(path: str, rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {path}")


def verify_certified(xlsx_path: str, rows: list[dict], contest_check: dict,
                     wi: dict):
    """Compare countywide totals with the county's certified results workbook."""
    import collections

    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["ElectionData"]
    party_codes = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "LIBERTARIAN": "LIB",
                   "GREEN PARTY": "GRN", "KEYSTONE": "KEY"}
    cert_party: dict = {}   # (contest_key, party code) -> votes
    cert_wi: dict = {}      # contest_key -> workbook "Write-in" row votes
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        title, party, cand, votes = (str(row[0]), str(row[5]), str(row[6]), int(row[7]))
        ck = PDF_CONTESTS.get(title)
        if ck is None:
            raise ValueError(f"certified workbook contest not in map: {title!r}")
        if cand.lower().startswith("write-in"):
            cert_wi[ck] = cert_wi.get(ck, 0) + votes
        else:
            code = party_codes.get(party)
            if code is None:
                raise ValueError(f"unknown party in workbook: {party!r}")
            cert_party[(ck, code)] = cert_party.get((ck, code), 0) + votes

    # ours: party-level candidate sums per contest
    ours_party: dict = collections.defaultdict(int)
    for r in rows:
        if r["candidate"] == "Write Ins":
            continue
        ck = (OFFICE_BY_NAME[r["office"]], r["district"])
        ours_party[(ck, r["party"])] += r["votes"]

    mismatches = 0
    for (ck, code), votes in cert_party.items():
        got = ours_party.get((ck, code))
        if got != votes:
            mismatches += 1
            print(f"  MISMATCH {OFFICE_MAP[ck[0]]} {ck[1]} {code}: "
                  f"precinct file {got} vs workbook {votes}")
    n_exact = len(cert_party) - mismatches

    # write-in rows: the review report per-precinct sums vs the workbook's
    # smaller "Write-in" row (documented source-level discrepancy)
    wi_reported = 0
    for ck, votes in cert_wi.items():
        got = sum(v for (p, off, d), v in wi.items() if off == ck[0] and d == ck[1])
        wi_reported += 1
        if got != votes:
            print(f"  write-in {OFFICE_MAP[ck[0]]} {ck[1]}: review report per-precinct "
                  f"sum {got} vs workbook write-in row {votes} ({got - votes} more; "
                  f"expected source-level discrepancy)")
    print(f"certified workbook check: {n_exact}/{len(cert_party)} candidate party "
          f"totals exact, {mismatches} mismatch(es); "
          f"workbook write-in rows compared for {wi_reported} contests")


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__)
        return 2
    prcres, writeins, outfile = argv[1], argv[2], argv[3]
    rows, contest_check, wi = build_rows(prcres, writeins)
    write_csv(outfile, rows)
    print("write-in printed totals (per contest, from the PDF 'Total' column):")
    for contest, ch in contest_check.items():
        print(f"  {contest}: not_assigned={ch['not_assigned']} "
              f"scattered={ch['scattered']} parsed_sum={ch['sum']}")
    if len(argv) > 4:
        verify_certified(argv[4], rows, contest_check, wi)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))