#!/usr/bin/env python3
"""Parse Bradford County PA 2023 general election "Election Detail Report" text.

Source: official Bradford County "Election Detail Report" (FINAL RESULTS),
60 pages, one county-level summary block per contest (no per-precinct detail).
Pre-extracted with `pdftotext -layout` into
work2023/txt/Bradford__Bradford_County_2023_General_Results.txt.

Each contest block looks like:

    JUSTICE OF THE SUPREME COURT
    Number of Precincts                         61
    Precincts Reporting                         61       100.00%
    Vote For 1
    Total Votes 13,418
                                                          Total               ED    ABS                P               M-I
     Daniel McCaffery (DEM)                            3891              2413    89                8             1381
     Write-in                                            25                21     0                0                4
     Undervote                                          357 ...
     Overvote                                             0 ...

Column mapping into the OpenElections schema (documented in
work2023/validate/bradford.md): ED -> election_day, P -> provisional,
and ABS + M-I are MERGED -> mail (this county reports absentee and
mail-in separately but the standard schema has a single `mail` column).
Undervote/Overvote rows are skipped; "Write-in" rows become candidate
"Write-ins" with an empty party; "(W)" (write-in nominee) candidates keep
their printed name with an empty party.

Contest routing (the report has no per-precinct breakdown anywhere, so
the finest available granularity is used):

- Single-municipality contests ("Number of Precincts 1..5" with a
  `<MUNICIPALITY> <office>` title) -> the PRECINCT CSV, with
  precinct = district = municipality name (ward number appended for
  ward-only contests, e.g. "Towanda Borough Ward 1").
- Countywide contests (Number of Precincts 61), Magisterial District
  Judge districts, multi-municipality school-director contests and
  retention questions -> the COUNTY-LEVEL CSV only
  (district empty for countywide, district = "42-3-0x" for MDJ,
  district = school district (and region) for school director,
  district empty for retentions).

Term suffixes ("4 Year Term", "6 Year Term", ...) are kept as
"(N Year)" suffixes on the office name whenever a single office type
appears with more than one term length in the source (e.g.
"Borough Auditor (6 Year)" vs "Borough Auditor (4 Year)"); single-term
offices drop the suffix. Metadata rows (Registered Voters / Ballots
Cast, from the per-page header) go to the county-level CSV only, with
empty breakdown columns.

Usage:
    python3 parsers/pa_bradford_general_2023_results_parser.py <input_txt> <precinct_csv> [county_extras_csv]

  <input_txt>          path to the pdftotext -layout text of the report
  <precinct_csv>       output precinct-level CSV (single-municipality contests)
  [county_extras_csv]  optional output for the county-level extras: metadata
                       rows plus the countywide / multi-municipality contests
                       that have no precinct rows.  The final county-level
                       file is this file merged with the output of
                       `python3 parsers/aggregate_county.py <precinct_csv> <agg.csv>`.
"""
from __future__ import annotations

import csv
import re
import sys

PRECINCT_FIELDS = ["county", "precinct", "office", "district", "party",
                   "candidate", "votes", "election_day", "mail", "provisional"]
COUNTY_FIELDS = ["county", "office", "district", "party", "candidate",
                 "votes", "election_day", "mail", "provisional"]

COUNTY = "Bradford"

# Lines that belong to the repeating page header/footer, never to a contest.
PAGE_HEADER_RE = re.compile(
    r"^(Election Detail Report|BRADFORD COUNTY|MUNICIPAL ELECTION|"
    r"NOVEMBER \d+, \d{4}|General \d{4} Election Day|FINAL RESULTS)$"
    r"|^Registered Voters [\d,]+ - Total Ballots|Page \d+/")

CANDIDATE_RE = re.compile(
    r"^\s+(\S.*?)\s{2,}([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$")
PARTY_RE = re.compile(r"^(.*?)\s*\((DEM|REP|KEY|W)\)$")
TERM_RE = re.compile(r"\s+(\d+) Year Term\s*$")

# Contest titles that are not municipality-scoped, mapped to standard names.
STATEWIDE_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
}
COUNTY_OFFICES = {
    "COUNTY AUDITOR": "County Auditor",
    "COUNTY COMMISSIONER": "County Commissioner",
    "CORONER": "Coroner",
    "DISTRICT ATTORNEY": "District Attorney",
    "PROTHONOTARY AND CLERK OF COURTS": "Prothonotary and Clerk of Courts",
    "REGISTER - RECORDER": "Register & Recorder",
    "SHERIFF": "Sheriff",
    "COUNTY TREASURER": "County Treasurer",
}

