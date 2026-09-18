#!/usr/bin/env python3
"""Union County PA 2023 general election — Official Precinct Summary parser.

Input: one or more pdftotext -layout text extracts of Union County
"Official Precinct Summary" PDFs (one PDF per precinct, 2 pages each, two
side-by-side contest columns per page).  Sources for 2023 live in
openelections-sources-pa/2023/general/ as
"Union County Official Precinct Summary P00xxx 2023 General.pdf".

Usage:
    python parsers/pa_union_general_2023_results_parser.py <input_path> <output_path>

<input_path> may be either:
  - a directory containing the extracted .txt files, or
  - a glob pattern (e.g. "work2023/txt/Union__Union_County_Official_Precinct_Summary_P*_2023_General.txt")

Layout notes:
  * Each page carries two contest columns; the right column starts at a fixed
    x-offset (verified column 67..70 across all 26 files; nothing but centered
    page headers ever crosses the boundary), so each -layout text line is split
    at column 67 into a left and a right piece.
  * Page headers ("Precinct Summary Report" ... "Official Count" plus the
    "Registered Voters X - Total Ballots Y : Z%" line) are skipped as a block;
    the precinct name is read from the line after "ElectionDay".
  * Only totals are reported (no election-day/mail/provisional breakdown), so
    the breakdown columns are left empty.
  * Contest titles may wrap across lines (e.g. "BUFFALO TOWNSHIP SUPERVISOR" /
    "(6 YEAR TERM)", "MILTON AREA SCHOOL DISTRICT REGION 3 SCHOOL" /
    "DIRECTOR"), candidate names may wrap their party suffix onto the next
    line (e.g. "TERA UNZICKER-FASSERO  62  7.23%" then "(REP)"), and
    retention contests have no "Vote For" line and list YES/NO rows.
  * "No Candidate Filed" lines carry no numbers and are skipped; the
    accompanying Write-in row holds the votes.
  * Source typo "ELECTION - PAENLLA" (one precinct) is normalized to
    "Panella".
"""
from __future__ import annotations

import csv
import glob
import os
import re
import sys

COUNTY = "Union"
SPLIT_COL = 67  # verified: right column never starts before col 69, left content never reaches col 64

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

CAND_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>[\d,]+)\s+(?P<pct>[\d.]+%)$")
VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.I)
TOTAL_RE = re.compile(r"^Total Votes\s+([\d,]+)$", re.I)
RV_RE = re.compile(r"^Registered Voters\s+([\d,]+)\s+-\s+Total Ballots\s+([\d,]+)")
PARTY_CONT_RE = re.compile(r"^\(([A-Z]{3})\)$")
PARTY_SUFFIX_RE = re.compile(r"\s*\(([A-Z]{3})\)$")
TERM_RE = re.compile(r"\s*\(?(?P<n>\d+)\s+YEAR(?P<u>\s+UNEXPIRED)?\s+TERM\)?\s*$", re.I)


def title_case(text: str) -> str:
    """ALL CAPS -> Title Case, handling initials, hyphens, apostrophes, Mc/O'."""
    def cap_word(w: str) -> str:
        lw = w.lower()
        if lw.startswith("mc") and len(lw) > 2 and lw[2].isalpha():
            return "Mc" + lw[2].upper() + lw[3:]
        if lw.startswith("o'") and len(lw) > 2:
            return "O'" + lw[2].upper() + lw[3:]
        out = []
        cap = True
        for ch in w:
            if ch.isalpha():
                out.append(ch.upper() if cap else ch.lower())
                cap = False
            else:
                out.append(ch.lower())
                if ch in ".-'":
                    cap = True
        return "".join(out)
    return " ".join(cap_word(w) for w in text.split())


def is_header_piece(s: str) -> bool:
    if not s:
        return True
    if s in ("Precinct Summary Report", "UNION COUNTY, PA", "MUNICIPAL ELECTION",
             "NOVEMBER 7, 2023", "ElectionDay", "Official Count"):
        return True
    if s.startswith("Page ") or "Date:" in s or "Time:" in s:
        return True
    return False


class Contest:
    def __init__(self):
        self.title_lines: list[str] = []
        self.vote_for = None
        self.total = None
        self.candidates: list[dict] = []  # {name, party, votes}

    @property
    def done(self) -> bool:
        """Contest data is complete once Total Votes has been seen."""
        return self.total is not None


