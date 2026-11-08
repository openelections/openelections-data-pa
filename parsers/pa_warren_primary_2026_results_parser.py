#!/usr/bin/env python3
"""Warren County, PA 2026 Primary — precinct-level + countywide results parser.

Source situation (documented):
  * The county's per-precinct source "Warren County 2026 Precinct Level
    Results.pdf" (232 pages) is a SCAN of 33 concatenated "Election
    Summary Report / Closed Primary" reports — one 7-page report per
    precinct (no extractable text; pdftotext yields nothing).  Sampled
    pages (rendered at 150 dpi and read) match the DOS bulk rows
    exactly, e.g. chunk 1 "Warren Central" Gov DEM Shapiro 35/24/0
    (total 59) equals DOS code 300's Shapiro; chunk 2 "Warren North"
    Gov DEM Shapiro 28/40/0 (68) equals DOS code 320's; chunk 3 "Warren
    South" Gov DEM Shapiro 18/16/0 (34) equals DOS code 330's.  Note
    the PDF concatenates its precinct reports in the county's own order
    (chunk order: Warren Central, Warren North, Warren South, ...) which
    is NOT the DOS ascending-code order, so a full-OCR conversion would
    need to map by each report's "Summary for: ... <precinct>" heading,
    not by page position.
  * OCR cost: the whole PDF needs the slow local PaddleOCR-VL scan
    stack (/tmp/paddleenv; ~7 min model load plus tens of seconds per
    page, sequential only -> on the order of hours for 232 pages), and
    the hosted PaddleOCR API (work2023p/ocr_api.py) was down again
    (Connection reset on submit, 2026-11-07).  Per the 2026-primary
    brief only sample pages were rendered and read; the precinct values
    were NOT extracted from the scan (documented later improvement: a
    full-OCR pass would add per-precinct write-ins, Registered Voters /
    Ballots Cast, ED/Mail/Provisional breakdowns and the county
    committee contests to the precinct file).
  * The countywide source "Warren County Summary Official Results_2.pdf"
    (28 pages, same vendor family — "Election Summary Report / Closed
    Primary", Official Results, generated 6/8/2026 1:27 PM) IS
    text-based; pdftotext -layout extract kept at
    work2026p/warren/summary.txt.  It prints countywide per-contest
    candidate tables with Election Day / Mail-In / Provisional / Total
    columns and named write-in candidate rows; it feeds the countywide
    CSV.

  Precinct-level figures therefore come from the PA Department of State
  official certified precinct-returns bulk file for the 2026 primary
  (DOS FALLBACK — the county's own per-precinct source is an
  unsearchable 232-page image report):
      PA DOS 2026 General Primary Certified Precinct Returns Statewide.txt
  (ASCII comma-delimited, quoted fields; election type "P"; Warren =
  county code 62; 297 rows = 33 tabulation units x 9 named-candidate
  rows).  A verbatim Warren slice is kept at
  work2026p/dos/warren__dos_precinctreturns.txt.

Verification (this parser):
  * internal: county-summary rows — election_day + mail + provisional
    == total on every candidate row.
  * DOS per-candidate precinct-row sums == the county's own countywide
    summary named-candidate totals AND the statewide county anchor
    2026/20260519__pa__primary__county.csv, digit for digit — all 9
    named candidates (Gov: Josh Shapiro 1,783 / Stacy Garrity 2,376;
    Lt Gov: Austin Davis 1,715, John Ventre 640, Jason Richey 1,794;
    U.S. House 15: Ray Bilger 1,677 / Glenn Gt Thompson 2,403; State
    House 65: Destiny Howard 1,745 / Kathy L Rapp 2,401).

Consequences (all inherent to the sources, not parsing losses):
  * The DOS bulk file has no vote breakdown -> 7-column precinct
    output header, and no RV/BC rows.
  * No per-precinct write-in rows in the DOS bulk file: the county
    summary's aggregate "Write-in" lines (Gov DEM 9 / REP 97, Lt Gov
    DEM 11 / REP 26, U.S. House DEM 7 / REP 38, State House DEM 8 /
    REP 72) cannot be assigned to precincts; per the no-invention rule
    no Write-In rows are emitted at precinct level.  Write-ins ARE in
    the countywide CSV, exactly as the county summary prints them:
    a "Write-In Totals" row plus the individually named write-in
    candidate rows (Centre 2026 county-CSV precedent).
  * State Committee (Member of the Democratic State Committee: Sarah
    Parmeter 1,716; Member of the Republican State Committee: Heidi
    Villella 1,979, Ash Khare 1,777) is in the county summary and the
    anchor but ABSENT from the DOS precinct bulk file -> countywide
    rows only (Clarion 2026 precedent).
  * The 66 "Member of Warren County (Democratic/Republican) Committee
    <unit>" contests are county-committee races, printed only in the
    county's PDFs (countywide aggregates in the summary, per-precinct
    rows only in the scan); neither CSV carries them (no 2026
    county-level CSV does).
  * No State Senate primary on Warren's 2026 ballot (no STS rows in
    the DOS bulk file and no State Senate section in the county
    summary).
  * Undervote quirk, kept out of the schema: the summary's "Total
    Votes" sanity line (e.g. Gov DEM 1,791) can be one below the
    candidate+write-in sum (1,792 because Times Cast counts a
    blank/undervoted ballot); the parser records only the printed
    candidate rows.

Usage:
    uv run python parsers/pa_warren_primary_2026_results_parser.py \
        <dos_txt> <precinct_out.csv>             # precinct CSV (DOS slice)
    uv run python parsers/pa_warren_primary_2026_results_parser.py \
        --county <summary_txt> <county_out.csv>  # countywide CSV
    ... with --verify in either mode.

Defaults: <dos_txt> = work2026p/dos/warren__dos_precinctreturns.txt,
<summary_txt> = work2026p/warren/summary.txt (pdftotext -layout extract
of the countywide summary PDF).
"""
from __future__ import annotations

