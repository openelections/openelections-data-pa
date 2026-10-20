#!/usr/bin/env python3
"""Tioga County, PA 2022 General Primary (May 17, 2022) — precinct results.

Source: Tioga PA May-2022-Primary-Official-Results.pdf — pdftotext -layout
extract of Tioga's Electionware "Summary Results Report" (OFFICIAL RESULTS,
generated 06/06/2022, post-recount).  The file concatenates the county
"Election Summary" (30 pages, footers "Election Summary - ... N of 30") and
a per-municipality "Precinct Summary" report (246 pages, footers
"Precinct Summary - ... N of 246", 41 municipality sections).  This parser
runs in --precinct mode only and writes the 10-column precinct CSV
(county, precinct, office, district, party, candidate, votes,
election_day, mail, provisional).

Usage:
    python parsers/pa_tioga_primary_2022_results_parser.py --precinct \
        <input.pdf-or.txt> 2022/counties/20220517__pa__primary__tioga__precinct.csv

Same structure as the Tioga 2023 primary source; parsing approach copied
from parsers/pa_tioga_primary_2023_results_parser.py (shared engine
pa_2023_summary_common.py is used unmodified):

- Candidate/detail rows carry 4 numbers: total, Election Day,
  Absentee/Mail-in, Provisional -> votes, election_day, mail, provisional.
- "Write-In: NAME" detail rows and "Not Assigned" are folded into the
  "Write-In Totals" aggregate, emitted as candidate "Write-ins" carrying
  the contest's party (party-empty write-ins fail duplicate_entries in a
  primary).
- Registered Voters / Ballots Cast per municipality are emitted from the
  section's Statistics block ("Registered Voters - Total", "Ballots Cast -
  Total", "Ballots Cast - Blank"); the per-party statistics lines and
  Voter Turnout lines are not emitted (repo convention records only
  totals).
- Contest headers carry the ballot party as a prefix ("DEM GOVERNOR",
  "REP REP COMM MAN Blossburg Borough"; note the source's typo
  "DEM DEMCOMM Sullivan Township").  Office mapping:
    UNITED STATES SENATOR            -> U.S. Senate
    GOVERNOR                          -> Governor
    LIEUTENANT GOVERNOR               -> Lieutenant Governor
    REP IN CONGRESS 15TH DIS          -> U.S. House, district 15
    REP IN THE GEN ASSEM 68TH DIS     -> State House, district 68
    MEMBER DEM STATE COMM 59TH DIST / MEMBER OF THE REPUBLICAN STATE
      COMMITTEE                       -> "Member of Democratic State
      Committee" / "Member of Republican State Committee" (the 2022
      primary anchor's names), district 59 for both (D header says
      59TH DIST; the R header has no district but the anchor reports the
      R race in district 59)
    DEM COMM <muni>                   -> "Democratic County Committee <Muni>"
    REP COMM MAN/WOMAN <muni>         -> "Republican County Committeeman /
      Committeewoman <Muni>" (office names like Westmoreland's
      "Democratic County Committee <Muni>" convention, keeping the
      source's man/woman seat distinction)
- Municipality name normalization matches the Tioga 2022 general
  precinct file: "Elkland Boro" -> "Elkland Borough",
  "Wellsboro Boro Ward 1/2" -> "Wellsboro Ward One/Two", source typo
  "Gaines Townsip" -> "Gaines Township", lowercase "township", etc.
"""

import csv
import re
import sys

from pa_2023_summary_common import is_junk, nums_from, text_from_pdf

COUNTY = "Tioga"

PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "mail",
                       "provisional"]

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.I)

VOTEFOR_RE = re.compile(r"^vote for (\d+)$", re.I)
PRECINCTS_REPORTING_RE = re.compile(r"^Precincts Reporting\b", re.I)

# the county "Election Summary" pages (cut the precinct report out at the
# first of these) and the appended per-municipality "Precinct Summary"
# report (the only part parsed; cut at its last footer)
ELECTION_SUMMARY_FOOTER_RE = re.compile(r"Election Summary - \d")
PRECINCT_FOOTER_RE = re.compile(r"Precinct Summary - \d")

