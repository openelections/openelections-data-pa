#!/usr/bin/env python3
"""Union County PA 2022 general election — Official Precinct Summary parser.

Input: "Union PA general results-2.pdf" from openelections-sources-pa/2022/general/
(one PDF, 26 single-column pages, one precinct per page).  Same vendor/layout as
Union's 2023 "Official Precinct Summary" reports, but single contest column
(every page is "Page 1/ 1") and contest titles are district-style:
"US SENATOR", "GOVERNOR", "REP IN CONGRESS 15TH", "GENERAL ASSEMBLY 76/83/85".

Usage:
    python parsers/pa_union_general_2022_results_parser.py <input.pdf-or-txt> <output.csv>

Only totals are reported (no election-day/mail/provisional breakdown), so the
7-column format is used.  Registered Voters / Ballots Cast rows are emitted.
"""
from __future__ import annotations

import re
import subprocess
import sys
import csv

COUNTY = "Union"

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

CAND_RE = re.compile(r"^(?P<name>.+?)\s+(?P<votes>[\d,]+)\s+(?P<pct>[\d.]+%)$")
VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.I)
TOTAL_RE = re.compile(r"^Total Votes\s+([\d,]+)$", re.I)
RV_RE = re.compile(r"^Registered Voters\s+([\d,]+)\s+-\s+Total Ballots\s+([\d,]+)")
PARTY_CONT_RE = re.compile(r"^\(([A-Z]{3})\)$")
PARTY_SUFFIX_RE = re.compile(r"\s*\(([A-Z]{3})\)$")

SKIP_EXACT = {"UNION COUNTY, PA", "GENERAL ELECTION", "NOVEMBER 8, 2022",
              "RESULTS", "Official"}
SKIP_PREFIX = ("Precinct Summary Report", "Page ", "Date:", "Time:")


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


def normalize_name(name: str) -> str:
    name = title_case(name)
    if name == "Glenn Gt Thompson":  # source prints "GLENN GT THOMPSON" (nickname)
        name = "Glenn GT Thompson"
    return name


def map_title(title: str) -> tuple[str, str]:
    t = re.sub(r"\s+", " ", title.strip()).upper()
    if t == "US SENATOR":
        return "U.S. Senate", ""
    if t == "GOVERNOR":
        return "Governor", ""
    m = re.match(r"^REP IN CONGRESS (\d+)(?:ST|ND|RD|TH)?$", t)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^GENERAL ASSEMBLY (\d+)$", t)
    if m:
        return "State House", m.group(1)
    raise ValueError(f"unmapped contest title: {title!r}")


def extract_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        return subprocess.run(["pdftotext", "-layout", path, "-"],
                              check=True, capture_output=True, text=True).stdout
    return open(path, encoding="utf-8").read()


class Contest:
    def __init__(self):
        self.title_lines: list[str] = []
        self.total: int | None = None
        self.rows: list[tuple[str, str, int]] = []  # (candidate, party, votes)


def parse(lines: list[str]) -> list[list]:
    rows: list[list] = []
    precinct = None
    expect_precinct = False
    cur: Contest | None = None
    cur_office = cur_district = None
    contest_sum = 0
    contests_done = 0

    def close_contest():
        nonlocal cur, cur_office, cur_district, contest_sum, contests_done
        if cur is None:
            return
        if cur.total is None:
            if not cur.title_lines and not cur.rows:
                cur = None
                return
            raise ValueError(f"{precinct}: contest {cur.title_lines} has no Total Votes")
        office, district = map_title(" ".join(cur.title_lines))
        if contest_sum != cur.total:
            raise ValueError(
                f"{precinct} {office} {district}: candidate sum {contest_sum} != Total Votes {cur.total}")
        for name, party, votes in cur.rows:
            rows.append([COUNTY, precinct, office, district, party, name, votes])
        contests_done += 1
        cur = None
        contest_sum = 0

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if s == "RESULTS":
            expect_precinct = True
            # page boundary: close the previous page's last contest if complete
            if cur is not None and cur.total is not None:
                close_contest()
            else:
                cur = None
                contest_sum = 0
            continue
        if any(s.startswith(p) for p in SKIP_PREFIX) or s in SKIP_EXACT \
                or "Date:" in s or "Time:" in s:
            continue
        if expect_precinct:
            precinct = title_case(s)
            expect_precinct = False
            continue
        m = RV_RE.match(s)
        if m:
            rows.append([COUNTY, precinct, "Registered Voters", "", "", "",
                         int(m.group(1).replace(",", ""))])
            rows.append([COUNTY, precinct, "Ballots Cast", "", "", "",
                         int(m.group(2).replace(",", ""))])
            continue
        if VOTEFOR_RE.match(s):
            if cur is not None and cur.total is not None:
                close_contest()
            if cur is None:
                cur = Contest()
            continue
        m = TOTAL_RE.match(s)
        if m:
            if cur is None:
                raise ValueError(f"{precinct}: Total Votes without contest: {s!r}")
            cur.total = int(m.group(1).replace(",", ""))
            continue
        m = CAND_RE.match(s)
        if m:
            if cur is None:
                raise ValueError(f"{precinct}: candidate row without contest: {s!r}")
            name = m.group("name").strip()
            votes = int(m.group("votes").replace(",", ""))
            contest_sum += votes
            party = ""
            pm = PARTY_SUFFIX_RE.search(name)
            if pm:
                party = pm.group(1)
                name = name[: pm.start()].strip()
            if name == "Write-in":
                name, party = "Write Ins", ""
            else:
                name = normalize_name(name)
            cur.rows.append((name, party, votes))
            continue
        if PARTY_CONT_RE.match(s):
            # wrapped party suffix from the previous candidate row
            if cur is not None and cur.rows:
                last = cur.rows[-1]
                if last[1] == "" and last[0] != "Write Ins":
                    cur.rows[-1] = (last[0], PARTY_CONT_RE.match(s).group(1), last[2])
            continue
        # contest title line
        if cur is not None and cur.total is not None:
            close_contest()  # previous contest ended (defensive)
        if cur is None:
            cur = Contest()
        cur.title_lines.append(s)

    close_contest()
    if precinct is None:
        raise ValueError("no precinct name found")
    if contests_done == 0:
        raise ValueError(f"{precinct}: no contests parsed")

    return rows


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    input_path, output_path = sys.argv[1], sys.argv[2]
    lines = extract_text(input_path).splitlines()
    out_rows = parse(lines)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(out_rows)
    n_precincts = len({r[1] for r in out_rows if r[2] == "Registered Voters"})
    print(f"wrote {len(out_rows)} rows for {n_precincts} precincts to {output_path}")


if __name__ == "__main__":
    main()