import csv
import os
import re
import sys

COUNTY_NAME = "Warren"
COUNTY_CODE = "62"
ELECTION_TYPE = "P"
YEAR = "2026"

PRECINCT_HEADER = ["county", "precinct", "office", "district", "party",
                   "candidate", "votes"]
COUNTY_HEADER = ["county", "office", "district", "party", "candidate",
                 "votes", "election_day", "mail", "provisional"]

OFFICES = {  # DOS office code -> standard office
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STS": "State Senate",
    "STH": "State House",
}
OFFICE_ORDER = ["Governor", "Lieutenant Governor", "U.S. House",
                "State Senate", "State House"]

NAME_FIX = {  # DOS candidate (Last, First, Middle-suffix) -> printed name
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE"): "Stacy Garrity",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("VENTRE", "JOHN", ""): "John Ventre",
    ("RICHEY", "JASON", "L"): "Jason Richey",
    ("BILGER", "RAY", ""): "Ray Bilger",
    # repo-standard "Glenn Gt Thompson" (Clarion 2026 precinct file, CD-15),
    # not the full DOS form "Glenn William Thompson Jr"
    ("THOMPSON", "GLENN", "WILLIAM"): "Glenn Gt Thompson",
    ("HOWARD", "DESTINY", "KAY PATRICIA"): "Destiny Howard",
    ("RAPP", "KATHY", "L"): "Kathy L Rapp",
}

