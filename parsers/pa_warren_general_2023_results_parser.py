#!/usr/bin/env python3
"""
Parse Warren County PA 2023 General (Municipal) Election results.

Source: "Warren County Official Election Results 2023 General.pdf" (43 pages,
Dominion "Election Summary Report", OFFICIAL RESULTS).  This report is
CONTEST-level only: each contest section aggregates over the precincts where
it was on the ballot ("Precincts Reported: n of m") and shows
Election Day / Mail-In / Provisional / Total columns.  The source contains NO
per-precinct rows, so the finest available granularity is countywide.

Outputs (to be generated together from the same input):
  * county-level CSV (directly from this parser);
  * a precinct CSV in the standard 10-column schema whose single
    ``precinct`` value is the pseudo-precinct "Warren County" -- every
    number in it comes straight from the source; it exists only so the
    standard two-file layout is preserved and it MUST NOT be read as
    precinct-level detail (see work2023/validate/warren.md).

Contest header mapping (office, district):
  * "Magisterial District Judge 37-2-01 for Warren County" -> office
    "Magisterial District Judge", district "37-2-01" (same for 37-3-01);
  * "Statewide Referendum Superior Court Retention 1" -> "Superior Court
    Retention - Jack Panella"; "... Retention 2" -> "Superior Court
    Retention - Victor P. Stabile" (both 2023 retentions are Superior Court
    seats; Yes/No rows);
  * "Register & Recorder / Clerk of Orphans Court" kept as printed;
  * "Treasurer" is the county row office -> "County Treasurer";
  * "City Council Member (N Year Term)" -> "City Council (N Year)",
    district "Warren City" (Warren City is the county's only city; the
    companion City Constable headers all say "Warren ...");
  * "City Constable - Warren <Ward>" -> "City Constable", district
    "Warren <Ward>";
  * "Township Supervisor/Auditor/Constable (N Year Term) - <Twp>" and
    "Borough Council/Auditor ... <Boro>" -> office with "(N Year)" suffix,
    district = municipality;
  * "Townhip Auditor- (6 Year Term) Deerfield Township" (source typo
    "Townhip") -> "Township Auditor (6 Year)", district "Deerfield Township";
  * "Region 1/2/3 School Board Members (N Year Term)" are the Warren County
    School District regions -> office "School Director Region N (N Year)",
    district "Warren County"; "Corry Area" / "Titusville" school boards ->
    district "Corry Area" / "Titusville Area".

Usage:
    python parsers/pa_warren_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--precinct] [--county Warren]

Default output is the county-level 9-column CSV.  With --precinct the output
is the 10-column schema (county,precinct,...) using the "Warren County"
pseudo-precinct for every row.
"""

import re
import sys

try:
    from pa_2023_summary_common import (Rows, FIELDNAMES, check_totals,
                                        clean_name, parse_esr2, text_from_pdf,
                                        vals_from, write_csv)
except ImportError:  # allow running from repo root
    sys.path.insert(0, __import__("os").path.join(
        __import__("os").path.dirname(__file__)))
    from pa_2023_summary_common import (Rows, FIELDNAMES, check_totals,
                                        clean_name, parse_esr2, text_from_pdf,
                                        vals_from, write_csv)

COUNTY = "Warren"

PRECINCT_PSEUDO = "Warren County"


