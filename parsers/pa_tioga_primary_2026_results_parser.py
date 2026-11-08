#!/usr/bin/env python3
"""Tioga County, PA 2026 General Primary (May 19, 2026) — precinct results.

Source: Tioga County Official-Primary-Summary-Results-2026.pdf — pdftotext
-layout extract of Tioga's Electionware "Summary Results Report" (OFFICIAL
RESULTS, generated 06/11/2026 certifying run, 41 of 41 precincts reporting),
kept at "~/code/openelections-sources-pa/2026/primary/".  The source
concatenates the county "Election Summary" (30 pages, footers "Election
Summary - ... N of 30") and a per-municipality "Precinct Summary" report
(164 pages, footers "Precinct Summary - ... N of 164", 41 municipality
sections).  This parser runs in --precinct mode only and writes the
10-column precinct CSV (county, precinct, office, district, party,
candidate, votes, election_day, mail, provisional).

Usage:
    uv run python parsers/pa_tioga_primary_2026_results_parser.py \
        <input.pdf-or.txt> 2026/counties/20260519__pa__primary__tioga__precinct.csv
    uv run python parsers/pa_tioga_primary_2026_results_parser.py \
        --verify <input.pdf-or.txt> "<dos county csv>"

--verify cross-checks the parsed per-precinct named-candidate rows against
the PA DOS official certified precinct returns for Tioga ("Tioga County DOS
Official Precinct Returns 2026 Primary.csv", county code 59 — per-county
certified export, 14-column CSV: 0=county, 1=election ("2026 P"),
2=contest, 3=office_code, 4=contest district (0 for GOV/LTG, 15 USC,
68 STH), 5=party, 6=candidate (full name), 7=votes, 8=precinct_code,
9=municipality, 10=municipality_breakdown, 11-13 us_cd/state_sen/
state_house; 41 precincts x 9 contest rows, named candidates only —
no write-ins, no RV/BC rows, no vote breakdowns, no State Committee).
The statewide fixed-quote layout (0-indexed 2=county code, 3=precinct
code, 8=office code, 9=party, 11=last / 12=first / 13=middle, 15=vote
total, 22-24 name/qualifier/split) is accepted too.  The DOS export was
NOT used to produce the CSV — the county's own official report has the
write-ins, RV/BC rows and vote breakdowns the DOS export lacks — it only
validated the parse (every DOS row matched the parsed precinct value
exactly).

Structure identical to the Tioga 2022 primary source; parsing approach
copied from parsers/pa_tioga_primary_2022_results_parser.py (shared engine
parsers/pa_2023_summary_common.py used unmodified).  Quirks:

- Column layout: TOTAL, VOTE %, Election Day, Absentee/Mail-in,
  Provisional Ballots.  The VOTE % token is a "nnn.nn%" decimal that the
  shared engine's nums_from drops; a 0-vote row (no VOTE % printed)
  carries the same 3 remaining numbers.  votes, election_day, mail
  (combined absentee/mail-in), provisional.
- "Write-In: NAME" detail rows and "Not Assigned" fold into the
  "Write-In Totals" aggregate, which the source prints per precinct and
  already includes both; emitted as candidate "Write-In Totals" carrying
  the contest's party (party-empty write-ins fail duplicate_entries in a
  primary; the aggregate is the source's own single totals row).
- Contest headers carry the ballot party as a prefix ("DEM GOVERNOR",
  "REP REP COMM MAN Bloss Township"; same source typo as 2022:
  "DEM DEMCOMM Sullivan Township").  Office mapping:
    GOVERNOR                          -> Governor
    LIEUTENANT GOVERNOR               -> Lieutenant Governor
    REP IN CONGRESS 15TH DIS          -> U.S. House | 15
    REP IN THE GEN ASSEM 68TH DIS     -> State House | 68
    MEMBER DEM STATE COMM 59TH DIST / MEMBER OF THE REPUBLICAN STATE
      COMMITTEE                       -> "Member of Democratic State
      Committee" / "Member of Republican State Committee", district ""
      (matches this county's 2026 county-level file, where the statewide
      anchor writes the district as "Tioga County (District 59)")
    DEM COMM <muni> (source typo "DEMCOMM <muni>")
      -> "Democratic County Committee <Muni>", district ""
    REP COMM MAN/WOMAN <muni>         -> "Republican County Committeeman /
      Committeewoman <Muni>", district "" (the Tioga 2022 primary
      precinct file's names, which keep the source's man/woman seat
      distinction and thus keep MAN and WOMAN totals separate)
  Committee contests are write-in-only primaries: their totals rows are
  all-write-in, carried for completeness as in the Tioga 2022 primary
  file (and as other 2026 counties - Fulton/Greene/Huntingdon - report
  their county committee contests).
- Registered Voters / Ballots Cast / Ballots Cast - Blank per
  municipality are emitted from the section's Statistics block; per-party
  statistics and Voter Turnout lines are not emitted (repo convention
  records only totals).
- Municipality section headers repeat every page top; only the
  stat-block-backed occurrence opens the section.  "Wellsboro Boro Ward
  1/2" headers carry a digit, so they are caught by a regex before the
  digit-free municipality test.  Names follow the county's own printed
  section headers (kept as printed, as in the 2024/2025 Tioga precinct
  files): "Wellsboro Boro Ward 1", "Delmar Township North/South", "Liberty
  Township" (DOS precinct code 220) before "Liberty Borough" (230), same
  for Tioga (350/360) and Westfield (420/440) - township precedes borough
  in the county's precinct numbering, as in its 2024/2025 general files.
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

PER_PARTY_STATS_RE = re.compile(
    r"^(Registered Voters|Ballots Cast) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN|GREEN|LIBERTARIAN)\b", re.I)
TURNOUT_RE = re.compile(r"^Voter Turnout - ", re.I)
STATS_RE = re.compile(r"^Statistics\b", re.I)

# column-header wrap lines that repeat at page tops inside contest blocks
# ("TOTAL    VOTE %   Election Absentee Provision / Day /Mail-in al /
# Ballots") and page-top report headers that is_junk misses; the standalone
# "VOTE %" token keeps these apart from "Total Votes Cast ..." data rows
COLHDR_RE = re.compile(r"^(TOTAL\s+VOTE\s+%|Day\s*/\s*Mail-in.*|Ballots\s*$)",
                       re.I)

EXACT_OFFICES = {
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "REP IN CONGRESS 15TH DIS": ("U.S. House", "15"),
    "REP IN THE GEN ASSEM 68TH DIS": ("State House", "68"),
    "MEMBER DEM STATE COMM 59TH DIST":
        ("Member of Democratic State Committee", ""),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", ""),
}

COMMITTEE_RE = re.compile(r"^(DEM COMM|DEMCOMM|REP COMM MAN|REP COMM WOMAN)"
                          r"\s+(.+)$", re.I)

WELLSBORO_WARD_RE = re.compile(r"^(Wellsboro) (?:Boro )?Ward (\d)$", re.I)


def muni(raw):
    """Normalize a Tioga municipality name to the county's printed section
    header spellings (the 2024/2025 precinct files' names)."""
    s = " ".join(raw.strip().split())
    s = re.sub(r"\btownsip\b", "Township", s, flags=re.I)
    s = re.sub(r"\btownship\b", "Township", s, flags=re.I)
    w = WELLSBORO_WARD_RE.match(s)
    if w:
        return f"Wellsboro Boro Ward {w.group(2)}"
    s = re.sub(r"\bBoro(?![a-z])", "Borough", s)  # "Lawrenceville Boro" typo
    # truncated ward/delmar tails -> the printed section-header spellings
    s = re.sub(r"\bDelmar Township N$", "Delmar Township North", s)
    s = re.sub(r"\bDelmar Township S$", "Delmar Township South", s)
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
        if PER_PARTY_STATS_RE.match(s) or TURNOUT_RE.match(s) \
                or COLHDR_RE.match(s):
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
            # municipality section header (digit-free, followed within a
            # few lines by the section's Statistics block) — or a page-top
            # repeat of the municipality name (followed by the page's next
            # contest block); only the Statistics-backed occurrence opens
            # the section
            nxt = [sec[k].strip() for k in range(i + 1, min(i + 5, len(sec)))
                   if sec[k].strip()]
            if nxt and STATS_RE.match(nxt[0]):
                precinct = muni(s)
                current = None
            elif current is None or not nxt or not (
                    VOTEFOR_RE.match(nxt[0])
                    or PARTY_PREFIX_RE.match(nxt[0])):
                raise SystemExit(
                    f"unexpected digit-free line inside contest block: "
                    f"{s!r}")
            continue
        if current is None:
            continue
        p_c, office, district, party, vfn = current
        key = (p_c, office, district, party)
        n = nums_from(s.split())
        if s.upper().startswith("WRITE-IN TOTALS"):
            if n:
                rows.append([COUNTY, p_c, office, district, party,
                             "Write-In Totals", int(n[0]),
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
    return rows


# --------------------------------------------------------------------------
# --verify: cross-check parsed rows against the DOS certified returns CSV
# --------------------------------------------------------------------------

# DOS precinct code -> parsed precinct name (municipality precedes borough
# in the county's numbering; see module docstring).
DOS_PRECINCT_NAMES = {
    "10": "Bloss Township", "20": "Blossburg Borough",
    "30": "Brookfield Township", "40": "Charleston Township",
    "60": "Chatham Township", "70": "Clymer Township",
    "80": "Covington Township", "90": "Deerfield Township",
    "100": "Delmar Township North", "110": "Delmar Township South",
    "120": "Duncan Township", "130": "Elk Township",
    "140": "Elkland Borough", "150": "Farmington Township",
    "160": "Gaines Township", "170": "Hamilton Township",
    "180": "Jackson Township", "190": "Knoxville Borough",
    "200": "Lawrence Township", "210": "Lawrenceville Borough",
    "220": "Liberty Township", "230": "Liberty Borough",
    "240": "Mansfield Borough", "250": "Middlebury Township",
    "260": "Morris Township", "270": "Nelson Township",
    "280": "Osceola Township", "290": "Putnam Township",
    "300": "Richmond Township", "310": "Roseville Borough",
    "320": "Rutland Township", "330": "Shippen Township",
    "340": "Sullivan Township", "350": "Tioga Township",
    "360": "Tioga Borough", "370": "Union Township",
    "390": "Ward Township", "400": "Wellsboro Boro Ward 1",
    "410": "Wellsboro Boro Ward 2", "420": "Westfield Township",
    "440": "Westfield Borough",
}

COUNTY_CODE = "59"
ELECTION_TYPE = "P"
YEAR = "2026"

OFFICES = {
    "GOV": "Governor", "LTG": "Lieutenant Governor", "USC": "U.S. House",
    "STS": "State Senate", "STH": "State House",
}
OFFICE_CODES = ("GOV", "LTG", "USC", "STS", "STH")


def dos_precinct_rows(path):
    """DOS rows per precinct: (precinct, office, party, candidate, votes).

    Handles the per-county certified export ("Tioga County DOS Official
    Precinct Returns 2026 Primary.csv", header row
    county,election,contest,office_code,district,party,candidate,votes,
    precinct_code,municipality,...,with candidate as a full name column) and
    the statewide fixed-quote layout of the work2026p/dos split files
    (11=last, 12=first, 15=votes)."""
    out = []
    with open(path, newline="", encoding="latin-1") as fh:
        rd = csv.reader(fh)
        hdr = next(rd, None)
        if hdr is not None and hdr and hdr[0] == "county":
            for row in rd:
                if not row or row[0] != "Tioga":
                    continue
                code = row[8].strip()
                if code not in DOS_PRECINCT_NAMES:
                    raise SystemExit(f"unknown DOS precinct code {code!r}")
                out.append([DOS_PRECINCT_NAMES[code],
                            _office_from_contest(row[2]), row[5].strip(),
                            row[6].strip(), int(row[7])])
        else:
            for row in rd:
                if len(row) < 23 or row[0] != YEAR or row[1] != ELECTION_TYPE \
                        or row[2] != COUNTY_CODE:
                    continue
                if row[8] not in OFFICE_CODES:
                    raise SystemExit(f"unknown DOS office {row[8]!r}")
                name = " ".join((row[12].title(), row[11].title()))
                out.append([DOS_PRECINCT_NAMES[row[3]], OFFICES[row[8]],
                            row[9], name, int(row[15])])
    return out


def _office_from_contest(contest):
    c = contest.strip()
    if c.startswith("Governor"):
        return "Governor"
    if c.startswith("Lieutenant Governor"):
        return "Lieutenant Governor"
    if c.startswith("Representative in Congress"):
        return "U.S. House"
    if c.startswith("Representative in the General Assembly"):
        return "State House"
    raise SystemExit(f"unknown contest in DOS CSV: {contest!r}")


def verify(parsed_rows, dos_path):
    """Every DOS named-candidate row must match a parsed precinct/office/
    party/candidate row exactly (the DOS export carries only the 9 named
    candidate contests, so it cannot check write-ins/RV/BC).  DOS names and
    the county report's printed names differ (JOSHUA/JOSH, GLENN WILLIAM JR/
    Glenn GT Thompson, CLINTON/Clint), so matching is per last name within
    each (precinct, office, party) contest row."""
    parsed = {}   # (precinct, office, party-upper, lastname) -> votes
    for r in parsed_rows:
        if r[2] in ("Registered Voters", "Ballots Cast",
                    "Ballots Cast - Blank"):
            continue
        if r[5].upper().startswith("WRITE-IN"):
            continue
        last = re.sub("[^a-z]", "", r[5].split()[-1].lower())
        key = (r[1], r[2], r[4].upper(), last)
        parsed[key] = parsed.get(key, 0) + int(r[6])
    dos = dos_precinct_rows(dos_path)
    mismatches = []
    for precinct, office, party, cand, votes in dos:
        last = cand.split()[-1].lower()
        key = (precinct, office, party.strip().upper(),
               re.sub("[^a-z]", "", last))
        got = parsed.get(key)
        if got is None:
            mismatches.append((precinct, office, party, cand, votes, got,
                               "row not found"))
        elif int(got) != votes:
            mismatches.append((precinct, office, party, cand, votes, got,
                               "value mismatch"))
    hits = len(dos) - len(mismatches)
    print(f"DOS cross-check: {hits}/{len(dos)} DOS rows matched exactly "
          f"({len(mismatches)} mismatches)")
    for m in mismatches[:20]:
        print("  MISMATCH", m)
    return len(mismatches)


def main():
    args = [a for a in sys.argv[1:] if a != "--verify"]
    do_verify = "--verify" in sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} [--verify] <input.pdf|.txt> "
                 f"<output.csv> [dos_csv]")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    rows = parse_precinct(src, dst)
    if do_verify:
        dos_path = args[2] if len(args) > 2 else None
        if dos_path:
            verify(rows, dos_path)


if __name__ == "__main__":
    main()
