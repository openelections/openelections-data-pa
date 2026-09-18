#!/usr/bin/env python3
"""Union County PA 2023 municipal primary — Official Summary Results parser.

Input: pdftotext -layout text extract of either Union County 2023 primary
report:

  * "Union County Summary Results 2023 Primary" (county-level "Election
    Summary Report", 16 pages, two side-by-side contest columns per page) or
  * "Union County Precinct Results 2023 Primary" (per-precinct "Precinct
    Summary Report", 79 pages / 26 precincts, same two-column layout).

The mode is auto-detected from the page header ("Election Summary Report"
vs "Precinct Summary Report"); the county summary emits the 9-column
county-level schema, the precinct report the 10-column precinct schema.

Usage:
    python parsers/pa_union_primary_2023_results_parser.py <input_path> <output_path>

Layout notes (same family as the 2023 general parser
pa_union_general_2023_results_parser.py):
  * Each page carries two contest columns; the right column starts at a
    fixed x-offset (col 68 in the county summary, col 70 in the precinct
    report — verified: no left-column token crosses the split in either
    file), so each -layout line is split into a left and a right piece.
  * Page headers ("Election Summary Report"/"Precinct Summary Report" ...
    "Official Count" plus the "Registered Voters X - Total Ballots Y"
    line) are skipped as a block; in precinct mode the precinct name is
    the line after "election day".
  * The page-1 "Party Distribution" block (Ballots 6,298; DEMOCRATIC
    2,148; REPUBLICAN 4,150) is skipped — per-party ballots-cast rows are
    not recorded (repo convention). The figures are used for the
    party-ballot arithmetic check instead.
  * "Number of Precincts" / "Precincts Reporting" lines are skipped;
    contest titles wrap across lines and carry the party as a separate
    "(DEMOCRATIC)"/"(REPUBLICAN)" line; term lines are "(N YEAR [UNEXPIRED]
    TERM)" or inline "N YEAR UNEXPIRED TERM" (Lewisburg Ward 4).
  * Source typo "LIMESTONE TOWNSHIP SUPERVISO" (missing final R, both
    parties) is normalized to "SUPERVISOR".
  * Write-in rows are aggregated to one "Write-ins" row per contest,
    carrying the contest's party (primary convention: DEM/REP sections of
    the same office must not collide as duplicates).
  * Only totals are reported (no election-day/mail/provisional
    breakdown), so the breakdown columns are left empty.
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Union"
SUMMARY_SPLIT = 68   # verified: right column starts exactly at col 68 in the county summary
PRECINCT_SPLIT = 70  # verified: right column starts exactly at col 70 in the precinct report

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

CAND_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>[\d,]+)\s+(?P<pct>[\d.]+%)$")
VOTEFOR_RE = re.compile(r"^VOTE FOR\s+(\d+)$", re.I)
TOTAL_RE = re.compile(r"^Total Votes\s+([\d,]+)$", re.I)
TOTAL_BARE_RE = re.compile(r"^Total Votes$", re.I)
BALLOTS_RE = re.compile(r"^Ballots\s+([\d,]+)$", re.I)
RV_RE = re.compile(r"^Registered Voters\s+([\d,]+)\s+-\s+Total Ballots\s+([\d,]+)")
NUMPRECINCTS_RE = re.compile(r"^Number of Precincts\s+[\d,]+$", re.I)
PRECREPORTING_RE = re.compile(r"^Precincts Reporting\s+[\d,]+\s+[\d.]+%$", re.I)
ALLREPORTING_RE = re.compile(r"^\d+ of \d+ Precincts Reporting", re.I)
PARTY_LINE_RE = re.compile(r"^\((DEMOCRATIC|REPUBLICAN)\)$", re.I)
PARTY_ROW_RE = re.compile(r"^(DEMOCRATIC|REPUBLICAN)\s+[\d,]+\s+[\d.]+%$", re.I)
WRITEIN_RE = re.compile(r"^write-?ins?$", re.I)
TERM_RE = re.compile(r"\s*\(?(?P<n>\d+)\s+YEAR(?P<u>\s+UNEXPIRED)?\s+TERM\)?\s*$", re.I)

COUNTY_OFFICES = {
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
STATEWIDE = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "JUDGE OF THE COURT OF COMMON PLEAS": "Judge of the Court of Common Pleas",
}


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


def normalize_candidate(name: str) -> str:
    if WRITEIN_RE.match(name.strip()):
        return "Write-ins"
    return title_case(name.strip())


class Contest:
    def __init__(self):
        self.title_lines: list[str] = []
        self.party = ""  # DEM / REP, from the contest's "(DEMOCTATIC)"/"(REPUBLICAN)" line
        self.vote_for = None
        self.total = None
        self.candidates: list[dict] = []  # {name, votes}

    @property
    def done(self) -> bool:
        return self.total is not None


class ColumnParser:
    """State machine for one side-by-side column of a summary page."""

    def __init__(self):
        self.cur: Contest | None = None
        self.contests: list[Contest] = []
        self.party_block = False  # inside the "Party Distribution" block

    def feed(self, s: str) -> None:
        if not s:
            return
        if NUMPRECINCTS_RE.match(s) or PRECREPORTING_RE.match(s) or ALLREPORTING_RE.match(s):
            return
        if s == "Party Distribution":
            self.party_block = True
            return
        if self.party_block:
            m = PARTY_ROW_RE.match(s)
            if m:
                if m.group(1).upper() == "REPUBLICAN":
                    self.party_block = False
                return
            if TOTAL_BARE_RE.match(s) or BALLOTS_RE.match(s):
                return
            self.party_block = False  # any other line ends the block
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
            elif self.cur.done:
                self.contests.append(self.cur)
                self.cur = Contest()
            self.cur.total = int(m.group(1).replace(",", ""))
            return
        m = CAND_RE.match(s)
        if m:
            if self.cur is None:
                raise ValueError(f"candidate row without contest: {s!r}")
            self.cur.candidates.append(
                {"name": m.group("name").strip(),
                 "votes": int(m.group("votes").replace(",", ""))})
            return
        m = PARTY_LINE_RE.match(s)
        if m:
            if self.cur is None:
                self.cur = Contest()
            self.cur.party = "DEM" if m.group(1).upper() == "DEMOCRATIC" else "REP"
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


def map_title(title_lines: list[str]) -> tuple[str, str, tuple[int, bool] | None]:
    """Joined contest title (party line removed) -> (office, district, term)."""
    title = " ".join(title_lines)
    title = re.sub(r"\s+", " ", title).strip()
    # source typo: text layer truncates the final R of "SUPERVISOR" (both
    # LIMESTONE TOWNSHIP SUPERVISOR contests, DEM and REP)
    title = re.sub(r"(\w+) SUPERVISO$", r"\1 SUPERVISOR", title)

    term = None
    m = TERM_RE.search(title)
    if m:
        term = (int(m.group("n")), bool(m.group("u")))
        title = title[: m.start()].strip()

    upper = title.upper()
    if upper in COUNTY_OFFICES:
        return COUNTY_OFFICES[upper], "", term
    if upper in STATEWIDE:
        return STATEWIDE[upper], "", term
    m = re.match(r"MAGISTERIAL DISTRICT JUDGE ([\d-]+[A-Z]?)$", title, re.I)
    if m:
        return "Magisterial District Judge", m.group(1), term
    # township offices
    m = re.match(r"(.+?)\s+TOWNSHIP (SUPERVISOR|AUDITOR|TAX COLLECTOR)$", title, re.I)
    if m:
        office = {"SUPERVISOR": "Township Supervisor", "AUDITOR": "Township Auditor",
                  "TAX COLLECTOR": "Tax Collector"}[m.group(2).upper()]
        return office, f"{title_case(m.group(1))} Township", term
    # borough offices
    m = re.match(r"(.+?)\s+BOROUGH (COUNCIL)(.*)$", title, re.I)
    if m:
        suffix = m.group(3).strip()
        district = title_case(m.group(1) + (" " + suffix if suffix else ""))
        return "Borough Council", district, term
    m = re.match(r"(.+?)\s+BOROUGH (AUDITOR|MAYOR|TAX COLLECTOR)$", title, re.I)
    if m:
        office = {"AUDITOR": "Borough Auditor", "MAYOR": "Mayor",
                  "TAX COLLECTOR": "Tax Collector"}[m.group(2).upper()]
        return office, title_case(m.group(1)), term
    # school director
    m = re.match(r"(.+?)\s+SCHOOL DIRECTOR$", title, re.I)
    if m:
        return "School Director", title_case(m.group(1)), term
    m = re.match(r"(.+? SCHOOL DISTRICT)\s+DIRECTOR$", title, re.I)
    if m:
        return "School Director", title_case(m.group(1)), term
    raise ValueError(f"unmapped contest title: {title!r}")


class PrecinctResult:
    def __init__(self, name: str | None):
        self.name = name
        self.rv = None
        self.bc = None
        self.contests: list[tuple[str, str, tuple[int, bool] | None, Contest]] = []
        # (office, district, term, Contest)


def parse_file(path: str) -> dict:
    """Parse one -layout text extract -> {mode, precincts: [PrecinctResult], warnings}."""
    raw = open(path, encoding="utf-8").read().replace("\x0c", "")
    lines = raw.splitlines()

    mode = "precinct"
    for ln in lines:
        s = ln.strip()
        if s == "Election Summary Report":
            mode = "summary"
            break
        if s == "Precinct Summary Report":
            break
    split = SUMMARY_SPLIT if mode == "summary" else PRECINCT_SPLIT

    warnings: list[str] = []
    precincts: list[PrecinctResult] = []
    cur = PrecinctResult(None)
    left = ColumnParser()
    right = ColumnParser()

    header = False
    awaiting = False

    def flush():
        nonlocal cur, left, right
        for col in (left, right):
            for c in col.finish():
                if c.total is None and not c.candidates and not c.title_lines:
                    continue
                if c.total is None:
                    raise ValueError(f"{path}: contest {c.title_lines} has no Total Votes")
                joined = " ".join(c.title_lines)
                if "Party Distribution" in joined:  # belt & braces: never emit the party block
                    continue
                if not c.party:
                    raise ValueError(
                        f"{path}: contest {joined!r} has no (DEMOCRATIC)/(REPUBLICAN) line")
                office, district, term = map_title(c.title_lines)
                cur.contests.append((office, district, term, c))
        left = ColumnParser()
        right = ColumnParser()

    for line in lines:
        lp = line[:split].strip()
        rp = line[split:].strip()
        if header:
            if RV_RE.match(lp):
                m = RV_RE.match(lp)
                cur.rv = int(m.group(1).replace(",", ""))
                cur.bc = int(m.group(2).replace(",", ""))
                header = False
                awaiting = False
            elif mode == "precinct" and lp == "election day":
                awaiting = True
            elif awaiting and lp and lp != "Official Count" and not lp.startswith("Page "):
                # first non-header line after "election day" is the precinct name
                awaiting = False
                lp = title_case(lp)
                if cur.name is None:
                    cur.name = lp
                elif cur.name != lp:
                    flush()
                    precincts.append(cur)
                    cur = PrecinctResult(lp)
            continue
        if lp in ("Election Summary Report", "Precinct Summary Report"):
            header = True
            continue
        left.feed(lp)
        right.feed(rp)

    # finalize
    flush()
    if mode == "precinct":
        precincts.append(cur)
    else:
        precincts = [cur]

    # sanity: every contest in every precinct got a name (precinct mode)
    if mode == "precinct":
        for p in precincts:
            if p.name is None:
                raise ValueError(f"{path}: precinct with no name")
    return {"mode": mode, "precincts": precincts, "warnings": warnings}


def term_suffix(office: str, term: tuple[int, bool], multi: bool) -> str:
    if term is None:
        return office
    n, unexpired = term
    if multi or unexpired:
        suffix = f"({n} Year"
        if unexpired:
            suffix += " Unexpired"
        return f"{office} {suffix})"
    return office


def emit(mode: str, precincts: list[PrecinctResult]) -> tuple[list[list], list[str]]:
    """Build output rows; applies the general parser's term-suffix convention
    (suffix when the same office+district has 2+ distinct term lengths, or
    whenever the term is UNEXPIRED)."""
    # global term inventory per (office, district)
    terms: dict[tuple[str, str], set] = {}
    parsed = []  # (precinct, office, district, term, contest)
    for p in precincts:
        for office, district, term, c in p.contests:
            parsed.append((p, office, district, term, c))
            if term is not None:
                terms.setdefault((office, district), set()).add(term)

    problems: list[str] = []
    rows: list[list] = []
    if mode == "summary":
        p = precincts[0]
        if p.rv is not None:
            rows.append([COUNTY, "", "Registered Voters", "", "", "", p.rv, "", "", ""])
            rows.append([COUNTY, "", "Ballots Cast", "", "", "", p.bc, "", "", ""])
    for p, office, district, term, c in parsed:
        multi = len(terms.get((office, district), set())) > 1
        office_full = term_suffix(office, term, multi)
        total = sum(cd["votes"] for cd in c.candidates)
        if total != c.total:
            problems.append(
                f"totals mismatch {office_full}|{district}|{c.party}: "
                f"candidates {total} != Total Votes {c.total}")
        for cd in c.candidates:
            rows.append([COUNTY, p.name or "", office_full, district, c.party,
                         normalize_candidate(cd["name"]), cd["votes"], "", "", ""])
    if mode == "precinct":
        for p in precincts:
            rows.append([COUNTY, p.name, "Registered Voters", "", "", "", p.rv, "", "", ""])
            rows.append([COUNTY, p.name, "Ballots Cast", "", "", "", p.bc, "", "", ""])
    return rows, problems


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    input_path, output_path = sys.argv[1], sys.argv[2]
    res = parse_file(input_path)
    mode = res["mode"]
    rows, problems = emit(mode, res["precincts"])
    if mode == "precinct":
        fieldnames = FIELDNAMES
    else:
        fieldnames = [f for f in FIELDNAMES if f != "precinct"]
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)
        for r in rows:
            if mode == "precinct":
                w.writerow(r)
            else:
                # rows carry an empty precinct slot in summary mode; drop it
                w.writerow([r[0]] + r[2:])
    n_contests = sum(len(p.contests) for p in res["precincts"])
    print(f"mode={mode} precincts={len(res['precincts'])} contests={n_contests} "
          f"rows={len(rows)} -> {output_path}")
    for prob in problems:
        print("PROBLEM:", prob)
    if problems:
        raise SystemExit(1)
    print("TOTALS CHECK: PASSED (all contests: candidate rows == Total Votes)")


if __name__ == "__main__":
    main()