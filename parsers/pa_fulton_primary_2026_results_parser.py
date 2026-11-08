#!/usr/bin/env python3
"""Fulton County 2026 primary precinct results parser.

Two vintages, both documented:

1. OFFICIAL (PA DOS certified bulk precinct returns, split per county at
   work2026p/dos/fulton__dos_precinctreturns.txt, county code 29).  Carries
   every named ballot contest on the primary ballot except the party state
   committee race and the local township/borough Democratic Committee
   Member race: GOV (DEM/REP), LTG (DEM; REP Richey/Ventre), USC 13,
   STS 30, STH 78.  Contest sums match the countywide anchor
   (2026/20260519__pa__primary__county.csv) exactly for every row this
   source contributes, so it is used IN PREFERENCE TO the county PDF for
   these contests.  DOS rows carry a single votes cell and no write-in /
   RV / BC rows, so breakdown columns are left empty for them.

2. ELECTION-NIGHT PDF ("Fulton County 2026 Primary Precinct Reports
   Combined.pdf", 26 reports = 13 municipalities x Democratic/Republican,
   unofficial, Run Date 05/19/2026, Run Time 7:35-9:14 PM), used
   (a) for the local DEM Committee Member contest rows, which neither
   DOS nor any other source carries (named rows exist only in 4 of the
   13 Democratic reports; the other 9 print unnamed zero rows and are
   skipped), and (b) for the Ballots Cast rows, decoded from each
   report's "X of N registered voters" panel.  That panel is ballots
   cast, not registered voters: for each report mail + election-day
   "Cast Votes" plus that contest's undervotes equals the panel value
   exactly (e.g. Ayr Republican 137 ED + 21 undervotes + 28 mail + 2
   undervotes = 188), and it matches the 2024 general-fulton finding
   that the same vendor panel is ballots cast.

   Election-night vs certified deltas seen (Republican precinct reports,
   election day column, PDF total -> certified countywide anchor):
   Governor/GARRITY 903->905, Lt Governor/RICHEY 709->711,
   U.S. House/JOYCE 992->994, State Senate/WARD 998->1000,
   State House/TOPPER 1018->1020, Member of Republican State
   Committee/KERLIN 1115->1117.  Every mail and every Democratic
   breakdown matches the anchor exactly.  Because only countywide
   certified totals exist (the anchor), the +2 election-day votes cannot
   be apportioned to precincts, so the state committee rows keep their
   election-night numbers (documented -2 ED below) while the contests DOS
   carries use the certified totals.

   The county PDF prints no write-in rows in any contest, and the DOS
   certified file carries no write-in rows either, so the output has no
   Write-In rows for this election.  Member of Democratic State
   Committee appears on all 13 Democratic ballots but every one of the
   13 rows is an unnamed zero placeholder, so the contest is omitted
   (no rows to emit; the countywide anchor carries no Fulton Democratic
   State Committee row either).

DOS is used for the five contest families above because Fulton's own
source (the combined PDF) is election-night only; per the coordinator's
brief the certified file takes precedence so precinct sums reconcile with
the certified countywide anchor.

Output: 2026/counties/20260519__pa__primary__fulton__precinct.csv
Columns: county,precinct,office,district,party,candidate,votes,
         election_day,mail,provisional

Usage:
    python parsers/pa_fulton_primary_2026_results_parser.py [output_csv] \
        [dos_bulk_file] [combined_pdf]

pdftotext (poppler) must be on PATH.
"""

import csv
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DEFAULT = os.path.join(
    REPO, "2026", "counties", "20260519__pa__primary__fulton__precinct.csv")
DOS_DEFAULT = os.path.join(
    REPO, "work2026p", "dos", "fulton__dos_precinctreturns.txt")
PDF_DEFAULT = os.path.expanduser(
    "~/code/openelections-sources-pa/2026/primary/"
    "Fulton County 2026 Primary Precinct Reports Combined.pdf")
COUNTY = "Fulton"

