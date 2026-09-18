#!/usr/bin/env python3
"""Perry County 2023 general — county-level results from the official
"Official Final Count" county SOVC summary.

Layout (11 pages, countywide totals only):
    <CONTEST> (Vote for N), <R> registered voters, turnout P%
        <NAME>  <votes>  <pct%>  <ED>  <MI>  <PR>
        Write-in ...
        Total ...

Contests may report more than one "Write-in" line; those are merged into a
single Write-ins row.  There is no party column.

Usage: pa_perry_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import (Rows, smart_title, nums_from,
                                   write_csv, clean_name, check_totals)

CONTEST_RE = re.compile(r"^(.+?) \(Vote for \d+\), (\d[\d,]*) registered voters",
                        re.I)
FURNITURE = re.compile(
    r"^(page:|perry county, pa|all precincts, all districts|total ballots cast:"
    r"|\d?\d precincts reported|choice\b)", re.I)

BOROUGHS = {"Blain", "Bloomfield", "Duncannon", "Landisburg", "Marysville",
            "Millerstown", "New Buffalo", "Newport"}
# Liverpool Township and Liverpool Borough both exist: council -> borough,
# auditor/supervisor -> township
BORO_AUDITORS = {"Landisburg", "New Buffalo"}

FIXED = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "COUNTY COMMISSIONER": "County Commissioner",
    "COUNTY AUDITOR": "County Auditor",
    "COUNTY CORONER": "Coroner",
    "COUNTY TREASURER": "County Treasurer",
    "DISTRICT ATTORNEY": "District Attorney",
    "PROTHONOTARY & CLERK OF COURTS": "Prothonotary & Clerk of Courts",
    "REGISTER & RECORDER AND CLERK OF THE ORPHANS COURT":
        "Register & Recorder and Clerk of the Orphans Court",
}


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip()).upper()

    if h == "RETENTION PANELLA":
        return "Judge of the Superior Court Retention - Jack Panella", ""
    if h == "RETENTION STABILE":
        return "Judge of the Superior Court Retention - Victor P. Stabile", ""
    if h in FIXED:
        return FIXED[h], ""

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (?:\d YR TERM )?"
                 r"(\d{2}-\d-\d{2})$", h)
    if m:
        return "Magisterial District Judge (6 Year)", \
            "Magisterial District " + m.group(1)

    m = re.match(r"^(.+?) (\d) YR TERM (SCHOOL DIRECTOR)$", h)
    if m:
        return f"School Director ({m.group(2)} Year)", \
            smart_title(m.group(1)).replace(" School School", " School")
    m = re.match(r"^(.+?) SCHOOL DIRECTOR (\d) YR TERM$", h)
    if m:
        district = smart_title(m.group(1))
        district = re.sub(r" Region ([IVX]+)$", r" School District Region \1",
                          district)
        if not district.endswith("School District"):
            district += " School District"
        return f"School Director ({m.group(2)} Year)", district

    m = re.match(r"^(.+?) (SUPERVISOR|AUDITOR|COUNCIL|MAYOR|TAX COLLECTOR)"
                 r" (\d) YR TERM$", h)
    if m:
        muni, base, term = smart_title(m.group(1)), m.group(2), m.group(3)
        suffix = "Borough" if muni in BOROUGHS else "Township"
        if base == "SUPERVISOR":
            office = "Township Supervisor"
        elif base == "AUDITOR":
            office = ("Borough Auditor" if muni in BORO_AUDITORS
                      else "Township Auditor")
        elif base == "COUNCIL":
            office = "Borough Council"
            suffix = "Borough"
        elif base == "MAYOR":
            office = "Mayor"
            suffix = "Borough"
        else:
            office = "Tax Collector"
        return f"{office} ({term} Year)", f"{muni} {suffix}"

    return smart_title(h), ""


def parse(text_path, county):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    current = None
    wi = [0, 0, 0, 0]  # total, ed, mi, pr
    saw_wi = False
    rv = None
    bc = None
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        m = CONTEST_RE.match(stripped)
        if m:
            if current is not None and saw_wi:
                R.add(current[0], current[1], "", "Write-ins",
                      wi[0], wi[1], wi[2], wi[3])
            if m:
                wi = [0, 0, 0, 0]
                saw_wi = False
            office, dist = map_contest(m.group(1))
            current = (office, dist)
            R.contests.append(current)
            continue
        if FURNITURE.match(stripped):
            continue

        toks = stripped.split()
        lead = toks[0].upper().rstrip(":") if toks else ""
        nums = nums_from(toks)
        if not nums:
            continue

        if stripped.upper().startswith("WRITE-IN"):
            saw_wi = True
            n = nums[:4]
            wi[0] += int(n[0])
            for k in (1, 2, 3):
                wi[k] += int(n[k]) if len(n) > k else 0
            continue
        if current is None:
            m2 = re.match(r"^Total Ballots Cast: (\d+), "
                          r"Registered Voters: (\d+)", stripped, re.I)
            if m2:
                bc, rv = int(m2.group(1)), int(m2.group(2))
            continue
        if lead == "TOTAL":
            nums4 = nums[:4]
            R.totals[current] = int(nums4[0])
            continue
        # candidate row: name tokens then votes, ED, MI, PR
        head = []
        for t in toks:
            if re.fullmatch(r"\d+", t.replace(",", "")):
                break
            head.append(t)
        rest = nums_from(toks[len(head):])
        if head and rest:
            n = rest[:4]
            R.add(current[0], current[1], "", clean_name(" ".join(head), True),
                  n[0], n[1] if len(n) > 1 else "",
                  n[2] if len(n) > 2 else "", n[3] if len(n) > 3 else "")
    if current is not None and saw_wi:
        R.add(current[0], current[1], "", "Write-ins",
              wi[0], wi[1], wi[2], wi[3])
    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc is not None:
        R.add_meta("Ballots Cast", bc)
    return R


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit("Usage: pa_perry_general_2023_results_parser.py "
                         "<input> <output>")
    R = parse(args[0], "Perry")
    n = write_csv(args[1], R)
    print(f"wrote {n} rows -> {args[1]}")
    print(f"contests parsed: {len(R.contests)}")
    bad = check_totals(R)
    if bad:
        for (office, dist), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != Total {t}")
    else:
        print("contest totals check: all contests reconcile")