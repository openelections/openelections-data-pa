#!/usr/bin/env python3
"""
Parser for Clinton County, PA 2022 General Primary (May 17, 2022)
precinct results.

Source: Clinton PA 2022 Primary Precinct.pdf (Electionware "Summary Results
Report" format, 34 precincts). Read from the layout-preserving pdftotext
extract (a .pdf input is converted internally via pdftotext -layout).

Clinton 2022 primary header style:
  - The PARTY lives on the OFFICE header, not the candidate row:
      "DEM UNITED STATES SENATOR"     -> U.S. Senate | DEM
      "REP GOVERNOR"                  -> Governor | REP
      "DEM LIEUTENANT GOVERNOR"       -> Lieutenant Governor | DEM
      "REP REPRESENTATIVE IN CONGRESS 15TH DISTRICT"
                                      -> U.S. House | 15 | REP
      "DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 76TH DISTRICT"
                                      -> State House | 76 | DEM
      "DEM DEMOCRATIC STATE COMMITTEE PERSON"
                                      -> Member of Democratic State Committee
      "REP REPUBLICAN STATE COMMITTEE PERSON"
                                      -> Member of Republican State Committee
        (anchor strings: 2022/20220517__pa__primary__county.csv; the anchor
         carries district 18 while the source shows none, so district is
         emitted empty here)
  - Per-precinct local party offices are kept as printed with the party
    prefix folded into the office name:
      "DEM DEMOCRATIC COMMITTEE MAN ALLISON TOWNSHIP"
                                      -> Democratic Committee Man | Allison Twp
      "REP REPUBLICAN COMMITTEE WOMAN SOUTH RENOVO BOROUGH"
                                      -> Republican Committee Woman | South
      (2020 primary precedent dropped these; kept here for completeness)
  - Statistics: Registered Voters - Total/DEMOCRATIC/REPUBLICAN/NONPARTISAN,
    Ballots Cast - Total/party/Blank. Per-party RV and BC rows carry the
    party (DEM/REP, NONPARTISAN -> NPA, 2020 Blair/Beaver precedent).
    "Ballots Cast - Blank" -> office "Ballots Cast - Blank".
  - Candidate names are printed ALL-CAPS short forms ("JOSH SHAPIRO",
    "GLENN GT THOMPSON"); they are emitted title-cased. Formal ENR variants
    ("Joshua D. Shapiro", "Alexandria Gloria Khalil") differ; cross-check
    against the anchor is done at party level where names diverge.
  - "NO CANDIDATE FILED" rows are skipped; "Write-In:" detail sub-rows and
    "Not Assigned" are skipped (Write-In Totals covers them); "Total Votes
    Cast" and "Contest Totals" are skipped.
  - Precinct-name typos in the source are corrected to match the 2022
    general file: PINE CREEK TOWSHIP II -> Pine Creek Township II,
    WOOWARD TOWNSHIP -> Woodward Township.

Generated 06/07/2022 -- post GOP-U.S.-Senate-recount OFFICIAL results, so
no amended/recount source is needed (countywide sums match the ENR anchor).

Usage:
    python parsers/pa_clinton_primary_2022_results_parser.py \
        "<input.pdf|input.txt>" "<output.csv>"
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Clinton"

# 7/10-column OpenElections header (breakdowns: election_day, mail,
# provisional from TOTAL/Election Day/Mail/Provisional).
FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

# Contest office headers: raw regex -> (office, district) with the party
# prefix carried separately.
DEM_SUFFIX = "Democratic State Committee"
REP_SUFFIX = "Republican State Committee"

OFFICE_HEADER_RE = re.compile(r"^(DEM|REP)\s+(.+)$")

RAW_OFFICE_MAP = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "DEMOCRATIC STATE COMMITTEE PERSON": ("Member of Democratic State Committee", ""),
    "REPUBLICAN STATE COMMITTEE PERSON": ("Member of Republican State Committee", ""),
}

CONGRESS_RE = re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)", re.IGNORECASE)
GENERAL_ASSEMBLY_RE = re.compile(
    r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)", re.IGNORECASE
)
COMMITTEE_RE = re.compile(r"^(DEMOCRATIC|REPUBLICAN) COMMITTEE (MAN|WOMAN)\s+(.+)$")

VOTE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")

HEADER_SKIP_EXACT = {
    "",
    "Summary Results Report",
    "OFFICIAL RESULTS",
    "STATISTICS",
    "Vote For 1",
}
HEADER_SKIP_STARTS = (
    "2022 GENERAL PRIMARY ELECTION",
    "May 17, 2022",
    "Report generated with Electionware",
)

FOOTER_RE = re.compile(
    r"\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}\s+[AP]M|Page \d+ of \d+"
)
STATS_SKIP_STARTS = (
    "Election Day Precincts Reporting",
    "Precincts Complete",
    "Precincts Partially Reported",
    "Absentee/",
    "Voter Turnout",
)

MC_RE = re.compile(r"^(mc[a-z])", re.IGNORECASE)
ROMAN_RE = re.compile(r"^[IVX]+$", re.IGNORECASE)


def title_candidate(name: str) -> str:
    """Title-case an ALL-CAPS printed candidate name; keep Roman numerals
    and the 'GT' middle-initial run upper; Mc prefix preserved (McSwain)."""

    def fix_word(word: str) -> str:
        if ROMAN_RE.match(word):
            return word.upper()
        if word.upper() == "GT":
            return "GT"
        if MC_RE.match(word):
            return "Mc" + word[2:].capitalize()
        return word.capitalize()

    return " ".join(fix_word(w) for w in name.split())


def prettify_location(name: str) -> str:
    """Title-case a municipality/ward for a committee-office district,
    fixing the source typos TOWSHIP->TOWNSHIP and WOOWARD->WOODWARD."""
    out = []
    for word in name.split():
        up = word.upper()
        if up in ("I", "II", "III"):
            out.append(up)
        elif MC_RE.match(word):
            out.append("Mc" + word[2:].capitalize())
        else:
            w = re.sub(r"TOWSHIP", "Township", word, flags=re.IGNORECASE)
            w = w.capitalize()
            if w == "Wooward":
                w = "Woodward"
            out.append(w)
    return " ".join(out)


def normalize_office_header(line: str) -> tuple[str, str, str] | None:
    """Return (office, district, party) for a 'DEM/REP ...' office header,
    else None."""
    m = OFFICE_HEADER_RE.match(line)
    if not m:
        return None
    party = m.group(1)
    rest = m.group(2).strip()

    if rest in RAW_OFFICE_MAP:
        office, district = RAW_OFFICE_MAP[rest]
        return office, district, party

    m2 = CONGRESS_RE.match(rest)
    if m2:
        return ("U.S. House", m2.group(1), party)

    m2 = GENERAL_ASSEMBLY_RE.match(rest)
    if m2:
        return ("State House", m2.group(1), party)

    m2 = COMMITTEE_RE.match(rest)
    if m2:
        party_word = "Democratic" if m2.group(1) == "DEMOCRATIC" else "Republican"
        kind = "Committee Man" if m2.group(2) == "MAN" else "Committee Woman"
        district = prettify_location(m2.group(3))
        return (f"{party_word} {kind}", district, party)

    return None


def load_text(path: Path) -> str:
    """Read an input .txt or convert a .pdf via pdftotext -layout."""
    if path.suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            out_path = Path(tmp.name)
        cmd = ["pdftotext", "-layout", str(path), str(out_path)]
        subprocess.run(cmd, check=True)
        text = out_path.read_text(encoding="utf-8", errors="replace")
        out_path.unlink(missing_ok=True)
        return text
    return path.read_text(encoding="utf-8", errors="replace")


def find_precinct_name(lines: list[str], stat_i: int) -> str:
    """Return the precinct name: the first non-header line walking back up
    from the STATISTICS marker."""
    for j in range(stat_i - 1, -1, -1):
        ln = lines[j]
        if not ln:
            continue
        if ln.startswith(HEADER_SKIP_STARTS) or ln in HEADER_SKIP_EXACT:
            continue
        if FOOTER_RE.search(ln):
            continue
        return ln
    raise RuntimeError(f"Could not find precinct name above STATISTICS @ line {stat_i}")


def parse_block(precinct_raw: str, block_lines: list[str]) -> list[dict]:
    rows: list[dict] = []
    office: str | None = None
    district: str = ""
    contest_party: str = ""
    in_stats = True  # statistics section precedes the first office header
    pending_vals: tuple | None = None  # wrapped "Ballots Cast" number line

    precinct = PRECINCT_FIXES.get(precinct_raw, precinct_raw)

    def add(office_v, district_v, party_v, candidate, vals):
        total, ed, mail, prov = vals
        rows.append(
            {
                "county": COUNTY,
                "precinct": precinct,
                "office": office_v,
                "district": district_v,
                "party": party_v,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "mail": mail,
                "provisional": prov,
            }
        )

    for line in block_lines:
        if not line:
            continue
        if line.startswith(HEADER_SKIP_STARTS) or line in HEADER_SKIP_EXACT:
            pending_vals = None
            continue
        if FOOTER_RE.search(line):
            pending_vals = None
            continue
        # Repeated precinct name at the top of continuation pages.
        if line == precinct_raw:
            continue

        header_result = normalize_office_header(line)
        if header_result is not None:
            office, district, contest_party = header_result
            in_stats = False
            continue

        # Statistics section rows (single-number RV rows, 4-number BC rows).
        if in_stats:
            # In the per-precinct report, the Ballots Cast labels and their
            # numbers wrap: the four numbers print on the line ABOVE the
            # label ("59 52 7 0" / "Ballots Cast - Total"). Stash a
            # number-only line and attach it to the BC label that follows.
            numonly = re.match(
                r"^([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$", line
            )
            if numonly:
                pending_vals = tuple(
                    int(numonly.group(i).replace(",", "")) for i in (1, 2, 3, 4)
                )
                continue
            m = re.match(
                r"^Ballots Cast - (Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN|Blank)$",
                line,
            )
            if m:
                kind = m.group(1)
                vals = pending_vals
                pending_vals = None
                if vals is None:
                    continue
                if kind == "Blank":
                    add("Ballots Cast - Blank", "", "", "", vals)
                elif kind == "Total":
                    add("Ballots Cast", "", "", "", vals)
                else:
                    add("Ballots Cast", "", STATS_PARTY[kind], "", vals)
                continue
            m = re.match(r"^Registered Voters - (Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+(\d[\d,]*)$", line)
            if m:
                kind = m.group(1)
                votes = int(m.group(2).replace(",", ""))
                if kind == "Total":
                    # RV total has no printed breakdown columns.
                    add("Registered Voters", "", "", "", (votes, "", "", ""))
                else:
                    add("Registered Voters", "", STATS_PARTY[kind], "", (votes, "", "", ""))
                continue
            m = re.match(r"^Ballots Cast - (Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN|Blank)\s+(.+)$", line)
            if m:
                kind = m.group(1)
                tail = VOTE_TAIL_RE.match(kind + " " + m.group(2))
                if tail:
                    vals = tuple(int(tail.group(i).replace(",", "")) for i in (2, 3, 4, 5))
                    if kind == "Blank":
                        add("Ballots Cast - Blank", "", "", "", vals)
                    elif kind == "Total":
                        add("Ballots Cast", "", "", "", vals)
                    else:
                        add("Ballots Cast", "", STATS_PARTY[kind], "", vals)
                continue
            # Any other statistics line (turnout etc.) is skipped.
            continue

        if office is None:
            continue

        vote_m = VOTE_TAIL_RE.match(line)
        if vote_m is None:
            continue
        head = vote_m.group(1).strip()
        vals = tuple(int(vote_m.group(i).replace(",", "")) for i in (2, 3, 4, 5))

        if not head:
            continue
        if head in ("Total Votes Cast", "Contest Totals"):
            continue
        if head == "NO CANDIDATE FILED":
            continue
        if head.startswith("Write-In:") or head == "Not Assigned":
            continue
        if head == "Write-In Totals":
            add(office, district, contest_party, "Write Ins", vals)
            continue

        # Regular candidate row.
        add(office, district, contest_party, title_candidate(head), vals)

    return rows


PRECINCT_FIXES = {
    "PINE CREEK TOWSHIP II": "PINE CREEK TOWNSHIP II",
    "WOOWARD TOWNSHIP": "WOODWARD TOWNSHIP",
}

STATS_PARTY = {
    "DEMOCRATIC": "DEM",
    "REPUBLICAN": "REP",
    "NONPARTISAN": "NPA",
}


def parse_text(raw_text: str) -> list[dict]:
    """Parse the full report text into OpenElections rows.

    Precinct blocks are delimited by STATISTICS markers; the first
    statistics marker ends precinct 0's name line, so every line between
    STATISTICS[i] and STATISTICS[i+1] (inclusive of trailing page headers,
    which the row parser skips) belongs to precinct i."""
    lines = [ln.strip() for ln in raw_text.split("\n")]
    stat_idx = [i for i, ln in enumerate(lines) if ln == "STATISTICS"]
    if not stat_idx:
        raise RuntimeError("No STATISTICS markers found; wrong PDF format?")

    names = [find_precinct_name(lines, i) for i in stat_idx]

    rows: list[dict] = []
    for n, i in enumerate(stat_idx):
        end = stat_idx[n + 1] if n + 1 < len(stat_idx) else len(lines)
        rows.extend(parse_block(names[n], lines[i + 1 : end]))
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        import csv

        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} <input.pdf|input.txt> <output.csv>")
    in_path = Path(argv[1])
    out_path = Path(argv[2])
    if not in_path.exists():
        sys.exit(f"Missing input: {in_path}")
    text = load_text(in_path)
    rows = parse_text(text)
    precincts = sorted({r["precinct"] for r in rows})
    write_csv(rows, out_path)
    print(
        f"Wrote {len(rows)} rows across {len(precincts)} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)