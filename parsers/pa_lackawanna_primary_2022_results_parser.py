#!/usr/bin/env python3
"""
Parse Lackawanna County PA 2022 Primary (May 17, 2022) precinct results.

Source: "Lackawanna PA PRIMARY22.CERTIFIED.PRECINCT.pdf" (672 pages, 163
precincts) — an Electionware "Precinct Results Report" print, the same
report family as Lackawanna's 2023 primary/general certified precinct PDFs
(engines: electionware_txt_2023, pa_lackawanna_*_2023_results_parser).
pdftotext -layout separates the columns cleanly, so the shared
``electionware_txt_2023.parse_report`` engine is reused from this wrapper.

Report vintage: the print is dated 06/07/2022 ("CERTIFIED RESULTS", after the
county canvass). Its countywide totals for every 2022-primary contest match
the ENR anchor (post-Oz/McCormick-recount electionreturns.pa.gov feed)
exactly — including the Republican U.S. Senate contest (Oz 7,468 /
McCormick 5,512) — so no separate recount amendment is needed for
Lackawanna: this print already reflects the state's post-recount totals.

Primary-specific handling in this wrapper (the rules are the 2023-primary
parser's, see pa_lackawanna_primary_2023_results_parser.py):
  * contest headers carry a party prefix ("DEM UNITED STATES SENATOR") ->
    the prefix is stripped and the contest party is written to the party
    column for every candidate row of that contest, INCLUDING the 'write-ins'
    rows (party-empty write-ins fail duplicate_entries in primaries);
  * the per-precinct statistics block reports Registered Voters and
    Ballots Cast per party (DEM/REP/NONPARTISAN) in addition to Total; those
    per-party rows are emitted (Indiana 2022-primary precedent) with the
    party in the party column;
  * 'MEMBER OF DEMOCRATIC / REPUBLICAN STATE COMMITTEE' headers are mapped
    to the anchor's exact office strings; 'COMMITTEEMAN / COMMITTEEWOMAN'
    (party committee, per precinct) are kept as printed with the trailing
    precinct qualifier stripped;
  * 'REPRESENTATIVE IN CONGRESS' carries no district number in the header;
    Lackawanna is entirely in the 8th Congressional district and both party
    contests report 'Vote For 1', so the district is 8;
  * 'SENATOR IN THE GENERAL ASSEMBLY 22ND DIST' -> State Senate /
    'REP IN THE GENERAL ASSEMBLY 112TH DIST' -> State House with the
    district number parsed from the header;
  * page furniture ("PRIMARY ELECTION", "May 17, 2022 Lackawanna County"),
    which the shared engine does not know, is dropped; a synthetic footer
    line is prepended so the first precinct is captured;
  * ALL-CAPS precinct lines are title-cased ("WAVERLY TOWNSHIP W-00 P-00" ->
    "Waverly Township W-00 P-00").

Usage:
    python parsers/pa_lackawanna_primary_2022_results_parser.py \
        <input.pdf-or-.txt> <output.csv>

Output header:
  county,precinct,office,district,party,candidate,votes,
  election_day,mail,provisional
"""

import os
import re
import sys

try:
    import electionware_txt_2023 as ew
except ImportError:  # allow running from the repo root
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import electionware_txt_2023 as ew

# In this primary the candidate rows carry no per-row party token (the party
# is in the contest header), while the engine classifies a row by a leading
# party code.  Patch the engine's row-party regex at runtime (same technique
# the 2023-primary parser uses) so a plain candidate row is accepted with an
# empty row party; the contest party is attached afterwards from the office
# tag.  Aggregate/label heads are excluded so they reach their own branches.
ew.PARTY_RE = re.compile(
    r"^()(?!Write-In Totals$|Overvotes$|Undervotes$|Not Assigned$|"
    r"Write-In:)(.+)$",
    re.IGNORECASE,
)

COUNTY = "Lackawanna"

PAGE_HEADER_KEY = "LACKAWANNA PRECINCT"

