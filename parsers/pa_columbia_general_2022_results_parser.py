#!/usr/bin/env python3
"""Parse Columbia County PA 2022 General precinct results.

Source: Columbia PA SOVC_Nov_8_2022_ElectionResults.pdf (crosstab format via
pdftotext -layout). Each contest occupies its own block of pages; every page
repeats the contest header (office + wrapped candidate columns), optionally a
Turnout block (only the U.S. Senate contest has one), then precinct blocks of
four vote-type rows each::

  BEAVER TWP
    Normal              609  404  66.34%   609  400   82  20.50% ...  1  0.25%
    Absentee            609   37   6.08%   609   36   25  69.44% ...   0     -
    Military/Overseas   609    -      -     609    0    0      -  ... 0     -
    Provisional         609    -      -     609    0    0      -  ... 0     -

Row layout: [turnout: reg ballots pct] (US Senate pages only), then
reg_voters, total_votes, then one (value, pct) pair per candidate column with
"Write-in" last. Each contest ends with a "Total" section whose final
"Total" row holds the county-wide totals (used for internal verification).

The source contains only four contests (U.S. Senate, Governor,
U.S. House 9th, State House 109th) -- no row offices or State Senate.
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "early_voting", "provisional",
]

# office_key, office, district, has_turnout, [(candidate, party), ...]
# has_turnout: a Turnout block (reg/ballots/pct) precedes the contest block on
# that contest's pages (only U.S. Senate in this file). The last candidate
# entry is always the Write-in column.
CONTESTS = [
    {
        "key": "UNITED STATES SENATE",
        "office": "U.S. Senate",
        "district": "",
        "has_turnout": True,
        "candidates": [
            ("John Fetterman", "DEM"),
            ("Mehmet Oz", "REP"),
            ("Erik Gerhardt", "LIB"),
            ("Richard L. Weiss", "GRN"),
            ("Daniel Wassmer", "KEY"),
        ],
    },
    {
        "key": "GOVERNOR/LIEUTENANT GOVERNOR",
        "office": "Governor",
        "district": "",
        "has_turnout": False,
        "candidates": [
            ("Josh Shapiro/Austin Davis", "DEM"),
            ("Douglas V. Mastriano/Carrie DelRosso", "REP"),
            ("Matt Hackenburg/Tim Dees", "LIB"),
            ("Christina DiGiulio/Michael Ole Shultz", "GRN"),
            ("Joe Soloski/Nicole Shultz", "KEY"),
        ],
    },
    {
        "key": "REPRESENTATIVE IN THE UNITED STATES CONGRESS",
        "office": "U.S. House",
        "district": "9",
        "has_turnout": False,
        "candidates": [
            ("Amanda R. Waldman", "DEM"),
            ("Dan Meuser", "REP"),
        ],
    },
    {
        "key": "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
        "office": "State House",
        "district": "109",
        "has_turnout": False,
        "candidates": [
            ("Ed Giannattasio", "DEM"),
            ("Robert Leadbeter", "REP"),
            ("Thomas Anderson", "LIB"),
        ],
    },
]

VOTE_TYPES = ("Normal", "Absentee", "Military/Overseas", "Provisional")
WRITE_IN = "Write Ins"

PRECINCT_RE = re.compile(r"^[A-Z][A-Z0-9 .\-/()']*$")
NUM_RE = re.compile(r"^[\d,]+$")


def extract_text(pdf_path: Path) -> str:
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), tmp_path],
            check=True, capture_output=True,
        )
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _val(tok: str):
    """Numeric token -> int; '-' -> 0; anything else -> None."""
    if tok == "-":
        return 0
    if NUM_RE.match(tok):
        return int(tok.replace(",", ""))
    return None


def _is_vote_type_row(stripped: str) -> bool:
    toks = stripped.split()
    return bool(toks) and toks[0] in VOTE_TYPES


class Contest:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.n_cols = len(cfg["candidates"]) + 1  # incl write-in column
        self.rows = {}      # precinct -> {votetype: [value per col]}
        self.totals = {}    # precinct -> {votetype: contest total}
        self.turnout = {}   # precinct -> {votetype: (reg, ballots)} (US Senate)
        self.rv = {}        # precinct -> registered voters (US Senate turnout)
        self.ballots = {}   # precinct -> {votetype: ballots cast}
        self.grand = None   # county grand-total row values per column
        self.problems = []


def _parse_vote_row(con: Contest, precinct: str, vt: str, nums: list[str], has_turnout: bool):
    if has_turnout:
        if len(nums) < 5:
            con.problems.append(f"short turnout row for {precinct}/{vt}: {nums}")
            return
        reg, ballots = _val(nums[0]), _val(nums[1])
        con.turnout.setdefault(precinct, {})[vt] = (reg, ballots)
        con.rv.setdefault(precinct, reg)
        nums = nums[3:]
    if len(nums) != 2 + 2 * con.n_cols:
        con.problems.append(
            f"unexpected token count for {precinct}/{vt}: got {len(nums)}, "
            f"want {2 + 2 * con.n_cols}"
        )
        return
    reg_v, total = _val(nums[0]), _val(nums[1])
    vals = nums[2:2 + 2 * con.n_cols:2]
    parsed = [_val(v) for v in vals]
    if any(v is None for v in parsed):
        con.problems.append(f"bad value tokens for {precinct}/{vt}: {vals}")
        return
    if sum(parsed) != total:
        con.problems.append(
            f"candidate sum {sum(parsed)} != total {total} for {precinct}/{vt}"
        )
    con.rows.setdefault(precinct, {})[vt] = parsed
    con.totals.setdefault(precinct, {})[vt] = total


def parse_section(lines: list[str], i: int, cfg: dict, problems: list[str]) -> tuple[Contest, int]:
    """Parse one contest section starting at its office-header line index i.
    Returns (contest, index just past the section)."""
    con = Contest(cfg)
    n = len(lines)
    has_turnout = cfg["has_turnout"]

    j = i + 1
    while j < n and lines[j].strip() != "Jurisdiction Wide":
        j += 1
    if j >= n:
        problems.append(f"no 'Jurisdiction Wide' found for {cfg['key']}")
        return con, n

    precinct = None
    j += 1
    while j < n:
        raw = lines[j]
        s = raw.strip()
        ind = _indent(raw)
        if not s:
            j += 1
            continue
        if _is_vote_type_row(s):
            if ind >= 8:
                j += 1  # centered page header containing a vote-type word
                continue
            toks = s.split()
            vt = toks[0]
            if precinct is None:
                con.problems.append(f"data row before precinct header: {s!r}")
            else:
                _parse_vote_row(con, precinct, vt, toks[1:], has_turnout)
            j += 1
            continue
        if ind >= 8:
            j += 1  # centered page junk (headers, candidate names, etc.)
            continue
        # small-indent, non-vote-type line
        if s == "Total":
            # totals section header: parse its rows, take grand-total row
            j = _parse_totals(lines, j + 1, con, has_turnout, problems)
            return con, j
        if PRECINCT_RE.match(s):
            precinct = s
            con.rows.setdefault(precinct, {})
            j += 1
            continue
        # unrecognized small-indent line: stop the section defensively
        problems.append(f"unrecognized line in {cfg['key']} section: {s!r}")
        return con, j
    return con, j


def _parse_totals(lines: list[str], j: int, con: Contest, has_turnout: bool,
                  problems: list[str]) -> int:
    """Read the contest's Total section; capture the grand-total row values."""
    n = len(lines)
    grand = None
    while j < n:
        raw = lines[j]
        s = raw.strip()
        if not s:
            j += 1
            continue
        toks = s.split()
        ind = _indent(raw)
        if ind < 8 and toks[0] == "Total" and len(toks) > 1:
            nums = toks[1:]
            if has_turnout:
                nums = nums[3:]
            if len(nums) == 2 + 2 * con.n_cols:
                con.grand = [_val(v) for v in nums[2:2 + 2 * con.n_cols:2]]
                return j + 1  # grand-total row is the last row of the section
            problems.append(f"{con.cfg['key']}: grand-total row mismatch: {s!r}")
            j += 1
            continue
        if ind < 8 and not (toks[0] in VOTE_TYPES):
            break  # next contest section
        j += 1
    con.grand = grand
    return j


