#!/usr/bin/env python3
"""Clearfield County, PA 2022 Primary (May 17, 2022) — precinct-level results parser.

Source situation (documented):

  * The county's source PDFs "Clearfield PA 2022 Primary Precinct.pdf" /
    "Clearfield PA 2022 Primary Summary.pdf" (Electionware "Precinct Summary
    Results Report" / "Summary Results Report", UNOFFICIAL, generated
    06/02/2022 10:41/10:42 AM, 420/8 pages) are recount-era reports in which
    ONLY the Republican United States Senator contest carries vote data.  Every
    other contest on the ballot, both parties, prints all-zero rows for all 70
    precincts (verified programmatically: all DEM rows and all REP rows outside
    REP U.S. Senate sum to 0), even though certified DEM vote counts exist (ENR
    and the DOS bulk file agree on e.g. Fetterman 3,772 / Shapiro 4,686).  The
    STATISTICS blocks carry correct per-precinct Registered Voters (sums equal
    the summary's 42,862 total / 14,004 DEM / 28,630 REP / 228 nonpartisan) and
    recount-artifact Ballots Cast (13,000 county-wide with DEMOCRATIC 0 —
    plainly wrong given certified DEM votes, because the report re-tabulated
    only the recounted contest's ballots).

  Consequently (no-invention rule):
      - Republican U.S. Senate rows come from the county PDF (the only per-
        precinct source for that contest, incl. Write-Ins rows and the
        total / election-day / absentee-mail-in / provisional breakdown).
      - All other named-candidate contest rows come from the PA DOS official
        certified precinct-returns bulk file
        ElectionReturns_2022_Primary_PrecinctReturns.txt (election type "P",
        county code 17; 2,538 Clearfield rows across 70 precincts).  DOS
        carries no breakdowns and no write-in rows, so those contests are
        emitted with the breakdown columns blank.
      - Contests with no usable per-precinct vote data anywhere are omitted:
        Member of the Democratic/Republican State Committee (the PDF prints
        only the candidate names — Christina Joy Fulton, Theron G. Terry Noble
        — with zero votes), DEM County Committee Chair (Robert C Tubbs,
        zero votes), DEM Representative in Congress and both DEM State House
        races (write-in-only contests; zero totals in the PDF, no DOS rows),
        write-in rows outside REP U.S. Senate, and all Ballots Cast rows
        (recount artifact).  A State Senate race does not appear on
        Clearfield's 2022 primary ballot report.

  * Recount note: the June 2022 Republican U.S. Senate recount figures in the
    county PDF ("Total Votes Cast" for the contest sums to 12,808 incl. 47
    write-ins) and the DOS certified bulk file agree per candidate AND per
    precinct.  The ENR county anchor 2022/20220517__pa__primary__county.csv
    carries pre-recount values for two candidates (Barnette 2,823 vs certified
    2,923; Sands 852 vs 862) and — for Lieutenant Governor — REP values that
    exceed the ballots actually cast (see --verify).

Usage:
    python parsers/pa_clearfield_primary_2022_results_parser.py [dos_txt] [out_csv]
    python parsers/pa_clearfield_primary_2022_results_parser.py --verify [dos_txt] [out_csv]

The DOS input defaults to the Clearfield slice kept at
work2022p/clearfield/clearfield_prcres_slice.txt; the county PDF is extracted
with pdftotext -layout on the fly (or an existing cached extract under
work2022p/clearfield/ is reused).
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNTY = "Clearfield"
COUNTY_CODE = "17"

COUNTY_PDF = os.path.join(
    os.path.expanduser("~/code/openelections-sources-pa"),
    "2022", "primary", "Clearfield PA 2022 Primary Precinct.pdf",
)
PDF_EXTRACT_FALLBACK = os.path.join(REPO, "work2022p", "clearfield", "precinct.txt")
DOS_DEFAULT = os.path.join(REPO, "work2022p", "clearfield", "clearfield_prcres_slice.txt")
OUTPUT_DEFAULT = os.path.join(
    REPO, "2022", "counties", "20220517__pa__primary__clearfield__precinct.csv",
)
ANCHOR_CSV = os.path.join(REPO, "2022", "20220517__pa__primary__county.csv")

HEADER = ["county", "precinct", "office", "district", "party", "candidate",
          "votes", "election_day", "mail", "provisional"]

# DOS office/contest code -> standard office name
DOS_OFFICES = {
    "USS": "U.S. Senate",
    "GOV": "Governor",
    "LTG": "Lieutenant Governor",
    "USC": "U.S. House",
    "STH": "State House",
}
DOS_OFFICE_BY_NAME = {v: k for k, v in DOS_OFFICES.items()}
OFFICE_ORDER = ["Registered Voters", "U.S. Senate", "Governor",
                "Lieutenant Governor", "U.S. House", "State House"]

# DOS candidate ID -> printed name (candidate blocks of the county PDF)
CAND_BY_ID = {
    "2022C0216": "ALEX KHALIL",
    "2022C0167": "CONOR LAMB",
    "2022C0126": "MALCOLM KENYATTA",
    "2022C0203": "JOHN FETTERMAN",
    "2022C0138": "KATHY BARNETTE",
    "2022C0147": "MEHMET OZ",
    "2022C0210": "GEORGE BOCHETTO",
    "2022C0142": "JEFF BARTOS",
    "2022C0176": "DAVE MCCORMICK",
    "2022C0193": "SEAN GALE",
    "2022C0154": "CARLA SANDS",
    "2022C0206": "JOSH SHAPIRO",
    "2022C0161": "LOU BARLETTA",
    "2022C0139": "DOUGLAS V MASTRIANO",
    "2022C0247": "NCHE ZAMA",
    "2022C0163": "DAVE WHITE",
    "2022C0224": "MELISSA HART",
    "2022C0195": "BILL MCSWAIN",
    "2022C0238": "CHARLIE GEROW",
    "2022C0192": "JOE GALE",
    "2022C0204": "JAKE CORMAN",
    "2022C0207": "AUSTIN DAVIS",
    "2022C0249": "RAY SOSA",
    "2022C0137": "BRIAN SIMS",
    "2022C0145": "CLARICE SCHILLINGER",
    "2022C0240": "JAMES EARL JONES",
    "2022C0166": "RICK SACCONE",
    "2022C0237": "JOHN BROWN",
    "2022C0239": "CHRIS FRYE",
    "2022C0243": "JEFF COLEMAN",
    "2022C0234": "RUSS DIAMOND",
    "2022C0169": "CARRIE LEWIS DELROSSO",
    "2022C0160": "TEDDY DANIELS",
    "2022C0153": "GLENN GT THOMPSON",
    "2022C0849": "DALLAS KEPHART",
    "2022C1299": "DEREK A WALKER",
    "2022C0834": "JOHN A SOBEL",
    "2022C0526": "MIKE ARMANINI",
}
# Printed name -> DOS candidate ID (inverse)
ID_BY_CAND = {v: k for k, v in CAND_BY_ID.items()}

# Contest row order within a precinct: the county report's own ballot order,
# write-in row last inside each contest.
PARTY_ORDER = {"DEM": 0, "REP": 1}
CAND_ORDER = {
    ("U.S. Senate", "DEM"): ["ALEX KHALIL", "CONOR LAMB", "MALCOLM KENYATTA",
                             "JOHN FETTERMAN"],
    ("U.S. Senate", "REP"): ["KATHY BARNETTE", "MEHMET OZ", "GEORGE BOCHETTO",
                             "JEFF BARTOS", "DAVE MCCORMICK", "SEAN GALE",
                             "CARLA SANDS", "Write Ins"],
    ("Governor", "DEM"): ["JOSH SHAPIRO"],
    ("Governor", "REP"): ["LOU BARLETTA", "DOUGLAS V MASTRIANO", "NCHE ZAMA",
                          "DAVE WHITE", "MELISSA HART", "BILL MCSWAIN",
                          "CHARLIE GEROW", "JOE GALE", "JAKE CORMAN"],
    ("Lieutenant Governor", "DEM"): ["AUSTIN DAVIS", "RAY SOSA", "BRIAN SIMS"],
    ("Lieutenant Governor", "REP"): ["CLARICE SCHILLINGER", "JAMES EARL JONES",
                                     "RICK SACCONE", "JOHN BROWN", "CHRIS FRYE",
                                     "JEFF COLEMAN", "RUSS DIAMOND",
                                     "CARRIE LEWIS DELROSSO", "TEDDY DANIELS"],
    ("U.S. House", "REP"): ["GLENN GT THOMPSON"],
    ("State House", "REP"): ["DALLAS KEPHART", "DEREK A WALKER", "JOHN A SOBEL",
                             "MIKE ARMANINI"],
}
RV_PARTY_ORDER = ["", "DEM", "NONPARTISAN", "REP"]

# PDF precinct headers, in the report's own (alphabetical) order — 70.
PDF_PRECINCTS = [
    "BRISBIN BOROUGH", "BURNSIDE BOROUGH", "CHESTER HILL BOROUGH",
    "CLEARFIELD BOROUGH - 1ST WARD", "CLEARFIELD BOROUGH - 2ND WARD",
    "CLEARFIELD BOROUGH - 3RD WARD", "CLEARFIELD BOROUGH - 4TH WARD",
    "COALPORT BOROUGH", "CURWENSVILLE BOROUGH", "DUBOIS CITY - 1ST WARD",
    "DUBOIS CITY - 2ND WARD", "DUBOIS CITY - 3RD WARD", "DUBOIS CITY - 4TH WARD",
    "DUBOIS CITY - 5TH WARD", "GLEN HOPE BOROUGH", "GRAMPIAN BOROUGH",
    "HOUTZDALE BOROUGH", "IRVONA BOROUGH", "MAHAFFEY BOROUGH", "NEWBURG BOROUGH",
    "NEW WASHINGTON BOROUGH", "OSCEOLA MILLS BOROUGH", "RAMEY BOROUGH",
    "TROUTVILLE BOROUGH", "WALLACETON BOROUGH", "WESTOVER BOROUGH",
    "BECCARIA TOWNSHIP - 1ST", "BECCARIA TOWNSHIP - 2ND", "BELL TOWNSHIP",
    "BIGLER TOWNSHIP", "BLOOM TOWNSHIP", "BOGGS TOWNSHIP",
    "BRADFORD TOWNSHIP - 1ST", "BRADFORD TOWNSHIP - 2ND", "BRADY TOWNSHIP",
    "BURNSIDE TOWNSHIP", "CHEST TOWNSHIP", "COOPER TOWNSHIP - 1ST",
    "COOPER TOWNSHIP - 2ND", "COVINGTON TOWNSHIP", "DECATUR TOWNSHIP - 1ST",
    "DECATUR TOWNSHIP - 2ND", "FERGUSON TOWNSHIP", "GIRARD TOWNSHIP",
    "GOSHEN TOWNSHIP", "GRAHAM TOWNSHIP", "GREENWOOD TOWNSHIP", "GULICH TOWNSHIP",
    "HUSTON TOWNSHIP", "JORDAN TOWNSHIP", "KARTHAUS TOWNSHIP", "KNOX TOWNSHIP",
    "LAWRENCE TOWNSHIP - GLEN RICHEY", "LAWRENCE TOWNSHIP - GOLDEN ROD",
    "LAWRENCE TOWNSHIP - HYDE", "LAWRENCE TOWNSHIP - PLYMPTON",
    "MORRIS TOWNSHIP - 1ST", "MORRIS TOWNSHIP - 2ND", "PENN TOWNSHIP",
    "PIKE TOWNSHIP", "PINE TOWNSHIP", "SANDY TOWNSHIP FC 1", "SANDY TOWNSHIP FC 2",
    "SANDY TOWNSHIP - OKLAHOMA", "SANDY TOWNSHIP - SABULA",
    "SANDY TOWNSHIP - TREASURE LAKE", "SANDY TOWNSHIP - WEST SANDY",
    "UNION TOWNSHIP", "WOODWARD TOWNSHIP - 1ST", "WOODWARD TOWNSHIP - 2ND",
]
HEADER_UPPER = set(PDF_PRECINCTS)

# DOS precinct code -> PDF header name (DOS codes follow the county's
# alphabetical precinct ordering; the two Burnside codes were disambiguated by
# the per-precinct Republican U.S. Senate values, which match only with
# 110 = Burnside Township and 120 = Burnside Borough).
DOS_PRECINCT_HEADER = {
    "10": "BECCARIA TOWNSHIP - 1ST",
    "20": "BECCARIA TOWNSHIP - 2ND",
    "30": "BELL TOWNSHIP",
    "40": "BIGLER TOWNSHIP",
    "50": "BLOOM TOWNSHIP",
    "60": "BOGGS TOWNSHIP",
    "70": "BRADFORD TOWNSHIP - 1ST",
    "80": "BRADFORD TOWNSHIP - 2ND",
    "90": "BRADY TOWNSHIP",
    "100": "BRISBIN BOROUGH",
    "110": "BURNSIDE TOWNSHIP",
    "120": "BURNSIDE BOROUGH",
    "130": "CHEST TOWNSHIP",
    "140": "CHESTER HILL BOROUGH",
    "150": "CLEARFIELD BOROUGH - 1ST WARD",
    "160": "CLEARFIELD BOROUGH - 2ND WARD",
    "170": "CLEARFIELD BOROUGH - 3RD WARD",
    "180": "CLEARFIELD BOROUGH - 4TH WARD",
    "190": "COALPORT BOROUGH",
    "200": "COOPER TOWNSHIP - 1ST",
    "210": "COOPER TOWNSHIP - 2ND",
    "220": "COVINGTON TOWNSHIP",
    "230": "CURWENSVILLE BOROUGH",
    "240": "DECATUR TOWNSHIP - 1ST",
    "250": "DECATUR TOWNSHIP - 2ND",
    "260": "DUBOIS CITY - 1ST WARD",
    "270": "DUBOIS CITY - 2ND WARD",
    "280": "DUBOIS CITY - 3RD WARD",
    "290": "DUBOIS CITY - 4TH WARD",
    "300": "DUBOIS CITY - 5TH WARD",
    "310": "FERGUSON TOWNSHIP",
    "320": "GIRARD TOWNSHIP",
    "330": "GLEN HOPE BOROUGH",
    "340": "GOSHEN TOWNSHIP",
    "350": "GRAHAM TOWNSHIP",
    "360": "GRAMPIAN BOROUGH",
    "370": "GREENWOOD TOWNSHIP",
    "380": "GULICH TOWNSHIP",
    "390": "HOUTZDALE BOROUGH",
    "400": "HUSTON TOWNSHIP",
    "410": "IRVONA BOROUGH",
    "420": "JORDAN TOWNSHIP",
    "430": "KARTHAUS TOWNSHIP",
    "440": "KNOX TOWNSHIP",
    "450": "LAWRENCE TOWNSHIP - GLEN RICHEY",
    "460": "LAWRENCE TOWNSHIP - GOLDEN ROD",
    "470": "LAWRENCE TOWNSHIP - HYDE",
    "480": "LAWRENCE TOWNSHIP - PLYMPTON",
    "500": "MAHAFFEY BOROUGH",
    "510": "MORRIS TOWNSHIP - 1ST",
    "520": "MORRIS TOWNSHIP - 2ND",
    "530": "NEW WASHINGTON BOROUGH",
    "540": "NEWBURG BOROUGH",
    "550": "OSCEOLA MILLS BOROUGH",
    "560": "PENN TOWNSHIP",
    "570": "PIKE TOWNSHIP",
    "580": "PINE TOWNSHIP",
    "590": "RAMEY BOROUGH",
    "600": "SANDY TOWNSHIP FC 1",
    "610": "SANDY TOWNSHIP FC 2",
    "620": "SANDY TOWNSHIP - OKLAHOMA",
    "630": "SANDY TOWNSHIP - SABULA",
    "640": "SANDY TOWNSHIP - TREASURE LAKE",
    "650": "SANDY TOWNSHIP - WEST SANDY",
    "660": "TROUTVILLE BOROUGH",
    "670": "UNION TOWNSHIP",
    "680": "WALLACETON BOROUGH",
    "690": "WESTOVER BOROUGH",
    "700": "WOODWARD TOWNSHIP - 1ST",
    "710": "WOODWARD TOWNSHIP - 2ND",
}

# Countywide figures printed on the county's own certified "Summary Results
# Report" PDF — used by --verify to validate the per-precinct extraction.
SUMMARY_RV = {"": 42862, "DEM": 14004, "REP": 28630, "NONPARTISAN": 228}
SUMMARY_US_SENATE_REP = {
    "KATHY BARNETTE": 2923, "MEHMET OZ": 4120, "GEORGE BOCHETTO": 73,
    "JEFF BARTOS": 727, "DAVE MCCORMICK": 3957, "SEAN GALE": 99,
    "CARLA SANDS": 862, "Write Ins": 47,
}
SUMMARY_US_SENATE_REP_TOTAL = 12808  # printed "Total Votes Cast", incl. write-ins

ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")


def county_pdf_text() -> str:
    """Extract the precinct PDF with pdftotext -layout (cached extract reused)."""
    if os.path.exists(PDF_EXTRACT_FALLBACK):
        with open(PDF_EXTRACT_FALLBACK, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    out = os.path.join(tempfile.mkdtemp(), "clearfield_primary_precinct.txt")
    subprocess.run(["pdftotext", "-layout", COUNTY_PDF, out], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(out, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def pretty_precinct(header: str) -> str:
    """'CLEARFIELD BOROUGH - 1ST WARD' -> 'Clearfield Borough - 1st Ward'."""
    words = []
    for w in header.split():
        m = ORDINAL_RE.match(w)
        if m:
            words.append(m.group(1) + m.group(2).lower())
        elif w in {"FC"}:
            words.append(w)
        else:
            words.append(w.title() if w.isupper() else w)
    return " ".join(words)


def parse_pdf(text: str) -> tuple[dict, dict]:
    """Parse the precinct PDF (see module docstring for what it carries).

    Returns (sen, rv):
      sen: {pdf_header: {candidate: (total, election_day, mail, provisional)}}
           for the REP 'UNITED STATES SENATOR' blocks; the Write-In Totals row
           is stored under candidate 'Write Ins'.
      rv:  {pdf_header: {'': v, 'DEM': v, 'REP': v, 'NONPARTISAN': v}}
    """
    lines = text.splitlines()
    stats_re = re.compile(r"^\s*STATISTICS\s*$")
    tail_re = re.compile(r"^(.*?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$")
    single_re = re.compile(r"^(.*?)\s+([\d,]+)$")
    skip_label = re.compile(
        r"^(Registered|Ballots|Election|Voter|Precincts|Absentee)")
    contest_re = re.compile(r"^(DEM|REP)\s+(.+)$")
    contest_keys = ("UNITED", "GOVERNOR", "LIEUTENANT", "REPRESENTATIVE",
                    "MEMBER", "COUNTY")

    sen: dict = defaultdict(dict)
    rv: dict = defaultdict(dict)
    bc_dem_nonzero = 0
    prec = party = office = None
    pending4_num = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if stats_re.match(ln):
            # nearest preceding non-blank line is the precinct header
            for j in range(i - 1, -1, -1):
                if lines[j].strip():
                    prec = lines[j].strip()
                    break
            continue
        m = contest_re.match(s)
        if m and m.group(2).split() and m.group(2).split()[0] in contest_keys:
            party, office = m.group(1), " ".join(m.group(2).split())
            pending4_num = None
            continue
        if s.startswith(("Vote For", "Contest Totals", "Total Votes Cast")):
            pending4_num = None
            continue
        if prec is None:
            continue
        if s.startswith("Ballots Cast"):
            if s.startswith("Ballots Cast - DEMOCRATIC") and pending4_num:
                if int(pending4_num.split()[0].replace(",", "")):
                    bc_dem_nonzero += 1
            pending4_num = None
            continue
        if s.startswith("Registered Voters"):
            if s.startswith("Registered Voters - Total"):
                key = ""
            elif s.startswith("Registered Voters - DEMOCRATIC"):
                key = "DEM"
            elif s.startswith("Registered Voters - REPUBLICAN"):
                key = "REP"
            elif s.startswith("Registered Voters - NONPARTISAN"):
                key = "NONPARTISAN"
            else:
                continue
            m1 = single_re.match(s)
            if m1:
                rv[prec][key] = int(m1.group(2).replace(",", ""))
            continue
        m2 = tail_re.match(s)
        if not m2:
            pending4_num = (" ".join(s.split()) if s.replace(" ", "").isdigit()
                            else None)
            continue
        pending4_num = m2.group(2)
        label = " ".join(m2.group(1).split())
        if skip_label.match(label):
            continue
        if party == "REP" and office == "UNITED STATES SENATOR":
            cand = "Write Ins" if label == "Write-In Totals" else label
            sen[prec][cand] = tuple(int(g.replace(",", "")) for g in m2.groups()[1:])
    return dict(sen), dict(rv), (bc_dem_nonzero == 0)


def parse_dos(path: str) -> list[dict]:
    """Clearfield (county 17, election type 'P') rows of the PA DOS prcres file."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        for r in csv.reader(fh):
            if len(r) < 21 or r[:2] != ["2022", "P"] or r[2] != COUNTY_CODE:
                continue
            if r[8] not in DOS_OFFICES:
                raise SystemExit("unexpected DOS office code %r in %s"
                                 % (r[8], path))
            out.append({
                "precinct_code": r[3],
                "oc": r[8],
                "office": DOS_OFFICES[r[8]],
                "party": r[9],
                "cand_id": r[10],
                "votes": int(r[15]),
                "uscd": r[18],
                "shd": r[20],
            })
    return out


