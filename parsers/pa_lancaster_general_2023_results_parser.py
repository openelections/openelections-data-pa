#!/usr/bin/env python3
"""Parse the Lancaster County 2023 General certified results HTML.

Source: Lancaster County Election Systems & Voter Registration returns portal
(vr.co.lancaster.pa.us), "Certified Official Results" page. The file is a
concatenation of the downloaded sub-pages (separated by <hr>), each containing
one contest's countywide totals (Election Day / Mail-In / Provisional / Total),
a Write-In row, and a "by Precinct" totals row. The per-precinct tables live on
remote "*ByPrecinct.html" pages that were NOT included in the capture, so this
file is countywide-only: the parser emits county-level rows (precinct column
absent by design; see work2023/validate/lancaster.md).

Usage:
    python pa_lancaster_general_2023_results_parser.py <input.html> <output.csv> [--county Lancaster]

The output CSV has the county-level schema:
    county,office,district,party,candidate,votes,election_day,mail,provisional
"""

import argparse
import csv
import re
import sys

from bs4 import BeautifulSoup

# Parties are not printed in the certified HTML. Party for the 2023 statewide
# judicial candidates is taken from the official candidate listings.
STATEWIDE_PARTY = {
    "daniel mccaffery": "DEM",
    "carolyn carluccio": "REP",
    "jill beck": "DEM",
    "timika lane": "DEM",
    "maria battista": "REP",
    "harry f smail jr": "REP",
    "harry f. smail jr.": "REP",
    "matt wolf": "DEM",
    "megan martin": "REP",
}


def split_contest_name(name):
    """Map a contest header to (office, district) per the 2023 SPEC."""
    n = re.sub(r"\s+", " ", name).strip()
    upper = n.upper()

    # Retention questions: "JUDGE OF THE SUPERIOR COURT RETENTION ELECTION - Jack Panella"
    m = re.match(r"^(JUDGE OF THE (?:SUPERIOR COURT|COURT OF COMMON PLEAS)) "
                 r"RETENTION ELECTION - (.+)$", n, re.I)
    if m:
        office = re.sub(r"\s+", " ", m.group(1)).title().replace("Of The", "of the")
        office = office.replace("Judge of the Superior Court",
                                "Superior Court Retention Election Question")
        office = office.replace("Judge of the Court of Common Pleas",
                                "Court of Common Pleas Retention Election Question")
        return office + " - " + m.group(2).strip(), ""

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d+)$", upper)
    if m:
        return "Magisterial District Judge", m.group(1)

    m = re.match(r"^SCHOOL DIRECTOR (.+?)(?:\s*-\s*(\d) YR)?$", upper)
    if m:
        district = n[len("SCHOOL DIRECTOR "):]
        if m.group(2):
            return "School Director (%s Year)" % m.group(2), district.strip()
        return "School Director", district.strip()

    m = re.match(r"^COUNCIL LANCASTER CITY(?:\s*-\s*(\d) YR)?$", upper)
    if m:
        office = "City Council" + (" (%s Year)" % m.group(1) if m.group(1) else "")
        return office, "Lancaster City"

    m = re.match(r"^COUNCIL (.+?)(?:\s*-\s*(\d) YR)?$", upper)
    if m:
        district = n[len("COUNCIL "):]
        office = "Borough Council"
        if m.group(2):
            office += " (%s Year)" % m.group(2)
        return office, district.strip()

    m = re.match(r"^COMMISSIONER (.+)$", upper)
    if m:
        return "Township Commissioner", n[len("COMMISSIONER "):].strip()

    m = re.match(r"^SUPERVISOR (.+?)(?:\s*-\s*(\d) YR)?$", upper)
    if m:
        district = n[len("SUPERVISOR "):]
        office = "Township Supervisor"
        if m.group(2):
            office += " (%s Year)" % m.group(2)
        return office, district.strip()

    m = re.match(r"^AUDITOR (.*?)\s*-\s*(\d) YR$", upper)
    if m:
        return _auditor_office(m.group(1), m.group(2) + " Year")
    if upper.startswith("AUDITOR "):
        return _auditor_office(n[len("AUDITOR "):].strip(), None)

    m = re.match(r"^TAX COLLECTOR (.+?)(?:\s*-\s*(\d)\s*YR)?$", upper)
    if m:
        district = n[len("TAX COLLECTOR "):]
        district = re.sub(r"\s*-\s*\d\s*YR\s*$", "", district, flags=re.I)
        office = "Tax Collector"
        if m.group(2):
            office += " (%s Year)" % m.group(2)
        return office, district.strip()

    # Countywide / statewide offices: title-case the header.
    office = re.sub(r"\s+", " ", n).title()
    office = re.sub(r"\bOf The\b", "of the", office)
    office = office.replace("Court Of Common Pleas", "Court of Common Pleas")
    return office, ""


