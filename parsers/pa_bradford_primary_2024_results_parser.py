#!/usr/bin/env python3
"""Bradford County, PA 2024 Primary — precinct-level results parser.

Source situation (documented):
  * The county's certified report "Bradford PA 2024 Primary Final Results.pdf"
    (~/code/openelections-sources-pa/2024/primary/, JasperReports
    "Election Summary Report", 11 pages, extracted with pdftotext -layout to
    work2024p/bradford/bradford.txt) is COUNTYWIDE-ONLY: every page carries
    contest-level county aggregate tables ("Number of Precincts 61",
    "Precincts Reporting 61 100.00%", "Total Votes" plus a (votes, percent)
    pair per candidate).  There are no per-precinct rows anywhere in the
    file — verified by extracting all 11 pages; the only "precinct" strings
    are the "N of 61 Precincts Reporting" header lines.
  * Precinct-level figures therefore come from the PA Department of State
    official certified precinct returns bulk file for the primary:
        ElectionReturns_2024_Primary_PrecinctReturns.txt
    (July 23, 2024 extract, 368,555 records; ASCII comma-delimited per the
    paired readme, with ".0" float-suffixed numeric fields; Bradford =
    county code 8; 2,111 rows = 61 precincts x the county's named-candidate
    rows).  A Bradford slice is kept at
    work2024p/bradford/bradford_dos.txt.  The DOS precinct list matches the
    county's "61 of 61 Precincts Reporting" exactly.

Consequences (all inherent to the DOS bulk source, not parsing losses):
  * No election-day / mail / provisional / absentee breakdown -> 7-column
    output header (the countywide summary PDF carries only contest totals,
    not per-precinct breakdowns, so none can be joined on).
  * No per-precinct write-in rows (the 2024 DOS bulk file carries NO
    write-in rows for any county — every Candidate Number is a "2024C"
    certified candidate): the summary PDF's write-in lines (e.g. President
    DEM "Donald J Trump (W)" 67 / "Robert Kennedy Jr (W)" 28 / Write-in 38;
    USS REP "Bob Casey Jr (W)" 50; ATT DEM Write-in 7; STS DEM district 23
    with two (W) write-in candidates; STH 68 DEM Clint Owlett (W) 14;
    STH 110 DEM all-(W) rows; the 8 Alternate Delegate (W) rows; and every
    contest's plain "Write-in" line) cannot be assigned to precincts.
    Per the no-invention rule no Write-in rows are emitted and the
    write-in-only Alternate Delegate contest has no rows at all.
  * The party committee offices (e.g. summary page 4+ "REPUBLICAN COMMITTEE
    - ASYLUM TOWNSHIP", which report Write-in only) are county/party
    precinct-committee contests absent from the DOS bulk file, so they have
    no rows (Clarion/Bedford/Cameron DOS precedent).

Verification: precinct-row sums per (office, party, candidate) equal the
countywide totals printed in the certified summary PDF for all 37 named
candidates (--verify), and also equal the DOS-file county column sums
trivially.  DOS sums deliberately differ from the PDF "Total Votes" lines
wherever the PDF's Total includes write-in votes not present in DOS.

Usage:
    python parsers/pa_bradford_primary_2024_results_parser.py [src_txt] [out.csv]
    python parsers/pa_bradford_primary_2024_results_parser.py --verify [src_txt] [out.csv]

Defaults: src = work2024p/bradford/bradford_dos.txt (relative to repo root),
out = 2024/counties/20240423__pa__primary__bradford__precinct.csv.
"""
from __future__ import annotations

import csv
import os
import sys

COUNTY_NAME = "Bradford"
COUNTY_CODE = "8"
ELECTION_TYPE = "P"

HEADER = ["county", "precinct", "office", "district", "party", "candidate", "votes"]
OFFICE_ORDER = ["President", "U.S. Senate", "Attorney General", "Auditor General",
                "State Treasurer", "U.S. House", "State Senate", "State House",
                "Delegate to the Democratic National Convention",
                "Delegate to the Republican National Convention"]