class ColumnParser:
    """State machine for one side-by-side column of a Precinct Summary page."""

    def __init__(self):
        self.cur: Contest | None = None
        self.contests: list[Contest] = []

    def feed(self, s: str) -> None:
        if not s:
            return
        m = VOTEFOR_RE.match(s)
        if m:
            if self.cur is None:
                self.cur = Contest()
            self.cur.vote_for = int(m.group(1))
            return
        m = TOTAL_RE.match(s)
        if m:
            if self.cur is None:
                self.cur = Contest()
            elif self.cur.done:  # new contest without a title seen yet
                self.contests.append(self.cur)
                self.cur = Contest()
            self.cur.total = int(m.group(1).replace(",", ""))
            return
        m = CAND_RE.match(s)
        if m:
            if self.cur is None:
                raise ValueError(f"candidate row without contest: {s!r}")
            self.cur.candidates.append(
                {"name": m.group("name").strip(), "party": "", "votes": int(m.group("votes").replace(",", ""))})
            return
        m = PARTY_CONT_RE.match(s)
        if m and self.cur and self.cur.candidates:  # wrapped party suffix
            self.cur.candidates[-1]["name"] += f" ({m.group(1)})"
            return
        if s == "No Candidate Filed":
            return
        # otherwise: a contest-title line
        if self.cur is not None and self.cur.done:
            self.contests.append(self.cur)
            self.cur = Contest()
        if self.cur is None:
            self.cur = Contest()
        self.cur.title_lines.append(s)

    def finish(self) -> list[Contest]:
        if self.cur is not None:
            self.contests.append(self.cur)
            self.cur = None
        return self.contests


def map_title(title_lines: list[str]) -> tuple[str, str]:
    """Joined contest title -> (office, district)."""
    title = " ".join(title_lines)
    title = re.sub(r"\s+", " ", title).strip()
    title = title.replace("PAENLLA", "PANELLA")  # source typo, P00010 only
    # P00130 text layer truncates the final R of "SUPERVISOR" (verified in the
    # PDF's embedded text, not a pdftotext artifact)
    title = re.sub(r"(\w+) SUPERVISO$", r"\1 SUPERVISOR", title)

    term = ""
    m = TERM_RE.search(title)
    if m:
        term = f"({m.group('n')} Year)"
        title = title[: m.start()].strip()

    # retention
    m = re.match(r"JUDGE OF THE SUPERIOR COURT RETENTION ELECTION - (.+)$", title, re.I)
    if m:
        return f"Judge of the Superior Court Retention - {title_case(m.group(1))}", ""
    # magisterial district judge
    m = re.match(r"MAGISTERIAL DISTRICT JUDGE ([\d-]+[A-Z]?)$", title, re.I)
    if m:
        return "Magisterial District Judge", m.group(1)
    # school director: "<DISTRICT> ... SCHOOL DIRECTOR" or "<DISTRICT> DIRECTOR"
    m = re.match(r"(.+?)\s+SCHOOL DIRECTOR$", title, re.I)
    if m:
        return "School Director", title_case(m.group(1))
    m = re.match(r"(.+? SCHOOL DISTRICT)\s+DIRECTOR$", title, re.I)
    if m:
        return "School Director", title_case(m.group(1))
    # township offices
    m = re.match(r"(.+?)\s+TOWNSHIP (SUPERVISOR|AUDITOR|TAX COLLECTOR)$", title, re.I)
    if m:
        office = {"SUPERVISOR": "Township Supervisor", "AUDITOR": "Township Auditor",
                  "TAX COLLECTOR": "Tax Collector"}[m.group(2).upper()]
        return office, f"{title_case(m.group(1))} Township"
    # borough offices
    m = re.match(r"(.+?)\s+BOROUGH (COUNCIL)(.*)$", title, re.I)
    if m:
        suffix = m.group(3).strip()
        district = title_case(m.group(1) + (" " + suffix if suffix else ""))
        return "Borough Council", district
    m = re.match(r"(.+?)\s+BOROUGH (AUDITOR|MAYOR|TAX COLLECTOR)$", title, re.I)
    if m:
        office = {"AUDITOR": "Borough Auditor", "MAYOR": "Mayor",
                  "TAX COLLECTOR": "Tax Collector"}[m.group(2).upper()]
        return office, title_case(m.group(1))
    # countywide
    county_offices = {
        "COUNTY COMMISSIONER": "County Commissioner",
        "REGISTER AND RECORDER": "Register and Recorder",
        "DISTRICT ATTORNEY": "District Attorney",
        "COUNTY TREASURER": "County Treasurer",
        "COUNTY AUDITOR": "County Auditor",
        "COUNTY CORONER": "County Coroner",
        "COUNTY SHERIFF": "County Sheriff",
        "COUNTY CONTROLLER": "County Controller",
        "COUNTY PROTHONOTARY": "Prothonotary",
    }
    if title.upper() in county_offices:
        return county_offices[title.upper()], ""
    # statewide / courts
    statewide = {
        "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
        "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
        "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
        "JUDGE OF THE COURT OF COMMON PLEAS": "Judge of the Court of Common Pleas",
    }
    if title.upper() in statewide:
        return statewide[title.upper()], ""
    raise ValueError(f"unmapped contest title: {title!r}")


