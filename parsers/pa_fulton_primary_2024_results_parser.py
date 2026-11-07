#!/usr/bin/env python3
"""Fulton County 2024 primary results parser.

Precinct detail comes from two vintages, both documented:

1. OFFICIAL (PA DOS certified bulk precinct returns,
   ElectionReturns_2024_Primary_PrecinctReturns.txt, county code 29).
   Carries every named contest except (a) township Republican Committee
   Person races and (b) Alternate Delegate to the RNC (carried as all-zero
   rows in the DOS file, while the county's cumulative official totals are
   non-zero -- a DOS-side omission).  DOS rows have a single votes cell, so
   the election_day/mail/provisional breakdown columns are left empty for
   them.  Contest sums reconcile exactly with the county's two cumulative
   official reports (cum_dem/cum_rep, Run Date 04/26/2024) for every DOS
   contest.

2. ELECTION-NIGHT PDF reports ("Fulton PA 2024 Primary <Municipality> -
   (Democratic|Republican).pdf", unofficial, Run Time 9:05 PM 04/23/2024),
   used only for the committee-person contest rows (not carried by DOS;
   sums within 1 vote of the official cumulative anchors for every
   candidate) and the alternate-delegate contest (not carried by DOS;
   election-night precinct sums within 3 votes of the official anchors),
   plus the Ballots Cast rows decoded from each report's "X of 0
   registered voters" panel (per-ballot-style ballots cast; the panel
   values sum to 2408 vs 2415/2008 in the official cumulative headers, so
   they are a few votes short of the official canvass).

Output: 2024/counties/20240423__pa__primary__fulton__precinct.csv
Columns: county,precinct,office,district,party,candidate,votes,
         election_day,mail,provisional

Usage:
    python parsers/pa_fulton_primary_2024_results_parser.py [output_csv] \
        [dos_bulk_file]

pdftotext (poppler) must be on PATH.
"""

import csv
import os
import re
import subprocess
import sys

SOURCE_DIR = os.path.expanduser(
    "~/code/openelections-sources-pa/2024/primary")
OUT_DEFAULT = "2024/counties/20240423__pa__primary__fulton__precinct.csv"
DOS_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2024p", "warren", "electionreturns_2024_primary_precinctreturns.txt")
COUNTY = "Fulton"

# 13 precincts = 13 municipalities (one precinct per municipality)
MUNICIPALITIES = [
    "Ayr Township",
    "Belfast Township",
    "Bethel Township",
    "Brush Creek Township",
    "Dublin Township",
    "Licking Creek Township",
    "McConnellsburg Borough",
    "Taylor Township",
    "Thompson Township",
    "Todd Township",
    "Union Township",
    "Valley Hi Borough",
    "Wells Township",
]
PARTIES = {"Democratic": "DEM", "Republican": "REP"}

HEADER = ["county", "precinct", "office", "district", "party",
          "candidate", "votes", "election_day", "mail", "provisional"]

