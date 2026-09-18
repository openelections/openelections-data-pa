#!/usr/bin/env python3
"""Parse the Schuylkill County 2023 General Election "Municipal Report"
(Electionware Summary Results Report) into the OpenElections precinct CSV.

The source is a municipality-level report: each contest section is one
municipality's race (no individual precincts), so the municipality name is
used as both the `precinct` and `district` value.

Usage:
    python3 parsers/pa_schuylkill_general_2023_results_parser.py \
        <input_txt> <output_csv>

The source contains ONLY municipal/local contests (no statewide or county
row office races) and no Registered Voters / Ballots Cast metadata rows.
"""

import csv
import re
import sys

COLUMNS = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "mail", "provisional",
]

COUNTY = "Schuylkill"

PARTY_TOKENS = {"DEM", "REP", "DEM/REP", "IND", "LIB", "GRN", "CON", "NF"}

# Municipalities holding "Council" contests that are cities (all other
# Council municipalities in Schuylkill County are boroughs).
CITIES = {"Pottsville"}

HEADER_SKIP_PAT = re.compile(
    r"^(Summary Results Report|7 November 2023|November \d+, \d{4}|\s*$)"
)


def clean_municipality(name):
    """Normalize a municipality name: 'Twp.' -> 'Twp', strip trailing period."""
    name = name.strip()
    name = re.sub(r"\s+Twp\.\s*$", " Twp", name)
    return name.rstrip(".").strip()


KINDS = ("City Controller", "City Treasurer", "City Council",
         "Tax Collector", "Supervisor", "Council", "Mayor", "Auditor")


def parse_title(title):
    """Split an Electionware contest title into (kind, term, place).

    Examples:
      'Supervisor V1 6yr Blythe Twp.'   -> ('Supervisor', '6yr', 'Blythe Twp.')
      'Council V4 4yr St. Clair'        -> ('Council', '4yr', 'St. Clair')
      'Tax Collector Delano Twp.'       -> ('Tax Collector', None, 'Delano Twp.')
      'City Controller Pottsville'      -> ('City Controller', None, 'Pottsville')
      'Mayor V1 2yr Coaldale'           -> ('Mayor', '2yr', 'Coaldale')
      'Council Palo Alto Ward 1 Palo Alto 1st Ward'
                                        -> ('Council', None, 'Palo Alto Ward 1 Palo Alto 1st Ward')
    """
    title = title.strip()
    kind = None
    for k in KINDS:
        if title == k or title.startswith(k + " "):
            kind = k
            break
    if kind is None:
        return title, None, ""
    rest = title[len(kind):].strip()
    term = None
    m = re.match(r"^(?:V\d+\s+)?(?:(\d+yr)\s+)?(.+)$", rest)
    if m:
        term = m.group(1)
        place = (m.group(2) or "").strip()
    else:
        place = ""
    return kind, term, place


def office_and_district(title, multi_term):
    """Map a contest title to (office, district).

    `multi_term` maps (kind, municipality) -> True when the same municipality
    has more than one term variant of the same office, in which case a
    "(N Year)" suffix is appended (matching the 2025 Berks style).
    """
    kind, term, place = parse_title(title)
    # Palo Alto ward contests: 'Council Palo Alto Ward 1 Palo Alto 1st Ward'
    ward_m = re.match(r"^(.*?)\s+(Palo Alto \d)(?:st|nd|rd|th) Ward$", place)
    if kind == "Council" and ward_m:
        mun = clean_municipality(ward_m.group(1))  # e.g. 'Palo Alto Ward 1'
        return ("Borough Council", mun)
    place_clean = clean_municipality(place)
    district = place_clean

    if kind == "City Controller":
        office = "City Controller"
    elif kind == "City Treasurer":
        office = "City Treasurer"
    elif kind == "City Council":
        office = "City Council"
    elif kind == "Council":
        office = "Borough Council"
    elif kind == "Mayor":
        office = "Mayor"
    elif kind == "Tax Collector":
        office = "Tax Collector"
    elif kind == "Supervisor":
        office = "Township Supervisor"
    elif kind == "Auditor":
        office = "Township Auditor"
    else:
        office = kind

    if term and multi_term.get((kind, place_clean)):
        years = int(term.replace("yr", ""))
        office = "%s (%d Year)" % (office, years)
    return office, district


NUM_ROW = re.compile(r"^(?P<label>.*?)\s{2,}(?P<n1>[\d,]+)\s+(?P<n2>[\d,]+)\s+"
                     r"(?P<n3>[\d,]+)\s+(?P<n4>[\d,]+)\s*$")