# DOS precinct code -> precinct name.  The county prints its 33 tabulation
# units under these names (identical wording in the county committee ballot
# blocks of the 2026 summary and the 20251104 county CSV); DOS gives
# NAME + optional qualifier/split (CONEWANGO P1, WARREN W CENTRAL), which
# maps 1:1 by ascending code.
# Units 255/270 both print "SUGAR GROVE" in the DOS file: 255 = Sugar Grove
# Borough, 270 = Sugar Grove Township (the county's ballot order puts the
# Borough's committee unit ahead of the Township's, matching both the
# county committee naming and the 2025 county precinct CSV).
PRECINCT_NAMES = {
    "10": "Bear Lake Borough",
    "20": "Brokenstraw Township",
    "30": "Cherry Grove Township",
    "40": "Clarendon Borough",
    "50": "Columbus Township",
    "60": "Conewango #1 Twp",
    "70": "Conewango #2 Twp",
    "80": "Deerfield Township",
    "90": "Eldred Township",
    "100": "Elk Township",
    "110": "Farmington Township",
    "125": "Freehold Township",
    "140": "Glade Township",
    "150": "Limestone Township",
    "165": "Mead Township",
    "180": "Pine Grove Township",
    "195": "Pittsfield Township",
    "210": "Pleasant Township",
    "220": "Sheffield Township",
    "230": "Southwest Township",
    "240": "Spring Creek Township",
    "255": "Sugar Grove Borough",
    "270": "Sugar Grove Township",
    "280": "Tidioute Borough",
    "290": "Triumph Township",
    "300": "Warren Central",
    "310": "Warren East",
    "320": "Warren North",
    "330": "Warren South",
    "340": "Warren Southeast",
    "350": "Warren West",
    "360": "Watson Township",
    "370": "Youngsville Borough",
}

DEFAULT_DOS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2026p", "dos", "warren__dos_precinctreturns.txt")
DEFAULT_SUMMARY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2026p", "warren", "summary.txt")

# summary heading prefix -> standard office (U.S. House / State House draw
# their district number from the heading text)
SUMMARY_CONTESTS = [
    ("governor", "Governor"),
    ("lieutenant governor", "Lieutenant Governor"),
    ("representative in congress", "U.S. House"),
    ("representative in the general assembly", "State House"),
    ("member of the democratic state committee",
     "Member of Democratic State Committee"),
    ("member of the republican state committee",
     "Member of Republican State Committee"),
]
COMMITTEE_PREFIX = "member of warren county"  # county-committee: skipped

CAND_LINE = re.compile(
    r"^(?P<name>.+?)\s+(?:(?P<party>DEM|REP|WRITE-IN)\s+)?"
    r"(?P<ed>[\d,]+)\s+(?P<mail>[\d,]+)\s+(?P<prov>[\d,]+)\s+"
    r"(?P<total>[\d,]+)(?:\s+[\d.]+%?)?\s*$")

# printed ALL-CAPS named candidate -> standard printed name
SUMMARY_NAME_FIX = {
    "JOSH SHAPIRO": "Josh Shapiro",
    "STACY GARRITY": "Stacy Garrity",
    "AUSTIN DAVIS": "Austin Davis",
    "JOHN VENTRE": "John Ventre",
    "JASON RICHEY": "Jason Richey",
    "RAY BILGER": "Ray Bilger",
    "GLENN GT THOMPSON": "Glenn Gt Thompson",
    "DESTINY HOWARD": "Destiny Howard",
    "KATHY L RAPP": "Kathy L Rapp",
    "SARAH PARMETER": "Sarah Parmeter",
    "HEIDI VILLELLA": "Heidi Villella",
    "ASH KHARE": "Ash Khare",
}


def pm_(party: str) -> str:
    """DEMOCRATIC/DEM/DEMx -> DEM; REPUBLICAN/REP -> REP."""
    p = party.upper()
    return {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}.get(p, party)


# ------------------------------------------------------------------ DOS