# per-party statistics / turnout lines: never emit (repo convention
# records only totals)
PER_PARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN|GREEN|LIBERTARIAN)\b", re.I)
TURNOUT_RE = re.compile(r"^Voter Turnout - ", re.I)

STATS_RE = re.compile(r"^Statistics\b", re.I)

EXACT_OFFICES = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "REP IN CONGRESS 15TH DIS": ("U.S. House", "15"),
    "REP IN THE GEN ASSEM 68TH DIS": ("State House", "68"),
    "MEMBER DEM STATE COMM 59TH DIST":
        ("Member of Democratic State Committee", "59"),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", "59"),
}

COMMITTEE_RE = re.compile(r"^(DEM COMM|DEMCOMM|REP COMM MAN|REP COMM WOMAN)"
                          r"\s+(.+)$", re.I)

SEQ_WORDS = {"1": "One", "2": "Two"}


WELLSBORO_WARD_RE = re.compile(r"^(Wellsboro) (?:Boro )?Ward (\d)$", re.I)


def muni(raw):
    """Normalize a Tioga municipality name to the repo's spellings (the
    Tioga 2022 general precinct file's names)."""
    s = " ".join(raw.strip().split())
    s = re.sub(r"\btownsip\b", "Township", s, flags=re.I)
    s = re.sub(r"\btownship\b", "Township", s, flags=re.I)
    w = WELLSBORO_WARD_RE.match(s)
    if w:
        return f"Wellsboro Ward {SEQ_WORDS[w.group(2)]}"
    s = re.sub(r"\bBoro(?![a-z])", "Borough", s)
    return s


def classify(header):
    """(office, district) for a contest header, party stripped."""
    h = " ".join(header.strip().split())
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h]
    m = COMMITTEE_RE.match(h)
    if m:
        kind, pl = m.group(1).upper(), m.group(2)
        place = muni(pl)
        if kind in ("DEM COMM", "DEMCOMM"):
            return f"Democratic County Committee {place}", ""
        if kind == "REP COMM MAN":
            return f"Republican County Committeeman {place}", ""
        return f"Republican County Committeewoman {place}", ""
    raise SystemExit(f"unrecognized contest header: {header!r}")


