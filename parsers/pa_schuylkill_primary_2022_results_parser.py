#!/usr/bin/env python3
"""
Parser for Schuylkill County, PA 2022 Primary Election (May 17, 2022)
precinct results.

Source: "Schuylkill PA 2022 Primary Precinct Summary.pdf"
(Electionware "Summary Results Report", 608 pages, 125 precincts).

Format notes (differs from the Electionware-txt family used for the
Schuylkill/other 2022 general reports):
  - No STATISTICS block: the report has no Registered Voters / Ballots Cast
    rows, so none are emitted.
  - Contest headers carry the ballot party as a prefix:
        "DEM United States Senator" / "REP Governor"
    and candidate rows carry no party code.
  - Each precinct's page sequence always begins with "DEM United States
    Senator", so a page whose first contest is that one starts a new
    precinct; later pages of the same precinct are continuations.
  - County and state committee races exist:
        "DEM Member of Democratic State Committee"
        "REP Member of Republican County Committee <locality>"
        "REP Member of Republican Alternate County Committee <locality>"
  - The report is dated 2022-06-03 and -- like Schuylkill's own county-wide
    summaries -- OMITS the Republican U.S. Senate contest entirely
    (pre-recount exclusion, cf. Bucks/Centre/Lebanon).  No per-precinct
    recount source exists for Schuylkill, so no GOP U.S. Senate rows are
    emitted; the ENR anchor values are quoted in the validation notes.
  - Cross-filed state-committee candidates print as printed
    ("Liz Bettinger, Female").

Office mapping:
  "(DEM|REP) United States Senator"                         -> U.S. Senate
  "(DEM|REP) Governor"                                      -> Governor
  "(DEM|REP) Lieutenant Governor"                           -> Lieutenant Governor
  "(DEM|REP) Representative in Congress"                    -> U.S. House (9)
  "(DEM|REP) Representative in the General Assembly <N>D." -> State House (N)
  "(DEM|REP) Member of ... State Committee"                 -> as printed
  "(DEM|REP) Member of ... County Committee <loc>"          -> as printed

Column mapping: TOTAL -> votes, Election Day -> election_day,
Mail Votes -> mail, Provisional -> provisional.

Usage:
    python parsers/pa_schuylkill_primary_2022_results_parser.py \
        "<input.pdf|input.txt>" "<output.csv>"
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Schuylkill"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

# Page / header junk.
JUNK_RES = [
    re.compile(r"^Summary Results Report\b"),
    re.compile(r"^May 17 2022 Gubernatorial Primary$"),
    re.compile(r"^February 24, 2022\b"),
    re.compile(r"^Schuylkill County\s*$"),
    re.compile(r"^Precinct Summary - \d"),
    re.compile(r"^Report generated with Electionware\b"),
    re.compile(r"^Vote For \d+$"),
]

# Contest headers: party prefix + title (titles may end in a ward/range
# number, e.g. "... County Committee Pottsville 3-2").
OFFICE_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$")

# Column-header lines: no digits, words drawn from the header vocabulary.
COL_HEADER_WORDS_RE = re.compile(
    r"^(?:TOTAL\s+)?(?:Election\s+)?(?:Mail\s+Votes\s+)?"
    r"(?:Provisional\s*)?(?:Day)?\s*$"
)

ASSEMBLY_RE = re.compile(
    r"^Representative in the General Assembly\s+(\d+)(?:st|nd|rd|th)?\s+District$",
    re.IGNORECASE,
)
CONGRESS_RE = re.compile(r"^Representative in Congress$", re.IGNORECASE)
US_SENATE_RE = re.compile(r"^United States Senator$", re.IGNORECASE)
GOVERNOR_RE = re.compile(r"^Governor$", re.IGNORECASE)
LT_GOVERNOR_RE = re.compile(r"^Lieutenant Governor$", re.IGNORECASE)
COUNTY_COMMITTEE_RE = re.compile(
    r"^(Member of (?:Democratic|Republican(?:\s+Alternate)?)\s+County Committee)"
    r"\s+(.+)$",
    re.IGNORECASE,
)

NUM_RE = re.compile(r"^\d[\d,]*$")

# Sub-locality suffixes that the county's 2022 general precinct file renders
# with a " - " separator ("Butler Twp. - Northeast", "St. Clair - North").
SUBLOCALITY_WORDS = {
    "East", "West", "North", "South", "Middle", "Englewood",
    "Fountain Springs", "Lavelle", "Northeast", "Currans", "Marlin",
    "Altamont", "Elixir",
}


def prettify_precinct(name: str) -> str:
    """Match the precinct-name conventions of this county's 2022 general
    precinct file (20221108__pa__general__schuylkill__precinct.csv):
      - "Twp" / "Twp." -> "Twp."
      - "Mt." -> "Mount" (only "Mt. Carbon" exists here)
      - a directional / sub-locality suffix gets a " - " separator
      - a trailing " Ward" ordinal is dropped ("Palo Alto 1st Ward")
    Verified to map all 125 printed names 1:1 onto that file's names.
    The source's "West Mahano Twp" county-committee typo is corrected here
    as well ("West Mahanoy Twp"; the precinct name itself is spelled
    correctly in the source)."""
    n = name.strip()
    if n == "Mt. Carbon":
        return "Mount Carbon"
    out = re.sub(r"\bTwp\b\.?", "Twp.", n)
    for w in SUBLOCALITY_WORDS:
        out = re.sub(r"\s+" + re.escape(w) + r"$", " - " + w, out)
    out = re.sub(r"\s+Ward$", "", out)
    # source typo in county-committee office headers only
    out = re.sub(r"\bWest Mahano\b", "West Mahanoy", out)
    return out


def trailing_nums(line: str):
    """Trailing integer tokens of a line, as a (head, [nums]) tuple."""
    toks = line.split()
    nums = []
    for tok in reversed(toks):
        if NUM_RE.match(tok):
            nums.append(tok)
        else:
            break
    nums.reverse()
    head = " ".join(toks[: len(toks) - len(nums)]) if nums else line
    return head, [n.replace(",", "") for n in nums]


def is_junk(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if any(rx.search(s) for rx in JUNK_RES):
        return True
    toks = s.split()
    if not any(NUM_RE.match(t) for t in toks):
        if set(toks) <= {"TOTAL", "Election", "Day", "Mail", "Votes", "Provisional"}:
            return True
    return False


def normalize_office(title: str):
    """Party-stripped contest title -> (office, district)."""
    t = re.sub(r"\s+", " ", title).strip()
    if US_SENATE_RE.match(t):
        return ("U.S. Senate", "")
    if GOVERNOR_RE.match(t):
        return ("Governor", "")
    if LT_GOVERNOR_RE.match(t):
        return ("Lieutenant Governor", "")
    if CONGRESS_RE.match(t):
        # Every Schuylkill precinct is in the 9th U.S. House district.
        return ("U.S. House", "9")
    m = ASSEMBLY_RE.match(t)
    if m:
        return ("State House", m.group(1))
    m = COUNTY_COMMITTEE_RE.match(t)
    if m:
        # County-committee suffixes stay as printed: they often name a
        # locality rather than a single precinct ("Pottsville",
        # "North Manheim 1st") and the source's own spelling drifts
        # ("Butler Twp Levelle", "West Mahanoy Altamont").  Only the
        # unambiguous "West Mahano" misspelling is corrected.
        suffix = re.sub(r"\s+", " ", m.group(2)).strip()
        suffix = re.sub(r"\bWest Mahano\b", "West Mahanoy", suffix)
        return (f"{m.group(1)} {suffix}", "")
    if "State Committee" in t:
        return (t, "")
    return (t, "")


def candidate_label(head: str):
    """Non-candidate aggregate labels -> output label or None to skip."""
    u = " ".join(head.split()).upper()
    if u == "WRITE-IN TOTALS":
        return "Write Ins"
    if u in ("NOT ASSIGNED", "TOTAL VOTES CAST", "CONTEST TOTALS"):
        return None
    return None


def parse_pages(lines, warnings):
    """Yield one (page_lines) chunk per 'Summary Results Report' page."""
    pages, cur = [], None
    for ln in lines:
        if JUNK_RES[0].match(ln.strip()):
            if cur is not None:
                pages.append(cur)
            cur = []
            continue
        if cur is not None:
            cur.append(ln.rstrip("\n"))
    if cur is not None:
        pages.append(cur)
    if not pages:
        raise RuntimeError("no 'Summary Results Report' pages found")
    return pages


def page_first_contest(page: list):
    """First (party, title) contest line of a page, or None."""
    for ln in page:
        s = ln.strip()
        if is_junk(s):
            continue
        m = OFFICE_PREFIX_RE.match(s)
        if m:
            return m.group(1), m.group(2)
    return None


def parse_page(page, precinct_name, warnings):
    """Parse one page into rows for the given precinct."""
    rows = []
    office = district = party = None
    for ln in page:
        s = re.sub(r"\s{2,}", " ", ln.strip())
        if not s:
            continue
        if prettify_precinct(s) == precinct_name:
            # Continuation-page repeat of the precinct name.
            continue
        m = OFFICE_PREFIX_RE.match(s)
        if m:
            office, district = normalize_office(m.group(2))
            party = m.group(1)
            continue
        if s.startswith("Vote For"):
            continue
        if is_junk(ln.strip()):
            continue
        if office is None:
            warnings.append(f"{precinct_name}: line before any contest: {s!r}")
            continue
        head, nums = trailing_nums(s)
        if not nums:
            if s != precinct_name:
                warnings.append(f"{precinct_name}: no votes on {s!r}")
            continue
        if party is None:
            warnings.append(f"{precinct_name}: data row with no contest: {s!r}")
            continue
        u = head.upper()
        if u == "WRITE-IN TOTALS":
            candidate = "Write Ins"
        elif u in ("NOT ASSIGNED", "TOTAL VOTES CAST", "CONTEST TOTALS"):
            continue
        elif u in ("OVERVOTES", "UNDERVOTES", "YES", "NO"):
            candidate = head
        else:
            candidate = head
        if len(nums) == 4:
            votes, ed, mailp, prov = nums
        elif len(nums) == 1:
            votes, ed, mailp, prov = nums[0], "", "", ""
        else:
            warnings.append(f"{precinct_name}: odd numbers {nums} on {s!r}")
            continue
        rows.append({
            "county": COUNTY,
            "precinct": precinct_name,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": votes,
            "election_day": ed,
            "mail": mailp,
            "provisional": prov,
        })
    return rows


def parse_txt(text, warnings):
    lines = text.split("\n")
    pages = parse_pages(lines, warnings)
    rows = []
    precincts = []
    current = None
    for page in pages:
        clean = [ln.strip() for ln in page if not is_junk(ln.strip()) and ln.strip()]
        if not clean:
            warnings.append("empty page skipped")
            continue
        # First clean line of a page is the precinct name.
        first_contest = page_first_contest(page)
        if first_contest and first_contest[1].lower() == "united states senator" \
                and first_contest[0] == "DEM":
            name = prettify_precinct(re.sub(r"\s{2,}", " ", clean[0]))
            current = name
            precincts.append(name)
        if current is None:
            warnings.append(f"page before any precinct: {clean[:2]}")
            continue
        rows.extend(parse_page(page, current, warnings))
    return rows, precincts


def parse_input(path: str, warnings):
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
        subprocess.run(["pdftotext", "-layout", str(p), str(tmp)], check=True)
        text = tmp.read_text(encoding="utf-8", errors="replace")
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
    return parse_txt(text, warnings)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf|input.txt> <output.csv>")
    warnings: list = []
    rows, precincts = parse_input(argv[1], warnings)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(
        f"Wrote {len(rows)} rows across {len(precincts)} precincts to {out_path}"
    )
    dup = len(precincts) - len(set(precincts))
    if dup:
        warnings.append(f"{dup} duplicate precinct name(s): "
                        f"{[p for p in set(precincts) if precincts.count(p) > 1]}")
    if warnings:
        print(f"WARNING: {len(warnings)} issue(s):")
        for wn in warnings[:40]:
            print("  " + wn)
        if len(warnings) > 40:
            print("  ...")
    else:
        print("No warnings")


if __name__ == "__main__":
    main(sys.argv)