# 13 precincts = 13 municipalities (one precinct per municipality)
MUNIS = ["Ayr", "Belfast", "Bethel", "Brush Creek", "Dublin",
         "Licking Creek", "McConnellsburg", "Taylor", "Thompson", "Todd",
         "Union", "Valley Hi", "Wells"]
PRECINCT_NAMES = {
    "Ayr": "Ayr Township",
    "Belfast": "Belfast Township",
    "Bethel": "Bethel Township",
    "Brush Creek": "Brush Creek Township",
    "Dublin": "Dublin Township",
    "Licking Creek": "Licking Creek Township",
    "McConnellsburg": "McConnellsburg Borough",
    "Taylor": "Taylor Township",
    "Thompson": "Thompson Township",
    "Todd": "Todd Township",
    "Union": "Union Township",
    "Valley Hi": "Valley Hi Borough",
    "Wells": "Wells Township",
}
PARTIES = {"Democratic": "DEM", "Republican": "REP"}

HEADER = ["county", "precinct", "office", "district", "party",
          "candidate", "votes", "election_day", "mail", "provisional"]

# DOS precinct code -> output precinct name (matches the county's own
# naming in the 2024/2025 files)
DOS_PRECINCT_NAMES = {
    "10": "Ayr Township",
    "20": "Belfast Township",
    "30": "Bethel Township",
    "40": "Brush Creek Township",
    "50": "Dublin Township",
    "60": "Licking Creek Township",
    "70": "McConnellsburg Borough",
    "80": "Taylor Township",
    "90": "Thompson Township",
    "100": "Todd Township",
    "110": "Union Township",
    "120": "Valley Hi Borough",
    "130": "Wells Township",
}

# DOS office code -> (output office, district)
DOS_OFFICES = {
    "GOV": ("Governor", ""),
    "LTG": ("Lieutenant Governor", ""),
    "USC": ("U.S. House", "13"),
    "STS": ("State Senate", "30"),
    "STH": ("State House", "78"),
}

# DOS (office, party, LAST, FIRST) -> candidate name as printed on the
# county PDF reports.  All-party-candidates for this file:  every DOS row
# for a mapped office must hit this map or the parser fails.  Any
# unmapped row raises.
DOS_CANDIDATES = {
    ("GOV", "DEM", "SHAPIRO", "JOSHUA"): "JOSH SHAPIRO",
    ("GOV", "REP", "GARRITY", "STACY"): "STACY GARRITY",
    ("LTG", "DEM", "DAVIS", "AUSTIN"): "AUSTIN DAVIS",
    ("LTG", "REP", "RICHEY", "JASON"): "JASON RICHEY",
    ("LTG", "REP", "VENTRE", "JOHN"): "JOHN VENTRE",
    ("USC", "DEM", "FARNHAM", "ELIZABETH"): "BETH FARNHAM",
    ("USC", "REP", "JOYCE", "JOHN"): "JOHN JOYCE",
    ("STS", "DEM", "STEEG", "SEAN"): "SEAN STEEG",
    ("STS", "REP", "WARD", "JUDITH"): "JUDY WARD",
    ("STH", "REP", "TOPPER", "JESSE"): "JESSE TOPPER",
}