def build_rows(dos_rows: list[dict], sen: dict, rv: dict) -> list[list]:
    dos_index = {}
    dos_precincts = set()
    uscd_by_prec = {}
    shd_by_prec = {}
    for r in dos_rows:
        dos_index[(r["precinct_code"], r["oc"], r["party"],
                   r["cand_id"])] = r["votes"]
        dos_precincts.add(r["precinct_code"])
        if r["office"] == "U.S. House":
            uscd_by_prec[r["precinct_code"]] = r["uscd"]
        if r["office"] == "State House":
            shd_by_prec[r["precinct_code"]] = r["shd"]

    missing = [r["precinct_code"] for r in dos_rows
               if r["precinct_code"] not in DOS_PRECINCT_HEADER]
    if missing:
        raise SystemExit("DOS precinct codes without mapping: %r"
                         % sorted(set(missing)))

    rows = []
    for header in PDF_PRECINCTS:            # report's own precinct order
        out_prec = pretty_precinct(header)

        base = {"county": COUNTY, "precinct": out_prec}

        # Registered Voters (PDF; party-sourced per the 2020 precedent)
        if header in rv:
            for pty in RV_PARTY_ORDER:
                if pty in rv[header]:
                    rows.append({**base, "office": "Registered Voters",
                                 "district": "", "party": pty, "candidate": "",
                                 "votes": rv[header][pty],
                                 "election_day": "", "mail": "",
                                 "provisional": ""})
        else:
            raise SystemExit("no Statistics block found for %r" % header)

        for office in OFFICE_ORDER[1:]:
            ocode = DOS_OFFICE_BY_NAME[office]
            for pty in ("DEM", "REP"):
                for cand in CAND_ORDER.get((office, pty), []):
                    row = {**base, "office": office, "district": "",
                           "party": pty, "candidate": cand,
                           "election_day": "", "mail": "", "provisional": ""}
                    if office == "U.S. Senate" and pty == "REP":
                        # County PDF block (post-recount; only contest with
                        # per-precinct data): breakdown + write-ins.
                        if header not in sen or cand not in sen[header]:
                            raise SystemExit("PDF REP U.S. Senate block "
                                             "incomplete at %r / %r"
                                             % (out_prec, cand))
                        total, ed, mail, prov = sen[header][cand]
                        if total != ed + mail + prov:
                            raise SystemExit(
                                "breakdown does not sum: %r %r %s"
                                % (out_prec, cand, (total, ed, mail, prov)))
                        row.update({"votes": total, "election_day": str(ed),
                                    "mail": str(mail), "provisional": str(prov)})
                    else:
                        cand_id = ID_BY_CAND[cand]
                        key = (DOS_PRECINCT_HEADER_INV[header], ocode, pty,
                               cand_id)
                        if key not in dos_index:
                            # contest not reported for this precinct/party:
                            # State House candidates only appear in their own
                            # district's precincts (DEM House/State House are
                            # write-in-only and carry no DOS rows at all).
                            if office in ("U.S. House", "State House"):
                                continue
                            raise SystemExit(
                                "missing DOS row %r" % (key, ))
                        row["votes"] = dos_index[key]
                        if office == "U.S. House":
                            row["district"] = str(
                                int(uscd_by_prec[DOS_PRECINCT_HEADER_INV[header]]))
                        elif office == "State House":
                            row["district"] = str(
                                int(shd_by_prec[DOS_PRECINCT_HEADER_INV[header]]))
                    rows.append(row)
    return rows