# candidate rows get the contest party, as do the contest's 'write-ins'
# rows (repo primary convention: the CONTEST's party is stamped on
# write-in/aggregate rows so the DEM and REP sections of one office never
# collide in the duplicate_entries test). Metadata rows (Registered Voters /
# Ballots Cast) and their per-party companions stay candidate-empty and get
# their party from the stat line itself.
NO_PARTY_CANDIDATES = {"", "No Candidate Filed"}

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
PRECINCT_SUFFIX_RE = re.compile(r"\s+W-\S+\s+P-\S+\s*$")

STATECOM_RE = re.compile(r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) STATE COMMITTEE$")
LEG_DIST_RE = re.compile(r"(\d+)(?:ST|ND|RD|TH)\s+DIST$", re.IGNORECASE)

PAGE_FURNITURE_RE = re.compile(
    r"^\s*((MUNICIPAL )?PRIMARY ELECTION"
    r"|May 17, 2022\s+LACKAWANNA COUNTY)\s*$",
    re.IGNORECASE,
)

STAT_PARTY_RE = re.compile(r"^Registered Voters - (DEM|REP|NONPART)\b")


# --------------------------------------------------------------------------
# contest header mapping
# --------------------------------------------------------------------------

def split_party(raw):
    """Return (party, body) for a 'DEM ...'/'REP ...' header, else None."""
    h = re.sub(r"\s+", " ", raw.strip())
    m = PARTY_PREFIX_RE.match(h)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).strip()


def normalize_body(body):
    """Strip the trailing precinct qualifier from committee headers."""
    b = PRECINCT_SUFFIX_RE.sub("", body).strip()
    # spelling/spacing seen in the header text
    b = b.replace("LA PLUME", "LAPLUME")
    return b


def map_body(body):
    """Map a party-stripped contest body to (office, district)."""
    b = normalize_body(body)
    upper = b.upper()

    if upper == "UNITED STATES SENATOR":
        return ("U.S. Senate", "")
    if upper == "GOVERNOR":
        return ("Governor", "")
    if upper == "LIEUTENANT GOVERNOR":
        return ("Lieutenant Governor", "")
    if upper == "REPRESENTATIVE IN CONGRESS":
        # Lackawanna is entirely in the 8th CD; the header carries no number.
        return ("U.S. House", "8")

    m = STATECOM_RE.match(upper)
    if m:
        name = {"DEMOCRATIC": "Democratic", "REPUBLICAN": "Republican"}[
            m.group(1)]
        return ("Member of %s State Committee" % name, "")

    if upper.startswith("SENATOR IN THE GENERAL ASSEMBLY"):
        d = LEG_DIST_RE.search(upper)
        if d:
            return ("State Senate", d.group(1))
    if upper.startswith("REP IN THE GENERAL ASSEMBLY"):
        d = LEG_DIST_RE.search(upper)
        if d:
            return ("State House", d.group(1))
    if upper.startswith("COMMITTEEMAN"):
        return ("Committeeman", "")
    if upper.startswith("COMMITTEEWOMAN"):
        return ("Committeewoman", "")
    return None


def map_header(raw):
    """Shared header mapper: party prefix + contest body.

    Returns (office, district) or None for unrecognized headers (reported
    as warnings by the engine, never guessed).
    """
    split = split_party(raw)
    if split is None:
        return None
    mapped = map_body(split[1])
    if mapped is None:
        return None
    # the contest party travels in the office tag; untagged before the write
    return (mapped[0] + "\t" + split[0], mapped[1])


def untag_rows(rows):
    """Move the office's party tag into the party column."""
    for r in rows:
        if "\t" in r["office"]:
            office, party = r["office"].split("\t", 1)
            r["office"] = office
            if r["candidate"] not in NO_PARTY_CANDIDATES:
                r["party"] = party
    return rows


# --------------------------------------------------------------------------
# per-party statistics (Registered Voters / Ballots Cast by party)
# --------------------------------------------------------------------------

RV_PARTY_RE = re.compile(r"^Registered Voters - (DEMOCRATIC|REPUBLICAN|"
                         r"NONPARTISAN)\s+([\d,]+)\s*$")
BC_PARTY_RE = re.compile(r"^Ballots Cast - (DEMOCRATIC|REPUBLICAN|"
                         r"NONPARTISAN)\s+([\d,]+)\s+([\d,]+)\s+"
                         r"([\d,]+)\s+([\d,]+)\s*$")
