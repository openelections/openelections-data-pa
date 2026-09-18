#!/usr/bin/env python3
"""Parse the Delaware County 2023 General "Cumulative Results" HTML (Clarity).

The source is the Clarity Elections Count_HtmlCumulativeReport: one official
countywide table per contest with Election Day / By Mail / Provisional / Total
columns, plus "Unresolved write-in votes" rows. It contains NO precinct detail
(only a "Precincts Reporting 428 of 428" header), so the parser emits
county-level rows:

    county,office,district,party,candidate,votes,election_day,mail,provisional

Registered Voters (TotalRegisteredVoters) and Ballots Cast
(TotalTabulatedBallots) are taken from the report's META tags. Per-contest
"Cast Votes" totals are used only as an internal cross-check (candidate rows +
write-ins must equal them).

Usage:
    python pa_delaware_general_2023_results_parser.py <input.html> <output.csv> [--county Delaware]
"""

import argparse
import csv
import re
import sys

from bs4 import BeautifulSoup

NUM = re.compile(r"^\d[\d,]*$")


def numbers_from_cells(cells):
    """Return the leading numeric (non-percentage) values of a row.

    Clarity duplicates each value cell (colspan artifacts), e.g.
    [label, label, '', '150', '150', '', '', '26', '26', ...], so drop a
    numeric cell that is an exact repeat of the immediately preceding cell.
    """
    out = []
    prev = None
    for c in cells:
        cur = c.replace(",", "").strip()
        if cur.endswith("%"):
            prev = "%"
            continue
        if NUM.match(cur):
            if cur == prev:
                prev = cur
                continue
            out.append(int(cur))
        prev = cur
    return out


def split_contest_name(name):
    """Map a Clarity contest header to (office, district) per the 2023 SPEC."""
    n = re.sub(r"\s+", " ", name).strip()
    # strip the "(Vote for ...)" tail
    n = re.sub(r"\s*-\s*\(Vote for[^)]*\)\s*", " ", n).strip()
    upper = n.upper()

    # "Superior Court Retention Question - Jack Panella" ->
    # office "Superior Court Retention - Jack Panella", district ""
    m = re.match(r"^(.*?)\s+Retention Question\s*-\s*(.+)$", n)
    if m:
        return "%s Retention - %s" % (m.group(1).strip(), m.group(2).strip()), ""

    m = re.match(r"^(.*)\s+(2 Year MidTerm|\d Year Term)$", n)
    term = None
    if m:
        n, term_txt = m.group(1).strip(), m.group(2)
        if term_txt == "2 Year MidTerm":
            term = "2 Year"

    def fin(office, district):
        if term:
            office += " (%s)" % term
        return office, district

    if upper.startswith("MAGISTERIAL DISTRICT JUDGE "):
        return "Magisterial District Judge", n[len("Magisterial District Judge "):].strip()

    if upper.startswith("SCHOOL DIRECTOR "):
        return fin("School Director", n[len("School Director "):].strip())

    m = re.match(r"^(.*?)\s+Twp Auditor$", n, re.I)
    if m:
        return fin("Township Auditor", m.group(1) + " Twp")
    m = re.match(r"^(.*?)\s+Bor Auditor$", n, re.I)
    if m:
        return fin("Borough Auditor", m.group(1) + " Bor")
    m = re.match(r"^(.*?)\s+Bor Tax Collector$", n, re.I)
    if m:
        return fin("Tax Collector", m.group(1) + " Bor")
    m = re.match(r"^(.*?)\s+Bor Constable$", n, re.I)
    if m:
        return fin("Constable", m.group(1) + " Bor")
    m = re.match(r"^(.*?)\s+Twp Supervisor$", n, re.I)
    if m:
        return fin("Township Supervisor", m.group(1) + " Twp")
    m = re.match(r"^(.*?)\s+Twp Mayor$", n, re.I)
    if m:
        return fin("Mayor", m.group(1) + " Twp")
    m = re.match(r"^(.*?)\s+City Mayor$", n, re.I)
    if m:
        return fin("Mayor", m.group(1) + " City")
    m = re.match(r"^(.*?)\s+City City Controller$", n, re.I)
    if m:
        return fin("City Controller", m.group(1) + " City")
    m = re.match(r"^(.*?)\s+City City Council$", n, re.I)
    if m:
        return fin("City Council", m.group(1) + " City")
    m = re.match(r"^(.*?)\s+Twp Council-At-Large$", n, re.I)
    if m:
        return fin("Township Council", m.group(1) + " Twp - At Large")
    m = re.match(r"^(.+?)\s+(\d+(?:st|nd|rd|th)) District Township Council$", n, re.I)
    if m:
        return fin("Township Council", "%s - %s District" % (m.group(1), m.group(2)))
    m = re.match(r"^(.+?)\s+(\d+(?:st|nd|rd|th)) Ward Borough Council$", n, re.I)
    if m:
        return fin("Borough Council", "%s - %s Ward" % (m.group(1), m.group(2)))
    m = re.match(r"^(.*?)\s+Twp Commissioner$", n, re.I)
    if m:
        return fin("Township Commissioner", m.group(1) + " Twp")
    m = re.match(r"^(.+?)\s+(\d+(?:st|nd|rd|th)) Ward Commissioner$", n, re.I)
    if m:
        return fin("Township Commissioner", "%s - %s Ward" % (m.group(1), m.group(2)))
    if re.match(r"^.*\s+Borough Council$", n, re.I):
        return fin("Borough Council", re.sub(r"\s+Borough Council$", "", n, flags=re.I))
    m = re.match(r"^(.*?)\s+Township Council$", n, re.I)
    if m:
        return fin("Township Council", m.group(1) + " Township")

    # countywide / statewide
    office = re.sub(r"\s+", " ", n)
    office = re.sub(r"\bOf\b", "of", office)
    office = office.replace("Court Of Common Pleas", "Court of Common Pleas")
    return fin(office, "")


