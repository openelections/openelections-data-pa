#!/usr/bin/env python3
"""
Parse Perry County PA 2024 Primary Election precinct results.

Source: "Perry PA 2024 Primary Official Precinct Results.pdf" (Dominion
"Statement of Votes Cast by Geography - Official" report, PA_Perry_2024P,
Apr 23, 2024; same SOVC-by-Geography family as the county's 2022-primary
parser). 31 precincts in "Precinct <NAME>" blocks; 17 contests per precinct
(7 statewide x DEM/REP, U.S. House CD-13 x DEM/REP, State House 86th
x DEM/REP, 3 national-convention delegate contests, and one per-precinct
Republican Committee Persons contest). Differences vs the 2022 report:

  - contest headers carry the ballot-party suffix once or twice:
        PRESIDENT OF THE UNITED STATES (DEM) (DEM) (Vote for 1)
        DELEGATE to the DEMOCRATIC NATIONAL CONVENTION (DEM) (Vote for 3)
  - candidate data rows include a Vote % column:
        NAME  VOTES  PCT  ED  MI  PR
  - each contest block has per-precinct "N ballots (...)" metadata and
    trailing "Overvotes"/"Undervotes" lines.
  - multi-slot contests print one "Write-in" row per slot; all slots are
    merged into a single "Write Ins" row per contest.

Districts are printed in the header only for U.S. House (13TH DISTRICT) and
State House (86TH / 86th); they are extracted from the title. National
convention delegate contests are not districted in the source.

Usage:
    uv run python parsers/pa_perry_primary_2024_results_parser.py \
        "<input.pdf>" "<output.csv>"

Validation: each contest block's printed "Total" row is compared against the
sum of that block's candidate rows; mismatches are reported on stderr and
abort the run. The county-summary SOVC PDF ("All Precincts" geography) parses
with the same code and is used as the cross-check anchor (see
work2024p/perry/crosscheck.py).
"""

import csv
import re
import subprocess
import sys
from pathlib import Path

COUNTY = "Perry"

PRECRE = re.compile(r"^Precinct\s+(.+)$")
# Contest header: office title, one or two (DEM)/(REP) tokens, "(Vote for N)".
CONTEST_RE = re.compile(
    r"^(.+?)\s*((?:\((?:DEM|REP)\)\s*)+)\(Vote for (\d+)\)\s*$", re.IGNORECASE
)
PARTY_TOKEN_RE = re.compile(r"\((DEM|REP)\)", re.IGNORECASE)
# Candidate data row: "NAME  VOTES  PCT%  ED  MI  PR".
CAND_RE = re.compile(
    r"^(.+?)\s+(\d[\d,]*)\s+(\d[\d,.]*%)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)

SKIP_PREFIXES = (
    "Statement of Votes Cast by Geography",
    "Perry County, PA, PA_Perry_",
    "All Precincts, All Districts",
    "Total Ballots Cast:",
    "Choice",
)
SKIP_RES = (
    re.compile(r"^Page: \d+ of \d+"),
    re.compile(r"^\d{4}-\d{2}-\d{2}|^\d{2}:\d{2}:\d{2}"),
    re.compile(r"^\d+ ballots \("),
    re.compile(r"^\d+ precincts reported out of"),
    re.compile(r"^\d[\d,]*\s*$"),  # stray page-break number
)
# Trailing zero-count rows after a page break ("Overvotes", "Undervotes").
OVERTYPE_RE = re.compile(r"^(Overvotes|Undervotes)\s+\d[\d,]*\s*$")


def normalize_office(raw):
    """Return (office, district) from a contest title with party suffix removed."""
    name = re.sub(r"\s+", " ", raw).strip()
    up = name.upper()
    m = re.match(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)", up)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)", up)
    if m:
        return "State House", m.group(1)
    if up.startswith("PRESIDENT OF THE UNITED STATES"):
        return "President", ""
    if up.startswith("UNITED STATES SENATOR"):
        return "U.S. Senate", ""
    if up.startswith("ATTORNEY GENERAL"):
        return "Attorney General", ""
    if up.startswith("AUDITOR GENERAL"):
        return "Auditor General", ""
    if up.startswith("STATE TREASURER"):
        return "State Treasurer", ""
    if up.startswith("ALTERNATE DELEGATE to the".upper()):
        return "Alternate Delegate to the Republican National Convention", ""
    if up.startswith("DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION"):
        return "Delegate to the Democratic National Convention", ""
    if up.startswith("DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION"):
        return "Delegate to the Republican National Convention", ""
    # Local offices (committee persons etc.) as printed, title-cased.
    return title_cand(name), ""