def _auditor_office(muni, term):
    office = "Township Auditor" if re.search(r"\bTwp\.?$", muni, re.I) else "Borough Auditor"
    if term:
        office += " (%s)" % term
    return office, muni


def parse(input_path, county="Lancaster"):
    raw = open(input_path, encoding="utf-8", errors="replace").read()
    segments = [s for s in raw.split("<hr") if "<html" in s.lower()]

    rows = []       # output rows
    problems = []   # validation problems

    for seg in segments:
        soup = BeautifulSoup(seg, "html.parser")
        h3 = soup.find("h3")
        if h3 is None or not re.match(r"^Contest \d+$", h3.get_text(strip=True)):
            continue
        err_h = soup.find(string=re.compile(
            r"404 - File or directory not found\.|The resource you are looking for"))
        if err_h is not None:
            problems.append("%s: source page missing (server error)" % h3.get_text(strip=True))
            continue

        name_td = soup.find("td", colspan="3")
        contest_name = name_td.get_text(" ", strip=True)
        office, district = split_contest_name(contest_name)

        # locate the results table
        table = None
        for tbl in soup.find_all("table"):
            hdr = [c.get_text(" ", strip=True) for c in tbl.find_all(["th", "td"])]
            if hdr and hdr[0] in ("Candidate", "Response"):
                table = tbl
                break
        if table is None:
            problems.append("%s: no results table" % contest_name)
            continue

        contest_rows = []
        col_totals = None
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue
            label = cells[0]
            if label in ("Candidate", "Response"):
                continue
            if label.startswith("by Precinct"):
                col_totals = cells[1:]
                continue
            if not label or label in ("Return to list of  categories", "Return"):
                continue
            nums = cells[1:]
            if len(nums) < 4:
                problems.append("%s / %s: short row %r" % (contest_name, label, cells))
                continue
            try:
                ed, mail, prov, total = (int(nums[i].replace(",", "")) for i in range(4))
            except ValueError:
                problems.append("%s / %s: bad numbers %r" % (contest_name, label, nums))
                continue
            candidate = "Write-ins" if label == "Write-In" else label
            party = ""
            if candidate != "Write-ins":
                party = STATEWIDE_PARTY.get(candidate.lower(), "")
            rows.append({
                "county": county, "office": office, "district": district,
                "party": party, "candidate": candidate,
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov,
            })
            if ed + mail + prov != total:
                problems.append("%s / %s: ED+Mail+Prov=%d != Total=%d"
                                % (contest_name, candidate, ed + mail + prov, total))

        # cross-check against the "by Precinct" totals row
        if col_totals:
            try:
                t_ed, t_mail, t_prov, t_total = (
                    int(col_totals[i].replace(",", "")) for i in range(4))
            except (ValueError, IndexError):
                problems.append("%s: bad by-Precinct totals %r" % (contest_name, col_totals))
                continue
            mine = [sum(r[c] for r in rows
                        if r["office"] == office and r["district"] == district)
                    for c in ("election_day", "mail", "provisional", "votes")]
            if mine != [t_ed, t_mail, t_prov, t_total]:
                problems.append(
                    "%s: summed rows %r != by-Precinct totals %r"
                    % (contest_name, mine, [t_ed, t_mail, t_prov, t_total]))

    return rows, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--county", default="Lancaster")
    args = ap.parse_args(argv)

    rows, problems = parse(args.input, args.county)
    cols = ["county", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print("wrote %d rows to %s" % (len(rows), args.output))
    if problems:
        print("VALIDATION PROBLEMS (%d):" % len(problems), file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())