def dos_rows(path: str) -> list[dict]:
    """Warren (county 62, election type 'P') rows of the DOS prcres file."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < 26 or row[:2] != [YEAR, ELECTION_TYPE]:
                continue
            if row[2] != COUNTY_CODE:
                continue
            if row[8] not in OFFICES:
                raise SystemExit("unexpected DOS office code %r in %s"
                                 % (row[8], path))
            key = (row[11].strip(), row[12].strip(), row[13].strip())
            if key not in NAME_FIX:
                raise SystemExit("unknown DOS candidate %r in %s"
                                 % (key, path))
            out.append({
                "precinct_code": row[3],
                "oc": row[8],
                "office": OFFICES[row[8]],
                "party": row[9],
                "name": NAME_FIX[key],
                "votes": int(row[15]),
                "uscd": row[18],
                "shd": row[20],
                "ballot_no": int(row[7]),
            })
    return out


def district_for(r: dict) -> str:
    """'': statewide; CD/HD numbers from the DOS district fields."""
    office = r["office"]
    if office in ("Governor", "Lieutenant Governor"):
        return ""
    if office == "U.S. House":
        return str(int(r["uscd"]))
    if office == "State Senate":
        return ""  # no STS rows for Warren in 2026
    return str(int(r["shd"]))  # State House


def build_precinct_rows(rows: list[dict]) -> list[list]:
    """Output order: precinct code asc, contest rank, ballot position."""
    out = []
    for r in rows:
        out.append({
            "precinct": PRECINCT_NAMES[r["precinct_code"]],
            "office": r["office"],
            "district": district_for(r),
            "party": r["party"],
            "candidate": r["name"],
            "votes": r["votes"],
            "_code": int(r["precinct_code"]),
            "_oc": OFFICE_ORDER.index(r["office"]),
            "_bn": r["ballot_no"],
        })
    out.sort(key=lambda x: (x["_code"], x["_oc"], x["_bn"], x["candidate"]))
    return [[COUNTY_NAME, o["precinct"], o["office"], o["district"],
             o["party"], o["candidate"], str(o["votes"])] for o in out]


def dos_sums(rows: list[dict]) -> dict[tuple, int]:
    sums: dict[tuple, int] = {}
    for r in rows:
        key = (r["office"], district_for(r), r["party"], r["name"])
        sums[key] = sums.get(key, 0) + r["votes"]
    return sums


# ------------------------------------------------------- countywide summary


def split_heading(heading: str) -> str | None:
    """Base name of a '(DEM) (Vote for 1)' contest heading, else None."""
    base = heading.split("(Vote for")[0].rstrip()
    if "(Vote for" not in heading:
        return None
    return re.sub(r"\s*\((DEM|REP)\)\s*$", "", base).strip()


def match_contest(heading: str):
    """Heading -> (office, district, heading party tag) or None."""
    if COMMITTEE_PREFIX in heading.lower():
        return None
    base = split_heading(heading)
    if base is None:
        return None
    mp = re.search(r"\((DEM|REP)\)\s*\(?$", heading[: heading.index("(Vote")])
    heading_party = mp.group(1) if mp else ""
    for token, office in SUMMARY_CONTESTS:
        if base.lower().startswith(token):
            district = ""
            if office in ("U.S. House", "State House"):
                dm = re.search(r"(\d+)(?:st|nd|rd|th)\s+District$", base)
                district = dm.group(1) if dm else district
            return office, district, heading_party
    return None


NON_DATA_PREFIXES = ("times cast", "total votes", "candidate", "elector group",
                     "voters cast:", "election day")


def parse_summary(text: str) -> tuple[list[dict], list[str]]:
    """Countywide candidate rows from the summary text extract.

    Every candidate row carries the contest's ballot party (DEM/REP).  A
    "Write-in ..." aggregate row is normalized to candidate
    "Write-In Totals" (single totals row per contest, per the brief's
    write-in naming rule); the individually named WRITE-IN candidate
    rows are kept exactly as printed.
    """
    rows: list[dict] = []
    problems: list[str] = []
    contest: tuple[str, str, str] | None = None  # (office, district, party)
    lines = [ln.rstrip() for ln in text.splitlines()]
    i = 0
    while i < len(lines):
        raw = lines[i].strip()
        i += 1
        if not raw or raw.startswith("Page: "):
            continue
        heading = raw
        # contest headings may wrap ("... Warren Central (DEM)\n(Vote for 2)")
        while "(Vote for" not in heading and i < len(lines):
            nxt = lines[i].strip()
            if not nxt or nxt.startswith("Page: "):
                break
            if re.match(r"^\(?\s*Vote\s+for\b", nxt):
                heading += " " + nxt
                i += 1
            break
        if "(Vote for" in heading and "—" not in heading:
            match = match_contest(" ".join(heading.split()))
            if match:
                contest = match
            else:
                contest = None
                if COMMITTEE_prefix_check(heading):
                    continue
                problems.append("unrecognized contest heading %r" % heading)
            continue

        if contest is None:
            continue
        low = raw.lower()
        if any(low.startswith(p) for p in NON_DATA_PREFIXES) or \
                low in ("dem", "rep"):
            continue
        m = CAND_LINE.match(" ".join(raw.split()))
        if not m:
            problems.append("unparsed data line in %r contest: %r"
                            % (contest[0], raw))
            continue
        name = clean_name(m.group("name"))
        vals = (int(m.group("ed").replace(",", "")),
                int(m.group("mail").replace(",", "")),
                int(m.group("prov").replace(",", "")),
                int(m.group("total").replace(",", "")))
        rows.append({
            "office": contest[0], "district": contest[1],
            "party": contest[2] or m.group("party"),
            "candidate": name,
            "ed": vals[0], "mail": vals[1], "prov": vals[2], "total": vals[3],
            "writein": name == "Write-In Totals"
            or m.group("party") == "WRITE-IN",
        })
    return rows, problems


def COMMITTEE_prefix_check(heading: str) -> bool:
    return heading.lower().startswith(COMMITTEE_PREFIX)


def clean_name(name: str) -> str:
    name = " ".join(name.replace("&#x27;", "'").replace("\\n", " ").split())
    if name.lower() == "write-in":
        return "Write-In Totals"
    return SUMMARY_NAME_FIX.get(name.upper(), name)


# -------------------------------------------------------------- verification


def verify_summary(rows: list[dict]) -> tuple[int, list[str]]:
    problems = []
    for r in rows:
        if r["ed"] + r["mail"] + r["prov"] != r["total"]:
            problems.append("summary %s [%s] %s: ED %d + mail %d + prov %d "
                            "!= total %d"
                            % (r["office"], r["party"], r["candidate"],
                               r["ed"], r["mail"], r["prov"], r["total"]))
    return len(rows), problems


def verify_vs_summary(dos: list[dict], summary_rows: list[dict],
                      problems: list[str]) -> int:
    """DOS precinct sums == the summary's named-candidate totals."""
    sums = dos_sums(dos)
    covered = set()
    n = 0
    for r in summary_rows:
        if r["writein"]:
            continue  # DOS has no write-in rows
        key = (r["office"], r["district"], r["party"], r["candidate"])
        covered.add(key)
        mine = sums.get(key)
        if mine is None:
            continue  # State Committee — absent from the DOS bulk file
        n += 1
        if mine != r["total"]:
            problems.append("%s [%s] %s: DOS precinct sum %d != summary %d"
                            % (key[0], key[1], key[3], mine, r["total"]))
    for key in sorted(sums):
        if key not in covered:
            problems.append("DOS contest row %s has no county-summary "
                            "counterpart" % (key,))
    return n