LOCAL_OFFICE_NAMES = {
    "COUNCILMAN": "Borough Council",
    "COUNCILWOMAN": "Borough Council",
    "SUPERVISOR": "Township Supervisor",
    "TAX COLLECTOR": "Tax Collector",
    "MAYOR": "Mayor",
}


def to_int(s: str) -> int:
    return int(s.replace(",", ""))


def titlecase(s: str) -> str:
    return s.title().strip()


def parse_contests(lines):
    """Return a list of contest dicts parsed from the report text."""
    contests = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].startswith("Number of Precincts"):
            i += 1
            continue
        num_precincts = to_int(lines[i].split()[-1])

        # Contest title: nearest line above that is not blank / page header.
        j = i - 1
        while j >= 0 and (not lines[j].strip() or PAGE_HEADER_RE.search(lines[j].strip())):
            j -= 1
        if j < 0:
            raise ValueError(f"no title found above line {i + 1}")
        title = lines[j].strip()

        contest = {
            "title": title,
            "num_precincts": num_precincts,
            "vote_for": None,
            "total_votes": None,
            "rows": [],  # (name, total, ed, abs, p, mi)
        }
        i += 1
        while i < n and not lines[i].startswith("Number of Precincts"):
            s = lines[i].strip()
            if s.startswith("Vote For "):
                contest["vote_for"] = int(s.split()[-1])
            elif s.startswith("Total Votes"):
                contest["total_votes"] = to_int(s.split()[-1])
            elif s and not PAGE_HEADER_RE.match(s):
                m = CANDIDATE_RE.match(lines[i])
                if m:
                    name = m.group(1).strip()
                    contest["rows"].append(
                        (name, to_int(m.group(2)), to_int(m.group(3)),
                         to_int(m.group(4)), to_int(m.group(5)), to_int(m.group(6))))
            i += 1
        if not contest["rows"]:
            raise ValueError(f"no candidate rows for contest {title!r}")
        contests.append(contest)
    return contests


def check_breakdowns(contests):
    """Verify each source row satisfies ED + ABS + P + M-I == Total."""
    bad = []
    for c in contests:
        for name, total, ed, abs_, p, mi in c["rows"]:
            if ed + abs_ + p + mi != total:
                bad.append((c["title"], name, total, ed, abs_, p, mi))
    return bad


def split_candidate(raw: str):
    """Return (candidate, party) from a printed candidate cell."""
    if raw == "Write-in":
        return "Write-ins", ""
    m = PARTY_RE.match(raw)
    if m:
        name, party = m.group(1).strip(), m.group(2)
        if party == "W":  # write-in nominee: keep name, no party
            return name, ""
        return name, party
    return raw, ""


def classify(contest):
    """Route one contest to ('precinct'|'county') and return office/district/etc."""
    title = contest["title"]
    t = TERM_RE.sub("", title).strip()
    term = (TERM_RE.search(title).group(1) if TERM_RE.search(title) else None)

    if "RETENTION" in t:
        m = re.match(r"^(.+?)\s+RETENTION\s+(.+)$", t)
        return "county", {
            "office": f"{m.group(1).title()} Retention - {m.group(2).title()}",
            "district": "",
            "term": None,
        }
    if t.startswith("MAGISTERIAL DISTRICT JUDGE"):
        m = re.match(r"^MAGISTERIAL DISTRICT JUDGE\s+(\d{2}-\d-\d+)$", t)
        return "county", {
            "office": "Magisterial District Judge",
            "district": m.group(1),
            "term": None,
        }
    if "SCHOOL DIRECTOR" in t:
        district = re.match(r"^(.+?)\s+SCHOOL DIRECTOR$", t).group(1)
        return "county", {
            "office": "School Director",
            "district": district.title(),
            "term": term,
        }
    if t in STATEWIDE_OFFICES:
        return "county", {"office": STATEWIDE_OFFICES[t], "district": "", "term": None}
    if t in COUNTY_OFFICES:
        return "county", {"office": COUNTY_OFFICES[t], "district": "", "term": None}
    if contest["num_precincts"] == 61:
        raise ValueError(f"unrecognized countywide contest: {title!r}")

    # Single-municipality contest: "<MUNI> [WARD n] <office> [N Year Term]"
    m = re.match(
        r"^(?P<base>.+?\s+(?:BOROUGH|TOWNSHIP))(?P<ward>\s+WARD\s+\d+)?\s+(?P<office>.+)$",
        t)
    if not m:
        raise ValueError(f"unrecognized local contest: {title!r}")
    base, ward, office_part = m.group("base"), m.group("ward"), m.group("office")
    locality = base + (ward or "")
    if office_part in ("AUDITOR",):
        office = "Borough Auditor" if base.endswith("BOROUGH") else "Township Auditor"
    elif office_part in LOCAL_OFFICE_NAMES:
        office = LOCAL_OFFICE_NAMES[office_part]
    else:
        raise ValueError(f"unrecognized local office in: {title!r}")
    return "precinct", {
        "office": office,
        "district": locality.title(),
        "precinct": locality.title(),
        "term": term,
    }