def parse(text: str, problems: list[str]) -> list[Contest]:
    lines = text.split("\n")
    n = len(lines)
    contests = []
    i = 0
    while i < n:
        s = lines[i].strip()
        matched = None
        if _indent(lines[i]) >= 8:
            for cfg in CONTESTS:
                # US Senate header lines are prefixed by the "Turnout" label
                if cfg["key"] in s:
                    matched = cfg
                    break
        if matched:
            con, i = parse_section(lines, i, matched, problems)
            problems.extend(f"[{con.cfg['key']}] {p}" for p in con.problems)
            contests.append(con)
            continue
        i += 1
    return contests


def build_rows(contests: list[Contest]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    warns: list[str] = []

    # Registered Voters / Ballots Cast from the US Senate turnout block
    senate = next((c for c in contests if c.cfg["office"] == "U.S. Senate"), None)
    if senate:
        precincts = list(senate.rows.keys())
        for p in precincts:
            rv = senate.rv.get(p)
            if rv is None:
                warns.append(f"no turnout rows for {p}")
                continue
            rows.append({
                "county": "Columbia", "precinct": p, "office": "Registered Voters",
                "district": "", "party": "", "candidate": "", "votes": rv,
                "election_day": "", "early_voting": "", "provisional": "",
            })
            ballots = {vt: senate.turnout[p].get(vt, (0, 0))[1] for vt in VOTE_TYPES}
            bc = sum(ballots.values())
            rows.append({
                "county": "Columbia", "precinct": p, "office": "Ballots Cast",
                "district": "", "party": "", "candidate": "", "votes": bc,
                "election_day": ballots["Normal"],
                "early_voting": ballots["Absentee"] + ballots["Military/Overseas"],
                "provisional": ballots["Provisional"],
            })

    for con in contests:
        office = con.cfg["office"]
        district = con.cfg["district"]
        for p in con.rows:
            per_type = con.rows[p]
            if not per_type:
                warns.append(f"{office}: no data rows for {p}")
                continue
            n_cols = con.n_cols
            agg = [0] * n_cols
            ed = [0] * n_cols
            ev = [0] * n_cols
            pv = [0] * n_cols
            for vt, vals in per_type.items():
                for k, v in enumerate(vals):
                    agg[k] += v
                    if vt == "Normal":
                        ed[k] += v
                    elif vt in ("Absentee", "Military/Overseas"):
                        ev[k] += v
                    elif vt == "Provisional":
                        pv[k] += v
            names = [c for c, _ in con.cfg["candidates"]] + [WRITE_IN]
            for k in range(n_cols):
                party = con.cfg["candidates"][k][1] if k < len(con.cfg["candidates"]) else ""
                rows.append({
                    "county": "Columbia", "precinct": p, "office": office,
                    "district": district, "party": party, "candidate": names[k],
                    "votes": agg[k], "election_day": ed[k],
                    "early_voting": ev[k], "provisional": pv[k],
                })
    return rows, warns


def verify(contests: list[Contest], problems: list[str]) -> list[str]:
    """Compare summed precinct candidate totals against each contest's own
    county grand-total row (values per column, write-in last)."""
    notes = []
    for con in contests:
        if con.grand is None:
            notes.append(f"{con.cfg['office']}: no grand-total row found")
            continue
        for p, per_type in con.rows.items():
            pass  # per-precinct candidate sums were validated at parse time
        # county-level: sum of parsed candidate values across precincts
        sums = [0] * con.n_cols
        for per_type in con.rows.values():
            for vals in per_type.values():
                for k, v in enumerate(vals):
                    sums[k] += v
        diffs = [(k, s, g) for k, (s, g) in enumerate(zip(sums, con.grand)) if s != g]
        names = [c for c, _ in con.cfg["candidates"]] + ["Write Ins"]
        if diffs:
            for k, s, g in diffs:
                problems.append(
                    f"{con.cfg['office']}: precinct sum {s} != SOVC total {g} for {names[k]}"
                )
        else:
            notes.append(f"{con.cfg['office']}: precinct sums match SOVC county totals")
    return notes


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    problems: list[str] = []
    contests = parse(extract_text(pdf_path), problems)
    if not contests:
        sys.exit("No contests parsed")
    rows, warns = build_rows(contests)
    verify(contests, problems)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"Wrote {len(rows)} rows to {out_path}")
    for w in warns:
        print(f"WARN: {w}")
    for p in problems:
        print(f"ERROR: {p}")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)