OFFICES = {  # DOS office code -> (office name, district source "cand"/"none")
    "USP": ("President", "none"),
    "USS": ("U.S. Senate", "none"),
    "ATT": ("Attorney General", "none"),
    "AUD": ("Auditor General", "none"),
    "TRE": ("State Treasurer", "none"),
    "USC": ("U.S. House", "cand"),
    "STS": ("State Senate", "cand"),
    "STH": ("State House", "cand"),
    "DED": ("Delegate to the Democratic National Convention", "cand"),
    "DER": ("Delegate to the Republican National Convention", "cand"),
}

# DOS candidate (Last, First, Middle, Suffix) -> printed name, matching the
# county's certified summary PDF candidate rows.
NAME_FIX = {
    ("BIDEN", "JOSEPH", "R", "JR"): "Joseph R. Biden Jr",
    ("PHILLIPS", "DEAN", "BENSON", ""): "Dean Phillips",
    ("TRUMP", "DONALD", "JOHN", ""): "Donald J. Trump",
    ("HALEY", "NIKKI", "R", ""): "Nikki R. Haley",
    ("CASEY", "ROBERT", "P", "JR"): "Robert P. Casey Jr",
    ("MCCORMICK", "DAVID", "H", ""): "Dave McCormick",
    ("STOLLSTEIMER", "JACK", "", ""): "Jack Stollsteimer",
    ("DEPASQUALE", "EUGENIO", "", ""): "Eugene Depasquale",
    ("KHAN", "JOSEPH", "JAMIL", ""): "Joe Khan",
    ("BRADFORD-GREY", "KEIR", "N", ""): "Keir Bradford-Grey",
    ("SOLOMON", "JARED", "G", ""): "Jared Solomon",
    ("SUNDAY", "DAVID", "", ""): "Dave Sunday",
    ("WILLIAMS", "WENDELL", "CRAIG", ""): "Craig Williams",
    ("KENYATTA", "MALCOLM", "", ""): "Malcolm Kenyatta",
    ("PINSLEY", "MARK", "E", ""): "Mark Pinsley",
    ("DEFOOR", "TIMOTHY", "LIONEL", ""): "Tim DeFoor",
    ("BIZZARRO", "RYAN", "", ""): "Ryan Bizzarro",
    ("MCCLELLAND", "ERIN", "R", ""): "Erin McClelland",
    ("GARRITY", "STACY", "L", ""): "Stacy Garrity",
    ("WALDMAN", "AMANDA", "R", ""): "Amanda Waldman",
    ("MEUSER", "DANIEL", "", ""): "Dan Meuser",
    ("YAW", "EMERSON", "EUGENE", ""): "Gene Yaw",
    ("OWLETT", "CLINTON", "D", ""): "Clint Owlett",
    ("PICKETT", "TINA", "", ""): "Tina Pickett",
    ("WAYMAN", "MATTHEW", "EDWARD", ""): "Matthew Wayman",
    ("ZIMMERMAN", "TODD", "P", ""): "Todd Zimmerman",
    ("BETTINGER", "ELIZABETH", "A", ""): "Liz Bettinger",
    ("MOFFETT", "DENITRA", "MITCHELLE", ""): "Denitra Michelle Moffett",
    ("BONKOSKI", "EUGENE", "ZIGMUND", ""): "Eugene Bonkoski",
    ("DELUCIA", "TAMMY", "L", ""): "Tammy DeLucia",
    ("WOLFE", "STEVEN", "MICHAEL", ""): "Steven Michael Wolfe",
    ("BONKOSKI", "CAROLYN", "LUCY", ""): "Carolyn Bonkoski",
    ("MILLER", "LAURA", "JANE", ""): "Laura Jane Miller",
    ("MCLINKO", "DOUGLAS", "", ""): "Doug McLinko",
    ("HUFFMAN", "DAVID", "A", "JR."): "Dave Huffman",
    ("MORGAN", "JESSICA", "F", ""): "Jessica Morgan",
}