def term_suffix(office: str, term, multi_term_types) -> str:
    if term is not None and office in multi_term_types:
        return f"{office} ({term} Year)"
    return office


def main(argv):
    if len(argv) not in (3, 4):
        sys.exit(__doc__)
    input_path, precinct_path = argv[1], argv[2]
    extras_path = argv[3] if len(argv) == 4 else None

    with open(input_path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    contests = parse_contests(lines)

    bad = check_breakdowns(contests)
    for b in bad:
        print(f"WARN source breakdown mismatch: {b}", file=sys.stderr)

    # Which offices appear with more than one term length?  Those get
    # an explicit "(N Year)" suffix on every row of that office type.
    terms_by_office = {}
    for c in contests:
        info = classify(c)[1]
        term = TERM_RE.search(c["title"])
        if info["office"] in ("Borough Council", "Borough Auditor",
                              "Township Supervisor", "Township Auditor",
                              "School Director") and term:
            terms_by_office.setdefault(info["office"], set()).add(term.group(1))
    multi_term_types = {o for o, ts in terms_by_office.items() if len(ts) > 1}

    precinct_rows, county_rows = [], []
    for c in contests:
        where, info = classify(c)
        term = TERM_RE.search(c["title"])
        office = term_suffix(info["office"], term.group(1) if term else None,
                             multi_term_types)
        for name, total, ed, abs_, p, mi in c["rows"]:
            if name in ("Undervote", "Overvote"):
                continue
            cand, party = split_candidate(name)
            if name.upper() in ("YES", "NO"):
                cand = name.title()
                party = ""
            row = {
                "county": COUNTY,
                "office": office,
                "district": info["district"],
                "party": party,
                "candidate": cand,
                "votes": total,
                "election_day": ed,
                "mail": abs_ + mi,  # ABS + M-I merged into mail
                "provisional": p,
            }
            if where == "precinct":
                row["precinct"] = info["precinct"]
                precinct_rows.append(row)
            else:
                county_rows.append(row)

    # Metadata from the page header totals.
    rv = bc = None
    for line in lines:
        m = re.search(r"Registered Voters ([\d,]+) - Total Ballots ([\d,]+)", line)
        if m:
            rv, bc = to_int(m.group(1)), to_int(m.group(2))
            break
    if rv is None:
        raise ValueError("could not find Registered Voters / Total Ballots header")

    with open(precinct_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRECINCT_FIELDS)
        w.writeheader()
        w.writerows(precinct_rows)

    if extras_path:
        with open(extras_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COUNTY_FIELDS)
            w.writeheader()
            w.writerow({"county": COUNTY, "office": "Registered Voters",
                        "candidate": "", "votes": rv})
            w.writerow({"county": COUNTY, "office": "Ballots Cast",
                        "candidate": "", "votes": bc})
            w.writerows(county_rows)

    print(f"parsed {len(contests)} contests: {len(precinct_rows)} precinct rows, "
          f"{len(county_rows)} county-level rows "
          f"(metadata: Registered Voters {rv}, Ballots Cast {bc})")
    if bad:
        print(f"{len(bad)} source rows failed the ED+ABS+P+M-I==Total check", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))