PARTY_ABBR = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP", "NONPARTISAN": "NON"}

# every precinct name line in the print ends with the W-xx P-yy qualifier
PRECINCT_LINE_RE = re.compile(r"^[A-Z][A-Z .,'()&/-]*\s+W-\S+\s+P-\S+$")


def prettify_precinct(s):
    """'CARBONDALE TWP. W-00 P-NE' -> 'Carbondale Twp. W-00 P-NE'."""
    out = []
    for tok in s.split():
        if "-" in tok or any(ch.isdigit() for ch in tok):
            out.append(tok)  # W-00 / P-NE / P-1-1 keep their source form
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def collect_party_stats(text):
    """(precinct -> [(kind, party, vals)]) from the per-party stat lines."""
    stats = {}
    expect_precinct = False
    precinct = None
    for raw in text.split("\n"):
        s = raw.strip()
        if not s:
            continue
        if PAGE_HEADER_KEY in raw:
            expect_precinct = True
            continue
        if expect_precinct:
            if PRECINCT_LINE_RE.match(s):
                expect_precinct = False
                precinct = prettify_precinct(s)
            continue
        m = RV_PARTY_RE.match(s)
        if m:
            stats.setdefault(precinct, []).append(
                ("Registered Voters", PARTY_ABBR[m.group(1)],
                 [int(m.group(2).replace(",", "")), None, None, None]))
            continue
        m = BC_PARTY_RE.match(s)
        if m:
            stats.setdefault(precinct, []).append(
                ("Ballots Cast", PARTY_ABBR[m.group(1)],
                 [int(x.replace(",", "")) for x in m.groups()[1:]]))
            continue
    if not stats and precinct is None:
        print("WARNING: no per-party statistics collected", file=sys.stderr)
    return stats


PARTY_STAT_OFFICES = {"Registered Voters", "Ballots Cast"}


def inject_party_stats(rows, stats):
    """Insert per-party RV/BC rows after their Total row, per precinct."""
    if not stats:
        return rows
    out = []
    inserted = 0
    for r in rows:
        out.append(r)
        if r["office"] in PARTY_STAT_OFFICES and r["candidate"] == "" \
                and r["party"] == "" and r["precinct"] in stats:
            for kind, party, vals in stats[r["precinct"]]:
                if kind != r["office"]:
                    continue
                nr = dict.fromkeys(ew.FIELDNAMES, "")
                nr.update(precinct=r["precinct"], office=kind, district="",
                          party=party, candidate="")
                vals_out = [v for v in vals]
                for field, v in zip(("votes", "election_day", "mail",
                                     "provisional"), vals_out):
                    if v is not None:
                        nr[field] = str(v)
                out.append(nr)
                inserted += 1
    used = {p for p in stats}
    seen = {r["precinct"] for r in rows}
    missing = used - seen
    if missing:
        print("WARNING: party stats for precincts absent in rows: %s"
              % sorted(missing), file=sys.stderr)
    print(f"per-party stat rows injected: {inserted}")
    return out


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def preprocess_precinct(text):
    """Drop page furniture the shared engine does not know; add the missing
    first-page footer so the first precinct is captured."""
    lines = ["LACKAWANNA PRECINCT - 06/07/2022         2:53 PM   0 of 672"]
    for line in text.split("\n"):
        if PAGE_FURNITURE_RE.match(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def load_text(src):
    return ew.load_text(src)


def run_precinct(src, dst):
    text = load_text(src)
    text = preprocess_precinct(text)
    rows, warnings = ew.parse_report(
        text, COUNTY, map_header,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=("PRIMARY ELECTION", "CERTIFIED RESULTS"),
        strip_pct=True,
        prettify_precinct=prettify_precinct,
        log=lambda m: print(m, file=sys.stderr),
    )
    untag_rows(rows)
    stats = collect_party_stats(text)
    rows = inject_party_stats(rows, stats)
    ew.write_csv(rows, dst, COUNTY)
    print(f"wrote {len(rows)} rows -> {dst} ({len(warnings)} warnings)")
    for w in warnings[:50]:
        print("WARN:", w, file=sys.stderr)
    return len(rows)


def main():
    if len(sys.argv[1:]) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf-or-.txt> <output.csv>")
    run_precinct(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()