def map_contest(header):
    h = re.sub(r"\s+", " ", header).strip()
    # strip the trailing "(Vote for N)"
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h, flags=re.IGNORECASE)
    hu = h.upper()

    if hu == "JUSTICE OF THE SUPREME COURT":
        return ("Justice of the Supreme Court", "")
    if hu == "JUDGE OF THE SUPERIOR COURT":
        return ("Judge of the Superior Court", "")
    if hu == "JUDGE OF THE COMMONWEALTH COURT":
        return ("Judge of the Commonwealth Court", "")
    if hu == "SHERIFF":
        return ("Sheriff", "")
    if hu == "TREASURER":
        return ("County Treasurer", "")
    if hu == "COUNTY AUDITOR":
        return ("County Auditor", "")
    if hu == "COUNTY COMMISSIONERS":
        return ("County Commissioner", "")
    if hu.startswith("REGISTER & RECORDER"):
        return ("Register & Recorder / Clerk of Orphans Court", "")

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})", hu)
    if m:
        return ("Magisterial District Judge", m.group(1))

    m = re.match(r"^STATEWIDE REFERENDUM SUPERIOR COURT RETENTION (\d)$", hu)
    if m:
        return ("Superior Court Retention - "
                + ("Jack Panella" if m.group(1) == "1"
                   else "Victor P. Stabile"), "")

    m = re.match(r"^CITY COUNCIL MEMBER \((\d) YEAR TERM\)$", hu)
    if m:
        return (f"City Council ({m.group(1)} Year)", "Warren City")

    m = re.match(r"^CITY CONSTABLE - (WARREN .+)$", hu)
    if m:
        return ("City Constable", m.group(1).title().replace("Of The", "of the"))

    m = re.match(r"^TOWN?SHIP (SUPERVISOR|AUDITOR|CONSTABLE) \((\d) YEAR TERM\)\s*-\s*(.+)$", hu)
    if m:
        suffix = {"SUPERVISOR": "Township Supervisor",
                  "AUDITOR": "Township Auditor",
                  "CONSTABLE": "Township Constable"}[m.group(1)]
        return (f"{suffix} ({m.group(2)} Year)", m.group(3).strip().title())

    m = re.match(r"^BOROUGH COUNCIL \((\d) YEAR TERM\) (.+)$", hu)
    if m:
        return (f"Borough Council ({m.group(1)} Year)",
                m.group(2).strip().title())
    m = re.match(r"^BOROUGH AUDITOR \((\d) YEAR TERM\) - (.+)$", hu)
    if m:
        return (f"Borough Auditor ({m.group(1)} Year)",
                m.group(2).strip().title())

    m = re.match(r"^REGION (\d) SCHOOL BOARD MEMBERS? \((\d) YEAR TERM\)$", hu)
    if m:
        return (f"School Director Region {m.group(1)} ({m.group(2)} Year)",
                "Warren County")
    m = re.match(r"^(CORRY AREA|TITUSVILLE) SCHOOL BOARD MEMBERS? \((\d) YEAR TERM\)$", hu)
    if m:
        name = "Corry Area" if m.group(1) == "CORRY AREA" else "Titusville Area"
        return (f"School Director ({m.group(2)} Year)", name)

    return None


def parse_warren(text_path, county):
    """parse_esr2 plus the Ballots Cast breakdown from the Times Cast row."""
    R = parse_esr2(text_path, county, map_contest, titlecase_names=False)

    # The header's "Ballots Cast" has no breakdown in R; recover it from the
    # contest-level "Times Cast" row (identical for every countywide
    # contest: Election Day, Mail-In, Provisional sum to Ballots Cast).
    with open(text_path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^Times Cast\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)",
                  text, re.M)
    if m:
        ed, mi, pr, tot = (int(x.replace(",", "")) for x in m.groups())
        if ed + mi + pr == tot:
            for r in R.rows:
                if r[1] == "Ballots Cast":
                    r[6], r[7], r[8] = str(ed), str(mi), str(pr)
    return R


PRECINCT_FIELDNAMES = ["county", "precinct"] + FIELDNAMES[1:]


def write_precinct_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv_writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        for r in rows.rows:
            w.writerow([r[0], PRECINCT_PSEUDO] + r[1:])


def csv_writer(fh):
    import csv
    return csv.writer(fh)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--county"]
    precinct_mode = "--precinct" in args
    args = [a for a in args if a != "--precinct"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input> <output> [--precinct] [--county NAME]")
    src, dst = args
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    R = parse_warren(src, COUNTY)
    if precinct_mode:
        write_precinct_csv(dst, R)
    else:
        write_csv(dst, R)
    print(f"wrote {len(R.rows)} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")
    bad = check_totals(R)
    if bad:
        for (office, dist), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != Total Votes {t}")
    else:
        print("contest totals check: all contests reconcile")