def parse_precinct(path, dst):
    """Parse the appended per-municipality "Precinct Summary" report into
    the 10-column precinct CSV."""
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    esf = [i for i, l in enumerate(lines)
           if ELECTION_SUMMARY_FOOTER_RE.search(l)]
    psf = [i for i, l in enumerate(lines)
           if PRECINCT_FOOTER_RE.search(l)]
    if not psf:
        raise SystemExit("no 'Precinct Summary' report in the source")
    start = esf[-1] + 1 if esf else 0
    sec = lines[start:psf[-1] + 1]

    # contest-header lines (the nonblank line above each "Vote For" line)
    hdr_idx = set()
    for i, l in enumerate(sec):
        if VOTEFOR_RE.match(l.strip()):
            j = i - 1
            while j >= 0 and not sec[j].strip():
                j -= 1
            if j >= 0:
                hdr_idx.add(j)

    rows = []
    totals = {}          # (precinct, office, district, party) -> Total Votes Cast
    agg = {}             # same key -> sum of emitted candidate+write-in rows
    precinct = None
    current = None

    for i, l in enumerate(sec):
        s = l.strip()
        m = VOTEFOR_RE.match(s)
        if m:
            j = i - 1
            while j >= 0 and not sec[j].strip():
                j -= 1
            hm = PARTY_PREFIX_RE.match(sec[j].strip())
            if not hm or precinct is None:
                raise SystemExit(
                    f"contest block outside a municipality: {sec[j]!r}")
            office, district = classify(hm.group(2))
            current = (precinct, office, district, hm.group(1).upper(),
                       int(m.group(1)))
            continue
        if not s or i in hdr_idx or is_junk(s):
            continue
        if PER_PARTY_STATS_RE.match(s) or TURNOUT_RE.match(s):
            continue
        if s.upper().startswith("REGISTERED VOTERS - TOTAL"):
            n = nums_from(s.split())
            if n and precinct is not None:
                rows.append([COUNTY, precinct, "Registered Voters", "", "",
                             "", n[0], "", "", ""])
            continue
        if s.upper().startswith("BALLOTS CAST - TOTAL"):
            n = nums_from(s.split())
            if n and precinct is not None:
                rows.append([COUNTY, precinct, "Ballots Cast", "", "",
                             "", n[0], n[1] if len(n) > 1 else "",
                             n[2] if len(n) > 2 else "",
                             n[3] if len(n) > 3 else ""])
            continue
        if s.upper().startswith("BALLOTS CAST - BLANK"):
            n = nums_from(s.split())
            if n and precinct is not None:
                rows.append([COUNTY, precinct, "Ballots Cast - Blank", "",
                             "", "", n[0], n[1] if len(n) > 1 else "",
                             n[2] if len(n) > 2 else "",
                             n[3] if len(n) > 3 else ""])
            continue
        if WELLSBORO_WARD_RE.match(s):
            # ward headers carry a digit, so they'd fall through to the
            # candidate-row branch; switch precinct here
            precinct = muni(s)
            current = None
            continue
        if not any(c.isdigit() for c in s):
            # municipality section header: digit-free, followed within a
            # few lines by the section's Statistics block
            nxt = [sec[k].strip() for k in range(i + 1, min(i + 5, len(sec)))
                   if sec[k].strip()]
            if nxt and STATS_RE.match(nxt[0]):
                precinct = muni(s)
                current = None
            continue
        if current is None:
            continue
        p_c, office, district, party, vfn = current
        key = (p_c, office, district, party)
        n = nums_from(s.split())
        if s.upper().startswith("WRITE-IN TOTALS"):
            if n:
                rows.append([COUNTY, p_c, office, district, party,
                             "Write-ins", int(n[0]),
                             n[1] if len(n) > 1 else "",
                             n[2] if len(n) > 2 else "",
                             n[3] if len(n) > 3 else ""])
                agg[key] = agg.get(key, 0) + int(n[0])
        elif s.upper().startswith("WRITE-IN:") \
                or s.upper().startswith("NOT ASSIGNED") \
                or s.upper().startswith("CONTEST TOTALS") \
                or PRECINCTS_REPORTING_RE.match(s):
            pass  # write-in detail rows fold into the aggregate; skip the rest
        elif s.upper().startswith("TOTAL VOTES CAST"):
            totals[key] = int(n[0]) if n else None
        else:
            name = re.sub(r"\s+", " ", re.sub(r"[\d,].*$", "", s)).strip()
            if name and n:
                total = int(n[0])
                ed = n[1] if len(n) > 1 else ""
                mi = n[2] if len(n) > 2 else ""
                pr = n[3] if len(n) > 3 else ""
                rows.append([COUNTY, p_c, office, district, party,
                             name, total, ed, mi, pr])
                agg[key] = agg.get(key, 0) + total

    bad = [(k, agg[k], t) for k, t in totals.items() if agg.get(k) != t]
    if bad:
        for k, a, t in bad:
            print(f"WARNING {k[1]} | {k[2]} | {k[3]} | {k[0]}: rows sum "
                  f"{a} != Total Votes Cast {t}")
    else:
        print(f"contest totals check: all {len(totals)} contests reconcile")

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRECINCT_FIELDNAMES)
        w.writerows(rows)
    pcs = {r[1] for r in rows}
    print(f"wrote {len(rows)} rows -> {dst} ({len(pcs)} precincts, "
          f"{len(totals)} contest blocks)")


def main():
    args = sys.argv[1:]
    if "--precinct" in args:
        args = [a for a in args if a != "--precinct"]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} [--precinct] <input.pdf|.txt> "
                 f"<output.csv>")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    parse_precinct(src, dst)


if __name__ == "__main__":
    main()