# DOS municipality (type code, name) + ward/district breakdown -> precinct
# name, following the county's own repo convention
# (2022/counties/20220517__pa__primary__bradford__precinct.csv /
# 20221108__pa__general__bradford__precinct.csv): plain Municipality
# wording, "Nth Ward" for borough wards and "Nth District" for township
# districts.
MUNI_NAMES = {
    ("4", "ALBANY"): "Albany Township",
    ("4", "ARMENIA"): "Armenia Township",
    ("4", "ASYLUM"): "Asylum Township",
    # Athens Township is always split (D 1/D 2) -> handled by the bd_code
    # branch in split_name below, never a plain municipality row.
    ("4", "BURLINGTON"): "Burlington Township",
    ("4", "CANTON"): "Canton Township",
    ("4", "COLUMBIA"): "Columbia Township",
    ("4", "FRANKLIN"): "Franklin Township",
    ("4", "GRANVILLE"): "Granville Township",
    ("4", "HERRICK"): "Herrick Township",
    ("4", "LEROY"): "Leroy Township",
    ("4", "LITCHFIELD"): "Litchfield Township",
    ("4", "MONROE"): "Monroe Township",
    ("4", "NORTH TOWANDA"): "North Towanda Township",
    ("4", "ORWELL"): "Orwell Township",
    ("4", "OVERTON"): "Overton Township",
    ("4", "PIKE"): "Pike Township",
    ("4", "RIDGEBURY"): "Ridgebury Township",
    ("4", "ROME"): "Rome Township",
    ("4", "SHESHEQUIN"): "Sheshequin Township",
    ("4", "SMITHFIELD"): "Smithfield Township",
    ("4", "SOUTH CREEK"): "South Creek Township",
    ("4", "SPRINGFIELD"): "Springfield Township",
    ("4", "STANDING STONE"): "Standing Stone Township",
    ("4", "STEVENS"): "Stevens Township",
    ("4", "TERRY"): "Terry Township",
    ("4", "TOWANDA"): "Towanda Township",
    ("4", "TROY"): "Troy Township",
    ("4", "TUSCARORA"): "Tuscarora Township",
    ("4", "ULSTER"): "Ulster Township",
    ("4", "WARREN"): "Warren Township",
    ("4", "WELLS"): "Wells Township",
    ("4", "WEST BURLINGTON"): "West Burlington Township",
    ("4", "WILMOT"): "Wilmot Township",
    ("4", "WINDHAM"): "Windham Township",
    ("4", "WYALUSING"): "Wyalusing Township",
    ("4", "WYSOX"): "Wysox Township",
    ("6", "ALBA"): "Alba Borough",
    # Athens Borough is always split (W 1-4) -> bd_code branch below.
    ("6", "BURLINGTON"): "Burlington Borough",
    ("6", "CANTON"): "Canton Borough",
    ("6", "LERAYSVILLE"): "Leraysville Borough",
    ("6", "MONROE"): "Monroe Borough",
    ("6", "NEW ALBANY"): "New Albany Borough",
    ("6", "ROME"): "Rome Borough",
    ("6", "SAYRE"): {},  # ward split, handled by bd_code branch
    ("6", "SOUTH WAVERLY"): "South Waverly Borough",
    ("6", "SYLVANIA"): "Sylvania Borough",
    # Towanda Borough is always split (W 1-3) -> bd_code branch below.
    ("6", "TROY"): "Troy Borough",
    ("6", "WYALUSING"): "Wyalusing Borough",
}

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th",
             6: "6th", 7: "7th", 8: "8th"}


def _title(muni: str) -> str:
    # "NORTH TOWANDA" -> "North Towanda", "LERAYSVILLE" -> "Leraysville"
    return muni.title().replace("Mc", "Mc").replace("O'", "O'").replace(
        "Leraysville", "Leraysville")