def anchor_load(problems: list[str]) -> dict[tuple, int]:
    anchor_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "2026", "20260519__pa__primary__county.csv")
    if not os.path.exists(anchor_path):
        problems.append("2026 anchor CSV not found at %s" % anchor_path)
        return {}
    anchor: dict[tuple, int] = {}
    with open(anchor_path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].upper() != COUNTY_NAME.upper() or len(row) < 6:
                continue
            # anchor district "Warren County (District 62)" on State
            # Committee rows: those offices are countywide -> key by office
            district = "" if row[1].startswith("Member of") else row[2]
            key = (row[1], district, pm_(row[3]), row[4].upper())
            anchor[key] = anchor.get(key, 0) + int(row[5])
    return anchor


def anchor_check(sums: dict[tuple, int], anchor: dict[tuple, int],
                 label: str, problems: list[str]) -> int:
    n = 0
    for (office, district, party, cand), v in sorted(sums.items()):
        key = (office, district, pm_(party), cand.upper())
        tot = anchor.get(key)
        n += 1
        if tot is None:
            problems.append("%s: anchor has no counterpart for %s [%s] %s [%s]"
                            % (label, office, district, party, cand))
        elif tot != v:
            problems.append("%s: %s [%s] %s: sum %d != anchor %d"
                            % (label, office, district, cand, v, tot))
    return n


def precinct_sums_of_rows(precinct_rows: list[list]) -> dict[tuple, int]:
    sums: dict[tuple, int] = {}
    for row in precinct_rows:
        key = (row[2], row[3], row[4], row[5])
        sums[key] = sums.get(key, 0) + int(row[6])
    return sums