# DOS precinct code -> output precinct name (matches the county's own
# naming in the 2024/2025 general files)
PRECINCT_NAMES = {
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

# DOS office code -> (output office, districted?)
DOS_OFFICES = {
    "USP": ("President", False),
    "USS": ("U.S. Senate", False),
    "ATT": ("Attorney General", False),
    "AUD": ("Auditor General", False),
    "TRE": ("State Treasurer", False),
    "USC": ("U.S. House", True),
    "STH": ("State House", True),
    "DED": ("Delegate to the Democratic National Convention", True),
    "DER": ("Delegate to the Republican National Convention", True),
    # ADR intentionally NOT here: the DOS file carries it as all-zero
    # rows for Fulton while the county's official cumulative totals are
    # non-zero, so alternate delegates keep the PDF-source rows.
}

# DOS (office, party, LAST, FIRST) -> candidate name as printed on the
# county's official cumulative/township reports (keeps names consistent
# across vintages in the output; DOS spells them from voter-registration
# records).  Every DOS row for a mapped office must hit this map or the
# parser fails.
DOS_CANDIDATES = {
    ("USP", "DEM", "BIDEN", "JOSEPH"): "JOSEPH R BIDEN JR",
    ("USP", "DEM", "PHILLIPS", "DEAN"): "DEAN PHILLIPS",
    ("USP", "REP", "HALEY", "NIKKI"): "NIKKI R HALEY",
    ("USP", "REP", "TRUMP", "DONALD"): "DONALD J TRUMP",
    ("USS", "DEM", "CASEY", "ROBERT"): "ROBERT P CASEY JR",
    ("USS", "REP", "MCCORMICK", "DAVID"): "DAVE MCCORMICK",
    ("ATT", "DEM", "STOLLSTEIMER", "JACK"): "JACK STOLLSTEIMER",
    ("ATT", "DEM", "DEPASQUALE", "EUGENIO"): "EUGENE DEPASQUALE",
    ("ATT", "DEM", "KHAN", "JOSEPH"): "JOE KHAN",
    ("ATT", "DEM", "BRADFORD-GREY", "KEIR"): "KEIR BRADFORD-GREY",
    ("ATT", "DEM", "SOLOMON", "JARED"): "JARED SOLOMON",
    ("ATT", "REP", "SUNDAY", "DAVID"): "DAVE SUNDAY",
    ("ATT", "REP", "WILLIAMS", "WENDELL"): "CRAIG WILLIAMS",
    ("AUD", "DEM", "KENYATTA", "MALCOLM"): "MALCOM KENYATTA",
    ("AUD", "DEM", "PINSLEY", "MARK"): "MARK PINSLEY",
    ("AUD", "REP", "DEFOOR", "TIMOTHY"): "TIM DEFOOR",
    ("TRE", "DEM", "BIZZARRO", "RYAN"): "RYAN BIZZARRO",
    ("TRE", "DEM", "MCCLELLAND", "ERIN"): "ERIN MCCLELLAND",
    ("TRE", "REP", "GARRITY", "STACY"): "STACY GARRITY",
    ("USC", "DEM", "FARNHAM", "ELIZABETH"): "BETH FARNHAM",
    ("USC", "REP", "JOYCE", "JOHN"): "JOHN JOYCE",
    ("STH", "REP", "TOPPER", "JESSE"): "JESSE TOPPER",
    ("DED", "DEM", "GRIBLER", "MATTHEW"): "MATTHEW A GRIBLER",
    ("DED", "DEM", "KRAMER", "LISA"): "LISA KRAMER",
    ("DER", "REP", "HAWBAKER", "ERICH"): "ERICH E HAWBAKER",
    ("DER", "REP", "WARD", "WILLIAM"): "BILL WARD JR",
    ("DER", "REP", "MCCLURE", "C"): "C ARNOLD MCCLURE",
    ("DER", "REP", "CAROWICK", "STEVEN"): "STEVEN CAROWICK JR",
    ("DER", "REP", "BYLER", "JUANITA"): "JUANITA BYLER",
    ("DER", "REP", "FOREMAN", "JAMES"): "JIM FOREMAN",
    ("DER", "REP", "VASILKO", "JAMES"): "JIM VASILKO",
}


def dos_rows(dos_path):
    """Return county-29 rows from the PA DOS certified bulk precinct-returns
    file, using the official (canvassed) totals for every named contest the
    file carries."""
    out = []
    with open(dos_path, newline="", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            rec = next(csv.reader([line]))
            if len(rec) < 17 or rec[1] != "P" or rec[2] != "29.0":
                continue
            prec_code = str(int(float(rec[3])))
            office_code = rec[8]
            if office_code not in DOS_OFFICES:
                if office_code == "ADR":
                    continue  # zero rows in DOS for Fulton; PDF rows kept
                raise SystemExit("unmapped DOS office code %r" % office_code)
            precinct = PRECINCT_NAMES[prec_code]
            office, districted = DOS_OFFICES[office_code]
            party = rec[9]
            district = ""
            if districted and rec[5].strip() and float(rec[5]):
                district = str(int(float(rec[5])))
            key = (office_code, party, rec[11].strip().upper(),
                   rec[12].strip().upper())
            if key not in DOS_CANDIDATES:
                raise SystemExit("unmapped DOS candidate %r" % (key,))
            votes = int(float(rec[15]))
            out.append([COUNTY, precinct, office, district, party,
                        DOS_CANDIDATES[key], str(votes), "", "", ""])
    return out


def map_office(raw):
    """Normalize an office title to the OpenElections standard."""
    m = " ".join(raw.split()).upper()
    if m.startswith("PRESIDENT OF THE UNITED STATES"):
        return "President", None
    if m.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", None
    if m.startswith("REPRESENTATIVE IN CONGRESS"):
        return "U.S. House", "13"
    if m.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        return "State House", "78"
    if m.startswith("ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION"):
        return "Alternate Delegate to the Republican National Convention", "13"
    if m.startswith("DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION"):
        return "Delegate to the Democratic National Convention", "13"
    if m.startswith("DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION"):
        return "Delegate to the Republican National Convention", "13"
    if m.startswith("ATTORNEY GENERAL"):
        return "Attorney General", None
    if m.startswith("AUDITOR GENERAL"):
        return "Auditor General", None
    if m.startswith("STATE TREASURER"):
        return "State Treasurer", None
    # Local offices as printed
    return m, None


def source_path(municipality, party):
    return os.path.join(
        SOURCE_DIR, "Fulton PA 2024 Primary %s - %s.pdf" % (municipality, party))


def pdf_text(out_dir, municipality, party):
    """Extract one PDF to text (cached) under out_dir."""
    base = "%s_%s.txt" % (municipality, party)
    txt = os.path.join(out_dir, base)
    if not os.path.exists(txt):
        subprocess.check_call([
            "pdftotext", "-layout",
            source_path(municipality, party), txt])
    return txt


def parse_file(path):
    """Yield (rv_total, contests) for one municipality/party report.

    contests: list of (office_raw, party, [(candidate, votes),
        election_day, mail, provisional) ...])
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.readlines()

    # Join wrapped contest headers: the "ALTERNATE DELEGATE TO THE ...
    # CONGRESSIONAL DISTRICT - Republican" headers break before "Party -".
    lines = []
    for line in raw_lines:
        s = line.rstrip()
        if lines and re.search(r"-\s+(Democratic|Republican)$",
                               lines[-1]) and s.startswith("Party"):
            lines[-1] = lines[-1] + " " + s
        else:
            lines.append(s)
    lines = [l + "\n" for l in lines]

    rv_total = None
    contests = []
    cur_office = None
    cur_party = None
    cur_candidates = []

    pair_re = r"(-?\d+)\s+([\d.]+)%"
    header_re = re.compile(r"^(.+?)\s+-\s+(Democratic|Republican)\s+Party")
    name_re = re.compile(r"\s*([A-Za-z(].*?)(?=\s+-?\d+\s+[\d.]+%)")

    def flush():
        nonlocal cur_office, cur_party, cur_candidates
        if cur_office is not None:
            contests.append((cur_office, cur_party, cur_candidates))
        cur_office, cur_party, cur_candidates = None, None, []

    for line in lines:
        stripped = line.strip()
        if "registered voters" in stripped:
            m = re.search(r"(\d+)\s+of\s+\d+\s+registered voters", stripped)
            if m:
                rv_total = int(m.group(1))
            continue
        # Contest header (the vote-for tail may wrap to the next line; the
        # office name itself is complete on the first line)
        m = header_re.match(" ".join(stripped.split()))
        if m and m.group(1) not in ("Choice",):
            flush()
            cur_office = m.group(1).strip()
            cur_party = PARTIES[m.group(2)]
            cur_candidates = []
            continue
        if not stripped or stripped.startswith("Choice"):
            continue
        if stripped.startswith("*** End of report"):
            continue
        if stripped.startswith(("Cast Votes:", "Undervotes", "Overvotes")):
            continue
        # Candidate rows: name text + 4 (votes pct) pairs
        stripped = " ".join(stripped.split())
        pairs = re.findall(pair_re, stripped)
        if len(pairs) == 4 and cur_office is not None:
            m = name_re.match(stripped)
            name = (m.group(1).strip() if m else "").strip()
            if not name:
                continue  # unnamed (blank) placeholder row
            mail, ed, prov, total = (int(p[0]) for p in pairs)
            cur_candidates.append((name, total, ed, mail, prov))

    flush()
    return rv_total, contests


def main():
    out_csv = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    dos_path = sys.argv[2] if len(sys.argv) > 2 else DOS_DEFAULT
    out_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "work2024p", "fulton", "text")
    os.makedirs(out_dir, exist_ok=True)

    # Official-vintage rows from the DOS certified bulk file.
    rows = dos_rows(dos_path)

    # PDF-sourced rows (election-night vintage, documented above):
    # Republican committee-person contests (not carried by DOS) and the
    # alternate-delegate contest (carried as zeros by DOS).  No ballot-type
    # breakdown columns for DOS rows.
    for municipality in MUNICIPALITIES:
        mname = municipality
        for party_word, party_code in PARTIES.items():
            txt = pdf_text(out_dir, municipality, party_word)
            rv_total, contests = parse_file(txt)
            if rv_total is not None:
                # The printed "X of 0 registered voters" panel is actually the
                # ballots cast count for this ballot style (values sum to the
                # cumulative reports' countywide ballots-cast figures and are
                # far below the real per-precinct registered-voter totals in
                # the 2024 general file), so record it as Ballots Cast.
                rows.append([COUNTY, mname, "Ballots Cast", "",
                             party_code, "", str(rv_total), "", "", ""])
            for office_raw, party, cands in contests:
                office, district = map_office(office_raw)
                is_committee = "COMMITTEE PERSON" in office.upper()
                is_adr = office == "Alternate Delegate to the Republican " \
                                  "National Convention"
                if not (is_committee or is_adr):
                    continue
                for name, total, ed, mail, prov in cands:
                    rows.append([COUNTY, mname, office, district, party,
                                 name, str(total), str(ed),
                                 str(mail), str(prov)])

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    print("wrote %d rows to %s" % (len(rows), out_csv))


if __name__ == "__main__":
    main()