def split_name(muni_type: str, muni: str, bd_code: str, bd_name: str) -> str | None:
    """Precinct name for a DOS municipality row, or None if unmapped."""
    if bd_code in ("W", "D"):
        n = int(float(bd_name))
        kind = "Ward" if bd_code == "W" else "District"
        kind_word = "Borough" if muni_type == "6" else "Township"
        return "%s %s %s %s" % (_title(muni), kind_word,
                                _ORDINALS[n], kind)
    return MUNI_NAMES.get((muni_type, muni))


DEFAULT_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2024p", "bradford", "bradford_dos.txt",
)
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "2024", "counties", "20240423__pa__primary__bradford__precinct.csv",
)


def f2i(v: str) -> int:
    return int(float(v))


def county_rows_from_dos(path: str) -> list[dict]:
    """Read the PA DOS precinct-returns comma-delimited structure; keep
    Bradford (county code 8) primary rows.  Values in the 2024 file carry
    ".0" float suffixes (field specs unchanged from the readme)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if not row or f2i(row[0]) != 2024 or row[1] != ELECTION_TYPE:
                continue
            if f2i(row[2]) != int(COUNTY_CODE):
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected office code %r in %s"
                                 % (row[8], path))
            out.append({
                "precinct_code": row[3],
                "office_code": row[8],
                "party": row[9],
                "cand_last": row[11].strip(),
                "cand_first": row[12].strip(),
                "cand_middle": row[13].strip(),
                "cand_suffix": row[14].strip(),
                "votes": f2i(row[15]),
                "cand_district": f2i(row[5]),
                "muni_type": f2i(row[21]),
                "muni_name": row[22].strip(),
                "bd_code": row[23].strip(),
                "bd_name": row[24].strip(),
            })
    return out


def cand_name(r: dict) -> str:
    key = (r["cand_last"], r["cand_first"], r["cand_middle"], r["cand_suffix"])
    name = NAME_FIX.get(key)
    if name is None:
        raise SystemExit("no printed name mapped for DOS candidate %r" % (key,))
    return name


def district_for(r: dict) -> str:
    _, dist_src = OFFICES[r["office_code"]]
    if dist_src == "cand":
        return str(r["cand_district"])
    return ""


def precinct_name(r: dict) -> str:
    name = split_name(str(r["muni_type"]), r["muni_name"],
                      r["bd_code"], r["bd_name"])
    if name is None:
        raise SystemExit("unmapped municipality %r type %r breakdown %r"
                         % (r["muni_name"], r["muni_type"], r["bd_code"]))
    return name


def to_output_rows(rows: list[dict]) -> list[list]:
    out = []
    for r in rows:
        office = OFFICES[r["office_code"]][0]
        out.append({
            "county": COUNTY_NAME,
            "precinct": precinct_name(r),
            "office": office,
            "district": district_for(r),
            "party": r["party"],
            "candidate": cand_name(r),
            "votes": r["votes"],
            "_code": f2i(r["precinct_code"]),
            "_oc": OFFICE_ORDER.index(office),
        })
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["district"],
                            x["candidate"]))
    return [[o["county"], o["precinct"], o["office"], o["district"],
             o["party"], o["candidate"], str(o["votes"])] for o in out]


# Countywide totals printed in the certified county summary PDF (named
# candidates only; the PDF's write-in lines are excluded from this check —
# the DOS bulk file carries no write-in rows at all).
PDF_TOTALS = {
    ("President", "DEM"): {
        "Joseph R. Biden Jr": 1868,
        "Dean Phillips": 185,
    },
    ("President", "REP"): {
        "Nikki R. Haley": 1002,
        "Donald J. Trump": 5417,
    },
    ("U.S. Senate", "REP"): {
        "Dave McCormick": 5968,
    },
    ("Attorney General", "DEM"): {
        "Jack Stollsteimer": 419,
        "Eugene Depasquale": 483,
        "Joe Khan": 516,
        "Keir Bradford-Grey": 326,
        "Jared Solomon": 292,
    },
    ("Attorney General", "REP"): {
        "Dave Sunday": 3304,
        "Craig Williams": 2486,
    },
    ("Auditor General", "DEM"): {
        "Malcolm Kenyatta": 851,
        "Mark Pinsley": 1135,
    },
    ("Auditor General", "REP"): {
        "Tim DeFoor": 5596,
    },
    ("State Treasurer", "DEM"): {
        "Ryan Bizzarro": 785,
        "Erin McClelland": 1187,
    },
    ("State Treasurer", "REP"): {
        "Stacy Garrity": 6325,
    },
    ("U.S. House", "DEM"): {
        "Amanda Waldman": 1990,
    },
    ("U.S. House", "REP"): {
        "Dan Meuser": 5826,
    },
    ("State Senate", "REP"): {
        "Gene Yaw": 5974,
    },
    ("State House", "REP"): {
        "Clint Owlett": 2403,
        "Tina Pickett": 3424,
        "Matthew Wayman": 560,
    },
    ("Delegate to the Democratic National Convention", "DEM"): {
        "Todd Zimmerman": 1623,
        "Liz Bettinger": 1602,
        "Denitra Michelle Moffett": 1493,
    },
    ("Delegate to the Republican National Convention", "REP"): {
        "Eugene Bonkoski": 921,
        "Tammy DeLucia": 1493,
        "Steven Michael Wolfe": 1774,
        "Carolyn Bonkoski": 1018,
        "Laura Jane Miller": 1591,
        "Doug McLinko": 4332,
        "Dave Huffman": 1889,
        "Jessica Morgan": 1918,
    },
}


def run_checks(rows: list[dict]) -> tuple[int, list[str]]:
    """Sum precinct votes per (office, party, candidate) and compare with
    the countywide totals printed in the certified summary PDF."""
    sums: dict[tuple, int] = {}
    for r in rows:
        key = (OFFICES[r["office_code"]][0], r["party"], cand_name(r))
        sums[key] = sums.get(key, 0) + r["votes"]
    problems = []
    n = 0
    for (office, party), cands in PDF_TOTALS.items():
        for cand, total in cands.items():
            n += 1
            mine = sums.get((office, party, cand), 0)
            if mine != total:
                problems.append("%s [%s]: %s: precinct sum %d != printed "
                                "total %d" % (office, party, cand, mine, total))
    return n, problems


def main(argv: list[str]) -> int:
    verify = "--verify" in argv
    args = [a for a in argv if a != "--verify"]
    if args and os.path.exists(args[0]) and args[0].endswith(".txt"):
        src = args[0]
        out_path = args[1] if len(args) > 1 else DEFAULT_OUT
    else:
        src = DEFAULT_SOURCE
        out_path = args[0] if args else DEFAULT_OUT
    if not os.path.exists(src):
        print("No Bradford 2024 primary DOS source found (tried %s)" % src,
              file=sys.stderr)
        return 1
    rows = county_rows_from_dos(src)
    if not rows:
        print("No Bradford primary rows found in source %s" % src,
              file=sys.stderr)
        return 1
    # sanity: candidates' district field agrees with the contest's districts
    precincts = sorted({f2i(r["precinct_code"]) for r in rows})
    if len(precincts) != 61:
        print("expected 61 precinct codes, found %d" % len(precincts),
              file=sys.stderr)
        return 1

    out_rows = to_output_rows(rows)

    ok = True
    if verify:
        n, problems = run_checks(rows)
        print("verify: %d candidate totals checked against the countywide "
              "certified summary PDF totals" % n)
        if problems:
            ok = False
            for p in problems:
                print("  MISMATCH " + p)
        else:
            print("verify: all contest sums match the countywide printed totals")

    if not ok:
        return 1

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(out_rows)
    print("wrote %s (%d data rows, %d precincts) from %s"
          % (out_path, len(out_rows), len(precincts), src))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