def dos_rows(dos_path):
    """Certified per-precinct rows for the five DOS-carried contest
    families, single votes cell (breakdown columns empty)."""
    out = []
    with open(dos_path, newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            rec = next(csv.reader([line]))
            if len(rec) < 17 or rec[1] != "P" or rec[2].replace(".0", "") != "29":
                continue
            code = (rec[8], rec[9], rec[11].strip().upper(),
                    rec[12].strip().upper())
            if code not in DOS_CANDIDATES:
                raise SystemExit("unmapped DOS row %r" % (code,))
            precinct = DOS_PRECINCT_NAMES[str(int(float(rec[3])))]
            office, district = DOS_OFFICES[rec[8]]
            votes = str(int(float(rec[15])))
            out.append([COUNTY, precinct, office, district, rec[9],
                        DOS_CANDIDATES[code], votes, "", "", ""])
    return out


def pdf_text(pdf_path, out_dir):
    """Extract the combined PDF to -layout text (cached) under out_dir."""
    txt = os.path.join(out_dir, "combined.txt")
    if not os.path.exists(txt):
        subprocess.check_call(["pdftotext", "-layout", pdf_path, txt])
    with open(txt, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def split_reports(text):
    """Yield (muni_key, party, [lines]) for each of the 26 wrapped page
    headers 'Ayr Township - Democratic Official Results'.  The party word
    and even the header itself can wrap onto the following line(s) as the
    left margin of the page header shifts, so headers are located by the
    'Official Results' string plus the municipality token."""
    lines = [l.rstrip() for l in text.splitlines()]
    starts = []
    for i, line in enumerate(lines):
        if "Official Results" not in line:
            continue
        tokens = line.split()
        # The report's municipality word can carry a leading form-feed.
        first = tokens[0].strip("\x0c") if tokens else ""
        muni = next((m for m in MUNIS if m.startswith(first)), None)
        if not muni:
            continue
        party = None
        chunk = re.match(r"^(.+?)\s*-\s*Official", line)
        body = chunk.group(1) if chunk else line
        m = re.search(r"(Democratic|Republican)\b", body)
        if m:
            party = m.group(1)
        else:
            # When the page header wraps, the party word starts the
            # line below ('Republican   FULTON COUNTY, PENNSYLVANIA').
            nxt = " ".join(lines[i + 1].split())
            m = re.match(r"^(Democratic|Republican)\s+FULTON", nxt)
            if m:
                party = m.group(1)
        if party:
            starts.append((i, muni, party))
    reports = []
    for idx, (i, muni, party) in enumerate(starts):
        # Reports interleave their two pages (Page 1 ends, then the Page 2
        # header of the same report); a municipality can therefore appear
        # as the start of two blocks.  Skip a block that does NOT move to
        # a new report: real reports alternate parties.  Blocks are keyed
        # by (muni, party) so repeated headers of the same report are
        # merged by taking the first Page-1 header per report and
        # scanning until the next DIFFERENT report header.
        reports.append((i, muni, party))
    return lines, reports


def parse_reports(text):
    """Parse the combined report text into
    ballots_cast: {(muni, party_code): count}
    rows: {(muni, party_code, office_raw, name): [mail, ed, prov, total]}
    plus a sanity count of the reports located."""
    lines, starts = split_reports(text)
    report_set = []
    seen = set()
    for i, muni, party in starts:
        if (muni, party) not in seen:
            seen.add((muni, party))
            report_set.append((i, muni, party))
    assert len(report_set) == 26, "expected 26 reports, got %d" % len(report_set)

    pair_re = re.compile(r"(-?\d+)\s+([\d.]+)%")
    header_re = re.compile(r"^(.+?)\s+-\s+(Democratic|Republican)\s+Party",
                           re.I)
    name_re = re.compile(r"\s*([A-Za-z(].*?)(?=\s+-?\d+\s+[\d.]+%| -?\d+ )")
    bc = {}
    rows = {}
    for idx, (i, muni, party_word) in enumerate(report_set):
        end = report_set[idx + 1][0] if idx + 1 < len(report_set) else len(lines)
        party = PARTIES[party_word]
        cur = None
        for line in lines[i:end]:
            s = " ".join(line.split())
            m = re.search(r"(\d+)\s+of\s+([\d,]+)\s+registered voters", s)
            if m:
                # The header panel "X of N registered voters" is ballots
                # cast per ballot style (see module docstring).
                bc[(muni, party)] = int(m.group(1))
                continue
            m = header_re.match(s)
            if m and m.group(1) not in ("Choice",):
                cur = (m.group(1).strip(), party, m.group(1).strip())
                continue
            if not s or s.startswith(("Choice", "Cast Votes:", "Undervotes",
                                      "Overvotes", "*** End of report")):
                continue
            pairs = pair_re.findall(s)
            if pairs and cur:
                total = int(pairs[-1][0])
                if len(pairs) != 4:
                    raise SystemExit(
                        "unexpected column layout in %s (%s): %r"
                        % ((muni, party_word), cur, s))
                mail, ed, prov, total = (int(p[0]) for p in pairs)
                nm = name_re.match(s)
                name = nm.group(1).strip() if nm else ""
                if not name:
                    continue  # unnamed placeholder row (no rows emitted)
                rows[(muni, party, cur[0], name)] = [mail, ed, prov, total]
    return bc, rows


def map_office(raw):
    """Normalize an office title to the OpenElections standard."""
    m = " ".join(raw.split()).upper()
    if m.startswith("GOVERNOR"):
        return "Governor", ""
    if m.startswith("LIEUTENANT GOVERNOR"):
        return "Lieutenant Governor", ""
    if m.startswith("REPRESENTATIVE IN CONGRESS"):
        return "U.S. House", "13"
    if m.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        return "State Senate", "30"
    if m.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        return "State House", "78"
    # Member of <party> State Committee: county committee district
    mm = re.match(r"MEMBER OF (?:DEMOCRATIC|REPUBLICAN) STATE COMMITTEE "
                  r"FULTON COUNTY DISTRICT (\d+)$", m)
    if mm:
        return ("Member of Republican State Committee",
                "Fulton County (District %s)" % mm.group(1))
    # Local committee race: strip the "(Vote For ...)" tail and the
    # wrapped "Electing: ..." continuation for a clean office name
    m = re.sub(r"\s*\(vote for[^)]*\)$", "", m, flags=re.I).strip()
    return m, ""


def main():
    out_csv = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    dos_path = sys.argv[2] if len(sys.argv) > 2 else DOS_DEFAULT
    pdf_path = sys.argv[3] if len(sys.argv) > 3 else PDF_DEFAULT
    out_dir = os.path.join(REPO, "work2026p", "fulton", "text")
    os.makedirs(out_dir, exist_ok=True)

    rows = dos_rows(dos_path)

    text = pdf_text(pdf_path, out_dir)
    bc, contest_rows = parse_reports(text)
    if len(bc) != 26:
        raise SystemExit("ballots-cast panel captured for only %d of 26 "
                         "reports" % len(bc))

    # Ballots Cast rows (election-night panels, per ballot style)
    for muni, party in sorted(bc, key=lambda k: (MUNIS.index(k[0]), k[1])):
        rows.append([COUNTY, PRECINCT_NAMES[muni], "Ballots Cast", "", party,
                     "", str(bc[(muni, party)]), "", "", ""])

    # PDF-only contest rows: the local Democratic Committee Member race
    # (not carried by DOS).  Everything DOS carries came from the
    # certified file; election-night rows for carried contests would
    # double-count, so only offices absent from DOS are emitted here.
    state_committee_pdf_rows = []
    for (muni, party, office_raw, name), (mail, ed, prov, total) \
            in sorted(contest_rows.items()):
        office, district = map_office(office_raw)
        if office in {o for o, _ in DOS_OFFICES.values()}:
            continue  # certified rows already emitted above
        if office == "Member of Republican State Committee":
            state_committee_pdf_rows.append(
                [COUNTY, PRECINCT_NAMES[muni], office, district, party,
                 name, str(total), str(ed), str(mail), str(prov)])
            continue
        if not office.endswith("COMMITTEE MEMBER"):
            raise SystemExit("unexpected PDF-only office %r" % office)
        rows.append([COUNTY, PRECINCT_NAMES[muni], office, district, party,
                     name, str(total), str(ed), str(mail), str(prov)])

    # Member of Republican State Committee is not in the certified bulk
    # file, so its rows come from the election-night PDF (documented
    # -2 election-day delta above).
    rows.extend(state_committee_pdf_rows)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    print("wrote %d rows to %s" % (len(rows), out_csv))


if __name__ == "__main__":
    main()