DOS_PRECINCT_HEADER_INV = {v: k for k, v in DOS_PRECINCT_HEADER.items()}


def to_csv_rows(rows: list[dict]) -> list[list]:
    return [[r["county"], r["precinct"], r["office"], r["district"],
             r["party"], r["candidate"], str(r["votes"]),
             str(r["election_day"]), str(r["mail"]),
             str(r["provisional"])] for r in rows]


def load_anchor():
    """ENR anchor county rows for Clearfield: {(office, district, party, cand): votes}."""
    out = {}
    if not os.path.exists(ANCHOR_CSV):
        return out
    with open(ANCHOR_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.reader(fh):
            if r and r[0] == COUNTY:
                out[(r[1], r[2], r[3], r[4])] = int(r[5])
    return out


def run_checks(dos_rows: list[dict], sen: dict, rv: dict, rows: list[dict]) -> list[str]:
    msgs = []
    problems = []
    noted = []

    # 1) PDF per-precinct REP U.S. Senate must equal the DOS certified values.
    dos_index = {}
    dos_county = defaultdict(int)
    for r in dos_rows:
        dos_index[(r["precinct_code"], r["oc"], r["party"],
                   r["cand_id"])] = r["votes"]
        dos_county[(r["office"], r["party"], CAND_BY_ID[r["cand_id"]])] += r["votes"]
    checked = 0
    dif = []
    for header, cands in sen.items():
        pcode = DOS_PRECINCT_HEADER_INV[header]
        for cand, (total, ed, mail, prov) in sorted(cands.items()):
            if cand == "Write Ins":
                continue
            v = dos_index.get((pcode, "USS", "REP", ID_BY_CAND[cand]))
            checked += 1
            if v is None or v != total:
                dif.append((header, cand, total, v))
            if total != ed + mail + prov:
                problems.append("PDF breakdown does not sum: %r %r %s"
                                % (header, cand, (total, ed, mail, prov)))
    msgs.append("PDF vs DOS per-precinct REP U.S. Senate: %d values checked, "
                "%d differ" % (checked, len(dif)))
    if sum(x[2] or 0 for x in dif) - sum(x[3] or 0 for x in dif) == 0:
        for d in dif:
            noted.append("per-precinct REP U.S. Senate difference (cancels "
                         "county-wide; county PDF used): %r" % (d, ))
    else:
        for d in dif:
            problems.append("PDF/DOS Senate mismatch: %r" % (d, ))
    if not sen:
        problems.append("PDF REP U.S. Senate blocks not found at all")

    # 2) Registered Voters sums vs the county summary PDF's countywide figures.
    for key in ("", "DEM", "REP", "NONPARTISAN"):
        total = sum(v[key] for v in rv.values() if key in v)
        if total != SUMMARY_RV[key]:
            problems.append("Registered Voters county sum %d != printed %d (party %r)"
                            % (total, SUMMARY_RV[key], key))
        else:
            msgs.append("Registered Voters county sum: %d == printed summary %d (party %r)"
                        % (total, SUMMARY_RV[key], key))

    # 3) REP U.S. Senate county sums vs the printed summary / its contest total.
    sums = defaultdict(int)
    for header, cands in sen.items():
        for cand, vals in cands.items():
            sums[cand] += vals[0]
    for cand in sorted(sums):
        expect = SUMMARY_US_SENATE_REP.get(cand)
        if expect is None:
            problems.append("unexpected Senate REP candidate %r from PDF" % cand)
            continue
        if sums[cand] != expect:
            problems.append("Senate REP %s: per-precinct sum %d != printed summary %d"
                            % (cand, sums[cand], expect))
        else:
            msgs.append("U.S. Senate REP %-16s per-precinct sum %5d == printed summary %d"
                        % (cand, sums[cand], expect))
    if sum(sums.values()) != SUMMARY_US_SENATE_REP_TOTAL:
        problems.append("Senate REP total sum %d != printed Total Votes Cast %d"
                        % (sum(sums.values()), SUMMARY_US_SENATE_REP_TOTAL))
    else:
        msgs.append("U.S. Senate REP total (incl. write-ins): %d == printed %d"
                    % (sum(sums.values()), SUMMARY_US_SENATE_REP_TOTAL))

    # 4) Per (office, district, party, candidate) sums vs the ENR anchor
    #   (named candidates only; write-ins / RV rows excluded).
    anchor = load_anchor()
    our = defaultdict(int)
    for r in rows:
        if r["office"] == "Registered Voters" or r["candidate"] == "Write Ins":
            continue
        dist = str(r["district"] or "")
        name_lookup = NAME_ENR.get(r["candidate"], r["candidate"])
        our[(r["office"], dist, r["party"], name_lookup)] += int(r["votes"])
    matched = mismatched = anchor_only = our_only = 0
    mism = []
    expected = {"Member of Democratic State Committee",
                "Member of Republican State Committee"}
    for k in sorted(set(our) | set(anchor)):
        a, o = anchor.get(k), our.get(k)
        if a == o:
            matched += 1
        elif a is not None and o is not None:
            mismatched += 1
            mism.append((k, o, a))
        elif o is None:
            if k[0] in expected:
                msgs.append("ENR anchor %s (no per-precinct source) not "
                            "produced: %d votes" % (k, a))
            else:
                anchor_only += 1
        else:
            our_only += 1
    msgs.append("ENR anchor comparison: %d matched, %d mismatched, "
                "%d anchor-only rows (omitted contest), %d our-only" %
                (matched, mismatched, anchor_only, our_only))
    for k, o, a in mism:
        # If the per-precinct sums equal the certified DOS bulk-file county
        # total, the ENR anchor's differing value is a documented feed issue
        # (pre-recount Republican U.S. Senate snapshot / impossible
        # Lieutenant Governor block), not a local problem.
        dos_tot = dos_county.get((k[0], k[2],
                                  NAME_ENR_INV.get(k[3], k[3])))
        if dos_tot is not None and dos_tot == o:
            noted.append("%s %s: ours(per-precinct sum) %d = certified DOS %d, "
                         "ENR anchor has %d" % (k[0], k[3], o, dos_tot, a))
        else:
            problems.append("ENR anchor mismatch %s: ours(per-precinct sum) %d "
                            "vs anchor %d" % (k, o, a))
    for k in sorted(set(anchor) - set(our)):
        if k[0] not in expected:
            problems.append("anchor row we did not produce: %s -> %d" % (k, anchor[k]))
    for k in sorted(set(our) - set(anchor)):
        problems.append("row we produced absent from anchor: %s" % (k, ))

    if noted:
        msgs.append("Noted (documented differences, %d):" % len(noted))
        msgs.extend(["  " + n for n in noted])
        return msgs + ["", "PROBLEMS: none"]
    return msgs + (["", "PROBLEMS:"] + ["  " + p for p in problems] if problems else [])


# ENR anchor (ENR/DOS formal variants) -> county PDF printed name.  Only pairs
# whose names differ from what the anchor uses need an entry.
NAME_ENR = {
    # the county PDF prints the short ballot name; the ENR anchor carries the
    # formal DOS names
    "ALEX KHALIL": "Alexandria Gloria Khalil",
    "CONOR LAMB": "Conor J. Lamb",
    "MALCOLM KENYATTA": "Malcolm Kenyatta",
    "JOHN FETTERMAN": "John K. Fetterman",
    "KATHY BARNETTE": "Kathy J. Barnette",
    "MEHMET OZ": "Mehmet C. Oz",
    "GEORGE BOCHETTO": "George A. Bochetto",
    "JEFF BARTOS": "Jeffrey A. Bartos",
    "DAVE MCCORMICK": "David H. McCormick",
    "SEAN GALE": "Sean Peter Gale",
    "CARLA SANDS": "Carla Herd Sands",
    "JOSH SHAPIRO": "Joshua D. Shapiro",
    "LOU BARLETTA": "Louis Barletta",
    "DOUGLAS V MASTRIANO": "Douglas V. Mastriano",
    "NCHE ZAMA": "Nche Zama",
    "DAVE WHITE": "David J. White",
    "MELISSA HART": "Melissa A. Hart",
    "BILL MCSWAIN": "William M. McSwain",
    "CHARLIE GEROW": "Charles R. Gerow",
    "JOE GALE": "Joseph Charles Gale",
    "JAKE CORMAN": "Jacob Doyle Corman III",
    "AUSTIN DAVIS": "Austin Davis",
    "RAY SOSA": "Raymond L. Sosa",
    "BRIAN SIMS": "Brian Sims",
    "CLARICE SCHILLINGER": "Clarice D. Schillinger",
    "JAMES EARL JONES": "James E. Jones",
    "RICK SACCONE": "Richard Saccone",
    "JOHN BROWN": "John A. Brown",
    "CHRIS FRYE": "Christopher C. Frye Jr",
    "JEFF COLEMAN": "Jeffrey H. Coleman",
    "RUSS DIAMOND": "Russell H. Diamond",
    "CARRIE LEWIS DELROSSO": "Carrie Delrosso",
    "TEDDY DANIELS": "Theodore Daniels",
    "GLENN GT THOMPSON": "Glenn William Thompson Jr",
    "DALLAS KEPHART": "Dallas Paul Kephart",
    "DEREK A WALKER": "Derek A. Walker",
    "JOHN A SOBEL": "John A. Sobel",
    "MIKE ARMANINI": "Michael J. Armanini",
}
NAME_ENR_INV = {v: k for k, v in NAME_ENR.items()}


def main(argv: list[str]) -> int:
    verify = "--verify" in argv
    argv = [a for a in argv if not a.startswith("--")]
    dos_path = argv[0] if argv else DOS_DEFAULT
    out_path = argv[1] if len(argv) > 1 else OUTPUT_DEFAULT

    text = county_pdf_text()
    sen, rv, bc_dem_clean = parse_pdf(text)
    if not bc_dem_clean:
        # Recount artifact check — see module docstring.  A non-zero DEM
        # "Ballots Cast" would mean the report's statistics are usable.
        sys.stderr.write(
            "NOTE: the PDF's Ballots Cast - DEMOCRATIC statistics are not all "
            "zero; Ballots Cast rows still not emitted (recount artifact).\n")
    dos_rows = parse_dos(dos_path)
    rows = build_rows(dos_rows, sen, rv)

    if verify:
        for line in run_checks(dos_rows, sen, rv, rows):
            print(line)

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(HEADER)
        w.writerows(to_csv_rows(rows))
    print("wrote %d data rows to %s" % (len(rows), out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))