def named_sums_of_summary(summary_rows: list[dict]) -> dict[tuple, int]:
    sums: dict[tuple, int] = {}
    for r in summary_rows:
        if r["writein"]:
            continue
        key = (r["office"], r["district"], r["party"], r["candidate"])
        sums[key] = sums.get(key, 0) + r["total"]
    return sums


def main(argv: list[str]) -> int:
    verify = "--verify" in argv
    argv = [a for a in argv if a != "--verify"]
    county_only = "--county" in argv
    argv = [a for a in argv if a != "--county"]
    src = argv[0] if len(argv) > 0 else (
        DEFAULT_SUMMARY if county_only else DEFAULT_DOS)
    out_csv = argv[1] if len(argv) > 1 else None
    summary_txt = argv[2] if len(argv) > 2 else DEFAULT_SUMMARY
    problems: list[str] = []

    if not os.path.exists(summary_txt):
        print("summary text not found (tried %s)" % summary_txt,
              file=sys.stderr)
        return 1
    summary_rows, sprobs = parse_summary(
        open(summary_txt, encoding="utf-8", errors="replace").read())
    n_srows, sbreak = verify_summary(summary_rows)
    problems += sbreak
    for p in sprobs:
        print("summary-parse problem: %s" % p, file=sys.stderr)

    if county_only:
        if out_csv:
            write_county_csv(out_csv, summary_rows)
            print("wrote %s (%d data rows from the county summary)"
                  % (out_csv, len(summary_rows)))
        if not verify:
            return 0
        anchor = anchor_load(problems)
        n_c = anchor_check(named_sums_of_summary(summary_rows), anchor,
                           "county csv", problems)
        print("verify: %d county-CSV named-candidate totals checked against "
              "the anchor (%d anchor rows for Warren)" % (n_c, len(anchor)))
        if problems:
            for p in problems:
                print("  MISMATCH " + p)
            return 1
        print("verify: county CSV checked; no mismatches")
        return 0

    # ---- precinct mode (DOS slice)
    if not os.path.exists(src):
        print("No Warren 2026 primary DOS slice (tried %s)" % src,
              file=sys.stderr)
        return 1
    dos = dos_rows(src)
    if not dos:
        print("No Warren primary rows in %s" % src, file=sys.stderr)
        return 1
    missing = sorted({r["precinct_code"] for r in dos} - set(PRECINCT_NAMES))
    if missing:
        print("Unknown DOS precinct codes: %s" % missing, file=sys.stderr)
        return 1
    n_units = len({r["precinct_code"] for r in dos})
    out_rows = build_precinct_rows(dos)

    n_s = verify_vs_summary(dos, summary_rows, problems)
    if not verify:
        # the summary cross-check runs the sums the parser needs anyway;
        # the anchor requires --verify (explicit statewide check)
        pass

    if verify:
        anchor = anchor_load(problems)
        n_p = anchor_check(precinct_sums_of_rows(out_rows), anchor,
                           "precinct csv", problems)
        n_a = anchor_check(named_sums_of_summary(summary_rows), anchor,
                           "county csv", problems)
        print("verify: %d candidate totals checked against the anchor "
              "(%d anchor rows for Warren)" % (max(n_p, n_a), len(anchor)))
    else:
        n_p = n_a = 0

    print("verify: %d countywide summary rows parsed (%d rows breakdown-"
          "checked)" % (n_srows, n_srows))
    print("verify: %d named-candidate DOS sums == county summary totals "
          "(%d tabulation units)" % (n_s, n_units))

    if problems:
        print("MISMATCHES:")
        for p in problems:
            print("  " + p)
        return 1
    print("verify: all checks pass (county summary%s)"
          % (" + anchor" if verify else ""))

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(PRECINCT_HEADER)
            w.writerows(out_rows)
        print("wrote %s (%d data rows)" % (out_csv, len(out_rows)))
    return 0


def write_county_csv(out_csv: str, rows: list[dict]) -> None:
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COUNTY_HEADER)
        for r in rows:
            w.writerow([COUNTY_NAME, r["office"], r["district"], r["party"],
                        r["candidate"], r["total"], r["ed"], r["mail"],
                        r["prov"]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