def parse_file(path: str) -> dict:
    """Parse one precinct text file -> {precinct, rows, warnings}."""
    raw_lines = open(path, encoding="utf-8").read().replace("\x0c", "").splitlines()

    # precinct name: first non-empty line after "ElectionDay"
    precinct = None
    for i, line in enumerate(raw_lines):
        if line.strip() == "ElectionDay":
            for later in raw_lines[i + 1:]:
                if later.strip():
                    precinct = title_case(later.strip())
                    break
            break
    if precinct is None:
        raise ValueError(f"no precinct name found in {path}")

    left = ColumnParser()
    right = ColumnParser()
    rv = bc = None

    i = 0
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i]
        s = line.strip()
        if s == "Precinct Summary Report":
            # skip page header through the metadata line
            i += 1
            while i < n and not RV_RE.match(raw_lines[i].strip()):
                i += 1
            if i < n:
                m = RV_RE.match(raw_lines[i].strip())
                rv, bc = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
            i += 1
            continue
        left.feed(line[:SPLIT_COL].strip())
        right.feed(line[SPLIT_COL:].strip())
        i += 1

    rows = []
    if rv is not None:
        rows.append(["Registered Voters", "", "", "", rv])
        rows.append(["Ballots Cast", "", "", "", bc])

    # group contests by (office, district) to decide term suffixes
    parsed = []  # (office, district, candidates)
    for col in (left, right):
        for c in col.finish():
            if c.total is None and not c.candidates and not c.title_lines:
                continue
            if c.total is None:
                raise ValueError(f"{path}: contest {c.title_lines} has no Total Votes")
            office, district = map_title(c.title_lines)
            parsed.append((office, district, c))

    # term suffix when the same office+district has 2+ distinct term lengths,
    # or whenever a contest is explicitly an UNEXPIRED (partial) term — e.g.
    # Lewisburg Borough Council Ward 4 appears both as a plain 4-year contest
    # and a "2 YEAR UNEXPIRED TERM" contest
    from collections import defaultdict
    terms = defaultdict(set)
    for office, district, c in parsed:
        m = TERM_RE.search(" ".join(c.title_lines))
        if m:
            terms[(office, district)].add((int(m.group("n")),
                                           bool(m.group("u"))))
    for office, district, c in parsed:
        m = TERM_RE.search(" ".join(c.title_lines))
        if m and (len(terms[(office, district)]) > 1 or m.group("u")):
            suffix = f"({m.group('n')} Year"
            if m.group("u"):
                suffix += " Unexpired"
            office = f"{office} {suffix})"
        for cand in c.candidates:
            name = cand["name"]
            pm = PARTY_SUFFIX_RE.search(name)
            party = ""
            if pm and name != "Write-in":
                party = pm.group(1)
                name = name[: pm.start()].strip()
            name = title_case(name) if name != "Write-in" else "Write-ins"
            rows.append([office, district, party, name, cand["votes"]])

    return {"precinct": precinct, "rows": rows}


def collect_inputs(input_path: str) -> list[str]:
    if os.path.isdir(input_path):
        files = sorted(glob.glob(os.path.join(
            input_path, "Union__Union_County_Official_Precinct_Summary_P*_2023_General.txt")))
    else:
        files = sorted(glob.glob(input_path))
    if not files:
        raise SystemExit(f"no input files found for {input_path}")
    return files


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    input_path, output_path = sys.argv[1], sys.argv[2]
    files = collect_inputs(input_path)
    out_rows = []
    for f in files:
        res = parse_file(f)
        for office, district, party, candidate, votes in res["rows"]:
            out_rows.append([COUNTY, res["precinct"], office, district, party,
                             candidate, votes, "", "", ""])
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(out_rows)
    print(f"wrote {len(out_rows)} rows for {len(files)} precincts to {output_path}")


if __name__ == "__main__":
    main()