def parse(input_path, county="Delaware"):
    raw = open(input_path, encoding="utf-8", errors="replace").read()
    soup = BeautifulSoup(raw, "html.parser")

    meta = {}
    for m in soup.find_all("meta"):
        if m.get("name") and m.get("content") is not None:
            meta[m["name"]] = m["content"]
    rv = int(meta.get("TotalRegisteredVoters", "0") or 0)
    bc = int(meta.get("TotalTabulatedBallots", "0") or 0)

    headers = [s for s in soup.find_all(string=re.compile(r"\(Vote for|Retention Question"))]

    rows = [{
        "county": county, "office": "Registered Voters", "district": "",
        "party": "", "candidate": "", "votes": rv,
        "election_day": "", "mail": "", "provisional": "",
    }, {
        "county": county, "office": "Ballots Cast", "district": "",
        "party": "", "candidate": "", "votes": bc,
        "election_day": "", "mail": "", "provisional": "",
    }]

    problems = []
    seen_contests = set()
    for h in headers:
        contest_name = re.sub(r"\s+", " ", h.strip())
        if contest_name in seen_contests:
            # The source prints two byte-identical "School Director Penn Delco
            # School District ... 2 Year MidTerm" headers for two DIFFERENT
            # seats (an unopposed special-election seat and a contested
            # unexpired-term seat; see work2023/validate/delaware.md).
            if "Penn Delco School District" in contest_name:
                contest_name = contest_name.replace(
                    "Penn Delco School District",
                    "Penn Delco School District - Unexpired Term 2", 1)
            else:
                continue
        seen_contests.add(contest_name)
        office, district = split_contest_name(contest_name)

        tbl = h
        while tbl is not None and tbl.name != "table":
            tbl = tbl.parent
        if tbl is None:
            problems.append("%s: no table" % contest_name)
            continue

        cast_votes = None
        cast_by_col = None
        for tr in tbl.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not any(cells):
                continue
            label = cells[0].strip()
            if label.startswith("Cast Votes"):
                nums = numbers_from_cells(cells)
                if len(nums) >= 4:
                    cast_by_col = nums
                continue
            if label.startswith("Unresolved write-in votes"):
                nums = numbers_from_cells(cells)
                if len(nums) < 4:
                    continue  # nested-table artifact; the full row parses the numbers
                ed, mail, prov, total = nums[:4]
                rows.append({
                    "county": county, "office": office, "district": district,
                    "party": "", "candidate": "Write-ins",
                    "votes": total, "election_day": ed, "mail": mail,
                    "provisional": prov,
                })
                if ed + mail + prov != total:
                    problems.append("%s / Write-ins: ED+Mail+Prov=%d != Total=%d"
                                    % (contest_name, ed + mail + prov, total))
                continue
            # retention tables label choices plainly as Yes / No
            if label in ("Yes", "No"):
                cand, party = label, ""
            else:
                m = re.match(r"^\(\d+\)\s+(.+)$", label)
                if not m:
                    continue
                cand = m.group(1).strip()
                party = cells[1].strip() if len(cells) > 1 and cells[1].strip() else ""
                if party in ("DEM, REP", "REP, DEM"):
                    party = "D/R"
            nums = numbers_from_cells(cells)
            if len(nums) < 4:
                problems.append("%s / %s: short row %r" % (contest_name, cand, cells))
                continue
            ed, mail, prov, total = nums[0], nums[1], nums[2], nums[3]
            rows.append({
                "county": county, "office": office, "district": district,
                "party": party, "candidate": cand,
                "votes": total, "election_day": ed, "mail": mail,
                "provisional": prov,
            })
            if ed + mail + prov != total:
                problems.append("%s / %s: ED+Mail+Prov=%d != Total=%d"
                                % (contest_name, cand, ed + mail + prov, total))

        # cross-check: candidate rows only (Cast Votes excludes unresolved write-ins)
        mine = {}
        for r in rows:
            if (r["office"] == office and r["district"] == district
                    and r["candidate"] and r["candidate"] != "Write-ins"):
                for c in ("votes", "election_day", "mail", "provisional"):
                    mine[c] = mine.get(c, 0) + r[c]
        if cast_by_col and len(cast_by_col) >= 4:
            want = {"election_day": cast_by_col[0], "mail": cast_by_col[1],
                    "provisional": cast_by_col[2], "votes": cast_by_col[3]}
            for c, v in want.items():
                if mine.get(c, 0) != v:
                    problems.append(
                        "%s: summed candidate %s=%d != Cast Votes=%d"
                        % (contest_name, c, mine.get(c, 0), v))

    return rows, problems, len(seen_contests)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--county", default="Delaware")
    args = ap.parse_args(argv)

    rows, problems, ncontests = parse(args.input, args.county)
    cols = ["county", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional"]
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    print("wrote %d rows (%d contests) to %s" % (len(rows), ncontests, args.output))
    if problems:
        print("VALIDATION PROBLEMS (%d):" % len(problems), file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())