def parse_contest_rows(lines, start, end):
    """Extract (candidates, writeins) rows for a contest section.

    candidates: list of (party, name, (total, day, mail, prov))
    writeins:   (total, day, mail, prov) or None
    """
    candidates = []
    writeins = None
    for line in lines[start:end]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Write-In:"):
            continue  # individual write-in sub-rows are skipped
        if stripped == "Not Assigned":
            continue
        m = NUM_ROW.match(stripped)
        if not m:
            continue
        label = m.group("label").strip()
        nums = (int(m.group("n1").replace(",", "")),
                int(m.group("n2").replace(",", "")),
                int(m.group("n3").replace(",", "")),
                int(m.group("n4").replace(",", "")))
        if label == "Not Assigned":
            continue
        if label == "Write-In Totals":
            writeins = nums
            continue
        if label.startswith("Total Votes") or label.startswith("Contest Totals") \
                or label.startswith("Overvotes") or label.startswith("Undervotes") \
                or label.startswith("Precincts Reporting"):
            continue
        party = ""
        name = label
        first = name.split(" ", 1)
        if len(first) == 2 and first[0] in PARTY_TOKENS:
            party = first[0]
            name = first[1].strip()
        candidates.append((party, name, nums))
    return candidates, writeins


def find_headers(lines):
    """Locate contest headers: (title_line_index, title)."""
    headers = []
    for i, line in enumerate(lines):
        if line.strip().startswith("Vote For"):
            title = None
            for j in range(i - 1, max(0, i - 6), -1):
                s = lines[j].strip()
                if not s:
                    continue
                if HEADER_SKIP_PAT.match(s) or s in ("OFFICIAL RESULTS",
                                                     "UNOFFICIAL RESULTS",
                                                     "Schuylkill County"):
                    continue
                if s.startswith("Vote For"):
                    continue
                title = s
                break
            if title is None:
                raise ValueError("No title found for 'Vote For' at line %d" % (i + 1))
            headers.append((i, title))
    return headers


CLOSE_MARKERS = ("Not Assigned", "Total Votes Cast")


def group_contests(lines, headers):
    """Group header events into contests, merging page-break continuations.

    A header starts a new contest unless the text since the previous header
    contains a section-closing marker (continuation pages repeat the contest
    title and 'Vote For' line but carry only additional rows).
    """
    contests = []
    prev = None
    for idx, title in headers:
        if prev is None:
            contests.append({"title": title, "start": idx})
        else:
            segment = "\n".join(lines[prev:idx])
            if any(marker in segment for marker in CLOSE_MARKERS):
                contests.append({"title": title, "start": idx})
            # else: continuation of the previous contest
        prev = idx
    ends = [c["start"] for c in contests[1:]] + [len(lines)]
    for c, end in zip(contests, ends):
        c["rows"], c["writeins"] = parse_contest_rows(lines, c["start"], end)
    return contests


def compute_multi_term(contests):
    """Find (kind, municipality) pairs with more than one term variant."""
    seen = {}
    multi_term = {}
    for c in contests:
        kind, term, place = parse_title(c["title"])
        if not term or not place:
            continue
        key = (kind, clean_municipality(place))
        seen.setdefault(key, set()).add(term)
    for key, terms in seen.items():
        if len(terms) > 1:
            multi_term[key] = True
    return multi_term


def parse(input_path, output_path):
    with open(input_path, encoding="utf-8") as f:
        lines = f.read().replace("\x0c", "\n").split("\n")
    headers = find_headers(lines)
    contests = group_contests(lines, headers)
    multi_term = compute_multi_term(contests)

    rows = []
    for c in contests:
        title = c["title"]
        office, district = office_and_district(title, multi_term)
        precinct = district
        for party, name, nums in c["rows"]:
            rows.append({
                "county": COUNTY,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": party,
                "candidate": name,
                "votes": nums[0],
                "election_day": nums[1],
                "mail": nums[2],
                "provisional": nums[3],
            })
        if c["writeins"] is not None:
            w = c["writeins"]
            rows.append({
                "county": COUNTY,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": "",
                "candidate": "Write-ins",
                "votes": w[0],
                "election_day": w[1],
                "mail": w[2],
                "provisional": w[3],
            })

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows, contests


def main():
    if len(sys.argv) < 3:
        print(__doc__.strip())
        sys.exit(1)
    input_path, output_path = sys.argv[1], sys.argv[2]
    rows, contests = parse(input_path, output_path)
    print("Parsed %d contests, %d result rows -> %s"
          % (len(contests), len(rows), output_path))


if __name__ == "__main__":
    main()