def title_cand(c):
    if c.lower() == "write-in":
        return "Write Ins"
    words = []
    for w in c.split():
        low = w.lower()
        if low in ("&", "/"):
            words.append(w)
        elif w.isdigit():
            words.append(w)
        elif len(w) <= 2 and w.isupper() and low not in ("mc",):
            # JR, II, 1ST-style short tokens keep letters capitalized-down
            if low in ("jr", "sr", "ii", "iii", "iv"):
                words.append(w.capitalize())
            elif re.match(r"^\d+(st|nd|rd|th)$", low):
                words.append(w[:2] + low[2:])
            else:
                words.append(w.capitalize() + ".")
        elif len(w) >= 3 and w[:2].lower() == "mc" and w.isupper():
            words.append("Mc" + w[2:].capitalize())
        elif w.isupper():
            words.append(w.capitalize())
        else:
            words.append(w)
    return " ".join(words)


def parse(text, county=COUNTY, include_countywide=False):
    """Parse SOVC text. Returns (rows, n_mismatches).

    The precinct report ends with a countywide "All Precincts" section
    duplicating every contest at county level; it is skipped by default (pass
    include_countywide=True to keep it, e.g. for parsing the summary-only
    SOVC PDF used as the cross-check anchor).
    """
    rows = []
    mismatches = []
    geo = None            # "Precinct X" or "All Precincts" (countywide)
    office = district = party = ""
    block = []            # candidate rows of the current contest block
    skip_section = False  # inside the countywide "All Precincts" section
    for raw_line in text.split("\n"):
        s = raw_line.strip()
        if not s:
            continue
        if any(s.startswith(sp) for sp in SKIP_PREFIXES):
            continue
        if any(r.match(s) for r in SKIP_RES):
            continue
        m = PRECRE.match(s)
        if m:
            geo = m.group(1).strip()
            office = district = party = ""
            skip_section = False
            continue
        if s == "All Precincts" or s.startswith("All Precincts "):
            geo = "All Precincts"
            office = district = party = ""
            skip_section = not include_countywide
            continue
        if skip_section:
            continue
        m = CONTEST_RE.match(s)
        if m:
            parties = {p.upper() for p in PARTY_TOKEN_RE.findall(m.group(2))}
            if len(parties) != 1:
                mismatches.append((geo, s[:60], "mixed party tokens", parties))
            party = parties.pop()
            office, district = normalize_office(m.group(1))
            block = []
            continue
        if OVERTYPE_RE.match(s):
            continue
        if geo and office:
            m = CAND_RE.match(s)
            if m:
                name = m.group(1).strip()
                votes = int(m.group(2).replace(",", ""))
                if name == "Total":
                    summed = sum(c["votes"] for c in block)
                    if summed != votes:
                        mismatches.append(
                            (geo, office, district, party, votes, summed)
                        )
                    # write-in slots (one printed row per slot) merged into one row
                    named = [c for c in block if not c["name"].lower() == "write-in"]
                    wi = [c for c in block if c["name"].lower() == "write-in"]
                    for c in named:
                        rows.append({
                            "county": county, "precinct": geo, "office": office,
                            "district": district, "party": party,
                            "candidate": title_cand(c["name"]),
                            "votes": c["votes"], "election_day": c["ed"],
                            "mail": c["mi"], "provisional": c["pr"],
                        })
                    if wi:
                        rows.append({
                            "county": county, "precinct": geo, "office": office,
                            "district": district, "party": party,
                            "candidate": "Write Ins",
                            "votes": sum(c["votes"] for c in wi),
                            "election_day": sum(c["ed"] for c in wi),
                            "mail": sum(c["mi"] for c in wi),
                            "provisional": sum(c["pr"] for c in wi),
                        })
                    block = []
                    office = district = party = ""
                    continue
                block.append({
                    "name": name, "votes": votes,
                    "ed": int(m.group(4).replace(",", "")),
                    "mi": int(m.group(5).replace(",", "")),
                    "pr": int(m.group(6).replace(",", "")),
                })
                continue
        print(f"WARN: unmatched line: {s[:80]}", file=sys.stderr)
    for mismatch in mismatches:
        print(f"  MISMATCH {mismatch}", file=sys.stderr)
    print(f"printed-total check: {len(mismatches)} mismatches", file=sys.stderr)
    return rows, len(mismatches)


FIELDS = ["county", "precinct", "office", "district", "party", "candidate",
          "votes", "election_day", "mail", "provisional"]


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    text = subprocess.run(
        ["pdftotext", "-layout", sys.argv[1], "-"], capture_output=True, text=True
    ).stdout
    rows, bad = parse(text)
    if bad:
        sys.exit(f"{bad} mismatches; inspect before writing output")
    with open(sys.argv[2], "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()
