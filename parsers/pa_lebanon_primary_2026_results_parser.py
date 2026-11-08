#!/usr/bin/env python3
"""
Parse Lebanon County PA 2026 General Primary (May 19, 2026) results.

Sources (~/code/openelections-sources-pa/2026/primary/):
  * "Lebanon County 2026 Primary Precinct Level Summary Report.pdf" --
    Electionware "Precinct Summary Results Report" ("Lebanon County Official
    Results", generated 05/29/2026 2:43 PM), 240 pages, per-precinct
    sections (60 precincts).
  * "Lebanon County 2026 Primary County Summary Results Report.pdf" --
    Electionware "Summary Results Report" ("Lebanon County Unofficial
    Results", generated 05/19/2026 10:06 PM), 17 pages, countywide
    sections.
  * PA DOS certified per-precinct returns slice
    (work2026p/dos/lebanon__dos_precinctreturns.txt, county code 38) --
    used below for the ballot-contest family rows (see "Certified
    sourcing").
  * Statewide countywide anchor 2026/20260519__pa__primary__county.csv --
    used below for the county CSV's named-candidate rows (ditto).

Report shape (as the Lebanon 2023 primary source, see
parsers/pa_lebanon_primary_2023_results_parser.py): each section opens
with a Statistics block

    Ballots Cast - Total / - DEMOCRATIC / - REPUBLICAN / - NONPARTISAN /
    Ballots Cast - Blank
    Registered Voters - Total / - DEMOCRATIC / - REPUBLICAN / - NONPARTISAN

followed by contest sections

      DEM GOVERNOR
      Vote For 1
    <column-header junk>
    JOSH SHAPIRO                                37   20   17   0
    Write-In Totals                              1    1    0    0
      Not Assigned                               1    1    0    0
      Overvotes                                  0    0    0    0
      Undervotes                                 1    1    0    0

2026 differences from 2023 (handled here):

  * Contests are the gubernatorial-primary slate (Governor, Lieutenant
    Governor, U.S. House, State Senate, State House) plus the party
    State/County Committee races.  The Lebanon report prints no district
    on "REPRESENTATIVE IN CONGRESS" or "SENATOR IN THE GENERAL ASSEMBLY":
    Lebanon lies entirely within CD 9 and SD 48, confirmed against the PA
    DOS certified per-precinct returns (work2026p/dos/
    lebanon__dos_precinctreturns.txt: office codes USC=9, STS=48 for every
    Lebanon row) and the statewide anchor.  Those districts are hardcoded
    and no other district values are accepted.
  * "REPRESENTATIVE IN THE GENERAL ASSEMBLY 101st State Legislative"
    carries the House district; office "State House", district "101".
  * "MEMBER OF <PARTY> STATE COMMITTEE" -> office "Member of Democratic
    State Committee" / "Member of Republican State Committee" (matches
    the statewide anchor's office spelling), district "".
  * "MEMBER OF DEMOCRATIC COUNTY COMMITTEE <precinct label>" is printed
    per precinct WITH the precinct label appended to the contest header;
    the label is stripped (cross-checked against the current precinct)
    and the rows become office "Member of Democratic County Committee",
    district "".  (Lebanon has no Republican county committee race on
    this ballot.)  These rows are emitted in the precinct CSV and
    OMITTED from the county CSV, matching parsers/pa_berks_general_2025
    _results_parser.py (also Electionware) and the other 2026 primary
    county CSVs.
  * Registered Voters / Ballots Cast statistics are printed this year
    (RV totals are 0 in both reports).  "Registered Voters", "Ballots
    Cast" and "Ballots Cast - Blank" total rows are emitted in the
    precinct CSV; per-party RV/BC rows are kept in memory for the
    party-ballot arithmetic check only, matching the repo convention of
    recording only totals.  The county CSV omits RV/BC rows to match the
    other 2026 primary county files (e.g. Berks).
  * "Not Assigned", "Overvotes" and "Undervotes" rows fold into
    validation only and are NOT emitted, matching the Berks/Chester/
    Lancaster 2026 primary files; "Write-In: NAME" detail rows likewise
    fold into the printed "Write-In Totals" row, which carries the
    CONTEST's party (party-empty write-ins are a duplicate_entries
    hazard in primaries).  Their arithmetic is validated.

Vote breakdown: the Electionware 4-integer tail is (total, Election Day,
Mail/Absentee, Provisional).  The precinct CSV maps it to election_day /
absentee (mail value) / provisional -- the 2026 primary precinct-file
convention (Berks, Chester, Lancaster, Butler...).  The county CSV maps
it to election_day / mail / provisional, matching the other 2026 primary
county CSVs.

Certified sourcing (both files): BOTH Electionware reports predate the
canvass adjustments -- the precinct report's Provisional column is 0
throughout and its per-contest totals trail the certified statewide
values by +1..+4 votes per precinct (226 votes countywide over the 610
ballot-contest candidate cells; the 05/29 "Official" run was generated
before the provisional/absentee adjudication was folded in).  The
certified DOS per-precinct returns, on the other hand, match the
statewide anchor exactly for Lebanon (every office/party/candidate).
So:

  * precinct CSV: named-candidate rows for the five ballot-contest
    families (Governor, Lieutenant Governor, U.S. House, State Senate,
    State House) are taken from the DOS certified slice -- certified
    votes, breakdown columns left empty (DOS exports no breakdowns;
    pa_adams_primary_2026 precedent).  Write-In Totals rows, Member of
    Dem/Rep State Committee, Member of Democratic County Committee and
    the Registered Voters / Ballots Cast / Ballots Cast - Blank rows
    exist in no precinct-level certified source and keep the report's
    (source-printed) values and Election Day / Mail-Absentee splits;
    their Provisional cells are printed 0 by the source and stay 0.
    The report-vs-DOS per-precinct comparison behind this split lives in
    work2026p/lebanon/compare_dos.py + NOTES.md.
  * county CSV: the 21 named-candidate rows the anchor carries (the five
    ballot families + both State Committees) are taken from the anchor
    (certified votes + ED/mail/provisional); Write-In Totals rows come
    from the county summary report.  Net effect: every countywide
    named-candidate sum equals the anchor exactly; the write-in rows are
    the unofficial 10:06-PM election-night values (certified write-in
    totals are not published at any level for Lebanon).

DOS fallback for the ballot families is used even though the county's
own reports cover every contest, because those reports' values
disagree with the certified totals (they trail them); work2026p/dos/
usage is documented here per the AGENT_BRIEF.md rule.

Usage (both deliverables):

  * precinct CSV:
    python parsers/pa_lebanon_primary_2026_results_parser.py \\
        "Lebanon County 2026 Primary Precinct Level Summary Report.pdf" \\
        2026/counties/20260519__pa__primary__lebanon__precinct.csv
  * county CSV (from the countywide summary report):
    python parsers/pa_lebanon_primary_2026_results_parser.py \\
        --county-source \\
        "Lebanon County 2026 Primary County Summary Results Report.pdf" \\
        2026/counties/20260519__pa__primary__lebanon__county.csv

Pass --dos <path> / --anchor <path> to override the certified-source
locations, --no-dos / --no-anchor to fall back to the raw Electionware
values (verification-only mode; the deliverables are always produced
with both).

No DOS fallback is needed for contest COVERAGE: both reports cover every
Lebanon contest (Governor, Lieutenant Governor, U.S. House 9, State
Senate 48, State House 98/101/102, State Committee, County Committee)
for all 60 precincts.  Known residual: Member of Dem/Rep State Committee
precinct sums stay 15-25 below the anchor (the report vintage lacks each
precinct's certified STC adjustments and DOS exports no State Committee
rows; no precinct-level certified source for these offices exists).
"""

import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

COUNTY = "Lebanon"

PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "provisional",
                       "absentee"]
COUNTY_FIELDNAMES = ["county", "office", "district", "party", "candidate",
                     "votes", "election_day", "mail", "provisional"]

VALS_RE = re.compile(r"^(.*?)\s+((?:\d[\d,]*)(?:\s+\d[\d,]*)*)$")
VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.IGNORECASE)
CONTEST_HEADER_RE = re.compile(r"^(DEM|REP)\s+(.+)$")
ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)\b", re.IGNORECASE)
PARTY_STATS = ("DEMOCRATIC", "REPUBLICAN", "NONPARTISAN")

# US House / State Senate districts: Lebanon County lies entirely within
# CD 9 and SD 48 (verified against the DOS certified per-precinct returns
# for all 60 Lebanon precincts and the statewide anchor CSV).
US_HOUSE_DISTRICT = "9"
STATE_SENATE_DISTRICT = "48"

# Offices whose per-precinct named-candidate rows are certified-DOS
# sourced (see "Certified sourcing" in the docstring).  Lebanon's 05/29
# precinct report trails the certified totals by +1..+4 votes per
# precinct-contest (provisional/canvass adjustments not in the report).
BALLOT_OFFICES = ("Governor", "Lieutenant Governor", "U.S. House",
                  "State Senate", "State House")

_DOS_SLICE_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "work2026p", "dos", "lebanon__dos_precinctreturns.txt")
_ANCHOR_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "2026", "20260519__pa__primary__county.csv")

_DOS_OFFICES = {"GOV": "Governor", "LTG": "Lieutenant Governor",
                "USC": "U.S. House", "STS": "State Senate",
                "STH": "State House"}

# The Electionware precinct labels encode ward/split in a prefix token
# ("22S Heidelberg Schaefferstown") while the DOS names spell the split
# out ("HEIDELBERG X SCH."); short DOS split words get a pin here
# (normalized first-4 chars: "sch" DOS -> "scha" report).
_DOS_SPLIT_PIN = {"sch": "scha"}

PAGE_JUNK_RE = re.compile(
    r"^(Precinct Summary Results Report\b"
    r"|Summary Results Report\b"
    r"|Lebanon County (?:Un)?official Results\b"
    r"|2026l? Primary\b"
    r"|2026 Primary Election\b"
    r"|May 19, 2026\b"
    r"|Precinct Summary - \S"
    r"|Election Summary - \S"
    r"|Report generated with Electionware\b"
    r"|Election Day Precincts Reporting\b"
    r"|Precincts (?:Complete|Partially Reported)\b"
    r"|Absentee/ Early Precincts Reporting\b"
    r"|Voter Turnout\b"
    r"|Statistics\b"
    r"|TOTAL\b"
    r"|Day In/Absent|ee$|al$)")


def _title(name):
    """Title-case an ALL-CAPS candidate name ("JOSH SHAPIRO" ->
    "Josh Shapiro")."""
    out = []
    for w in name.split():
        if w.isupper():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def map_contest(header, party):
    """(office, district) for a contest-header tail with the DEM/REP
    prefix stripped; ``party`` is DEM/REP.  Raises on unrecognized
    headers."""
    h = re.sub(r"\s+", " ", header).strip()
    hu = h.upper()

    if hu == "GOVERNOR":
        return ("Governor", "")
    if hu == "LIEUTENANT GOVERNOR":
        return ("Lieutenant Governor", "")
    if hu.startswith("REPRESENTATIVE IN CONGRESS"):
        return ("U.S. House", US_HOUSE_DISTRICT)
    if hu == "SENATOR IN THE GENERAL ASSEMBLY":
        return ("State Senate", STATE_SENATE_DISTRICT)
    if hu.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        rest = h[len("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):].strip()
        m = ORDINAL_RE.match(rest)
        if not m:
            raise SystemExit(f"State House header without district: {h!r}")
        return ("State House", m.group(1))
    if hu == "MEMBER OF DEMOCRATIC STATE COMMITTEE" and party == "DEM":
        return ("Member of Democratic State Committee", "")
    if hu == "MEMBER OF REPUBLICAN STATE COMMITTEE" and party == "REP":
        return ("Member of Republican State Committee", "")
    if hu.startswith("MEMBER OF DEMOCRATIC COUNTY COMMITTEE"):
        return ("Member of Democratic County Committee", "")
    raise SystemExit(f"unrecognized contest header: {header!r}")


def load_text(input_path):
    if input_path.lower().endswith(".pdf"):
        tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp = tf.name
        tf.close()
        try:
            subprocess.run(["pdftotext", "-layout", input_path, tmp],
                           check=True)
            with open(tmp, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    with open(input_path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse(text, county_source=False):
    """Return (rows, warnings, checks).

    county_source=False: per-precinct 10-column rows from the precinct
    report.  True: single-county 9-column rows from the countywide
    summary report.
    """
    lines = [l.strip("\x0c") for l in text.splitlines()]
    rows = []
    warnings = []

    def warn(msg):
        warnings.append(msg)

    precinct = None
    contest = None      # (party, office, district, vote_for)

    # pre-mark contest-header lines: a DEM/REP line whose next nonblank
    # line is "Vote For N"
    contest_header_idx = set()
    for i, l in enumerate(lines):
        s = l.strip()
        if not re.match(r"^(DEM|REP)\b", s):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and VOTEFOR_RE.match(lines[j].strip()):
            contest_header_idx.add(i)

    # pre-compute the precinct for each line: the precinct name is the
    # nonblank line right before the "Statistics" column-header line.
    prec_of = {}
    label = None
    for i, l in enumerate(lines):
        if re.match(r"^Statistics\b", l.strip()):
            label = None
            for j in range(i - 1, max(i - 8, -1), -1):
                t = lines[j].strip()
                if t and "TOTAL" not in t and not PAGE_JUNK_RE.match(t) \
                        and not t.startswith("Ballots") \
                        and not t.startswith("Registered Voters"):
                    label = t
                    break
        prec_of[i] = label
    precinct_names = {v for v in prec_of.values() if v is not None}

    detail = defaultdict(int)     # (prec, party, office, district) -> sum
    notassigned = defaultdict(int)
    writetot = defaultdict(int)
    contest_sum = defaultdict(int)
    vfn_by = {}                   # (prec, party, office, district) -> vfn
    stats = {}                    # precinct -> {"DEMOCRATIC": [...], ...}

    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue

        if re.match(r"^Statistics\b", s):
            # a Statistics block always opens a (new) precinct section
            if county_source:
                precinct = COUNTY
            else:
                p = prec_of[i]
                if p is None:
                    raise SystemExit(
                        f"statistics block with no precinct at line {i + 1}")
                precinct = p
            continue

        if PAGE_JUNK_RE.match(s):
            continue

        if i in contest_header_idx:
            m = CONTEST_HEADER_RE.match(s)
            party, header = m.group(1).upper(), m.group(2)
            vf = lines[i + 1].strip() if i + 1 < len(lines) else ""
            vfm = VOTEFOR_RE.match(vf)
            office, district = map_contest(header, party)
            if office == "Member of Democratic County Committee":
                tail = re.split(r"COMMITTEE", header, 1)[1].strip() \
                    if "COMMITTEE" in header else ""
                if not county_source and precinct is not None \
                        and tail != precinct:
                    warn(f"county-committee header label {tail!r} != "
                         f"precinct {precinct!r}")
                if county_source:
                    # per-precinct sections would collide once the
                    # precinct column is dropped; omit from the county CSV
                    contest = ("SKIP", "", "", 0)
                    continue
            contest = (party, office, district, int(vfm.group(1)))
            vfn_by[(precinct, party, office, district)] = contest[3]
            continue

        if VOTEFOR_RE.match(s):
            continue  # consumed by the header detection above

        if not county_source and s in precinct_names:
            continue  # precinct label at the top of a page

        if s.startswith(("Registered Voters", "Ballots Cast")):
            m = VALS_RE.match(s)
            if not m:
                warn(f"{precinct}: no numbers on statistics row: {s!r}")
                continue
            head = m.group(1).strip()
            vals = [int(v.replace(",", "")) for v in m.group(2).split()]
            if head.startswith("Registered Voters - "):
                which = head[len("Registered Voters - "):]
                if which != "Total":
                    if not re.match(r"^(?:%s)$" % "|".join(PARTY_STATS),
                                    which):
                        warn(f"{precinct}: unrecognized RV row: {s!r}")
                    continue  # per-party RV kept out of the file
                if not county_source:
                    rows.append([COUNTY, precinct, "Registered Voters", "",
                                 "", "", str(vals[0]), "", "", ""])
            else:                      # Ballots Cast - ...
                which = head[len("Ballots Cast - "):]
                if which in PARTY_STATS:
                    stats.setdefault(precinct, {})[which] = vals
                elif which in ("Total", "Blank"):
                    if not county_source:
                        tail = [str(v) for v in vals] + [""] * 4
                        office = ("Ballots Cast" if which == "Total"
                                  else "Ballots Cast - Blank")
                        rows.append([COUNTY, precinct, office, "", "", "",
                                     tail[0],    # total
                                     tail[1],    # election day
                                     tail[3],    # provisional
                                     tail[2]])   # mail/absentee
                else:
                    warn(f"{precinct}: unrecognized Ballots Cast row: {s!r}")
            continue

        if contest is None or contest[0] == "SKIP":
            # SKIP: county-committee sections in county-source mode
            if contest is None and VALS_RE.match(s):
                warn(f"{precinct}: data row outside any contest: "
                     f"{s[:70]!r}")
            continue
        if not VALS_RE.match(s):
            if not any(c.isdigit() for c in s):
                # wrapped candidate-name fragments ("  MOYER") and
                # column-header remnants
                continue
            warn(f"{precinct}: unrecognized row in contest {contest}: "
                 f"{s[:70]!r}")
            continue

        m = VALS_RE.match(s)
        head = m.group(1).strip()
        vals = [int(v.replace(",", "")) for v in m.group(2).split()]
        if len(vals) != 4:
            warn(f"{precinct}: row without 4 values: {s[:70]!r}")
            continue
        k = (precinct, contest[0], contest[1], contest[2])
        if head == "Write-In Totals":
            rows.append(_row(county_source, precinct, contest, "Write-In "
                             "Totals", vals))
            writetot[k] += vals[0]
        elif head.startswith("Write-In:"):
            detail[k] += vals[0]
            continue  # folded into Write-In Totals
        elif head == "Not Assigned":
            notassigned[k] += vals[0]
            continue  # folded into Write-In Totals
        elif head in ("Overvotes", "Undervotes"):
            continue  # validated below; not emitted (Berks 2026 style)
        else:
            rows.append(_row(county_source, precinct, contest, _title(head),
                             vals))
        contest_sum[k] += vals[0]

    # ---- write-in arithmetic: details + Not Assigned == Write-In Totals --
    writein_bad = []
    for k in sorted(set(writetot) | set(detail) | set(notassigned)):
        wt = writetot.get(k, 0)
        parts = detail.get(k, 0)
        if k in detail and parts + notassigned.get(k, 0) != wt:
            writein_bad.append((k, "details", parts, "+ not assigned",
                                notassigned.get(k, 0), "!=", wt))
    for k, wt in sorted(writetot.items()):
        if k not in detail and notassigned.get(k, 0) != wt:
            writein_bad.append((k, "not assigned", notassigned.get(k, 0),
                                "!=", "totals", wt))

    # ---- party-ballot arithmetic: per contest, named+write-in rows
    # <= Vote For N x that party's Ballots Cast for the precinct ----
    party_bad = _party_check(not county_source, contest_sum, vfn_by, stats,
                             warn)
    return rows, warnings, {
        "writein_bad": writein_bad, "party_bad": party_bad,
        "contest_sum": dict(contest_sum),
    }


def _party_check(is_precinct, contest_sum, vfn_by, stats, warn):
    bad = []
    if is_precinct:
        per = defaultdict(int)
        for (p, party, office, district), v in contest_sum.items():
            per[(p, party, office, district)] += v
        for (p, party, office, district), v in per.items():
            if "County Committee" in office:
                continue
            vfn = vfn_by.get((p, party, office, district))
            if vfn is None:
                warn(f"{p}: no Vote For recorded for {office}")
                continue
            b = stats.get(p, {}).get(
                "DEMOCRATIC" if party == "DEM" else "REPUBLICAN")
            if b is None:
                warn(f"{p}: no Ballots Cast - {party} statistics row")
                continue
            if v > vfn * b[0]:
                bad.append((p, party, office, district, vfn, v, b[0]))
    else:
        per = defaultdict(int)
        for (p, party, office, district), v in contest_sum.items():
            per[(party, office, district)] += v
        for (party, office, district), v in per.items():
            if "County Committee" in office:
                continue
            vfn = next(vf for key, vf in vfn_by.items()
                       if key[1:] == (party, office, district))
            b = stats.get(COUNTY, {}).get(
                "DEMOCRATIC" if party == "DEM" else "REPUBLICAN")
            if b is None:
                continue
            if v > vfn * b[0]:
                bad.append((party, office, district, vfn, v, b[0]))
    return bad


def _row(county_source, precinct, contest, candidate, vals):
    """Build one output row in this file's column order."""
    party, office, district, _ = contest
    vals = (vals + [None] * 4)[:4]
    total, ed, mail, prov = (str(v) for v in vals)
    if county_source:
        return [COUNTY, office, district, party, candidate, total, ed, mail,
                prov]
    return [COUNTY, precinct, office, district, party, candidate, total, ed,
            prov, mail]


# ---- certified-source loading --------------------------------------------

def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower()).replace("mount", "mt")


def _split4(s):
    n = _norm(s)
    if not n:
        return ""
    n = _DOS_SPLIT_PIN.get(n[:3], n)
    return n[:4]


def _dos_key(name):
    """Precinct join key from a DOS precinct name ("LEBANON W 1 X EAST",
    "ANNVILLE X EAST", "HEIDELBERG X SCH.", "CLEONA"): (municipality,
    ward number for Lebanon wards only, split prefix)."""
    toks = name.split()
    qi = next((i for i, t in enumerate(toks)
               if i and t in ("X", "W", "D")), None)
    if qi is None:
        muni, ward, sp = name, None, ""
    elif toks[qi] in ("W", "D"):
        muni = " ".join(toks[:qi])
        ward = int(re.sub(r"\D", "", toks[qi + 1]))
        rest = toks[qi + 2:]          # skip the ward token
        if rest and re.fullmatch(r"X", rest[0], re.I):
            rest = rest[1:]
        sp = " ".join(rest)
    else:
        muni, ward, sp = " ".join(toks[:qi]), None, " ".join(toks[qi + 1:])
    km = _norm(muni)
    return (km, ward if km == "lebanon" else None, _split4(sp))


def _report_key(precinct):
    """Precinct join key from the report's own label ("01E Lebanon 1st
    Ward East", "22S Heidelberg Schaefferstown", "25 North Annville")."""
    pm = re.match(r"^(\d+)([A-Za-z]{1,2})?\s+(.+)$", precinct.strip())
    if not pm:
        raise SystemExit(f"unrecognized precinct label: {precinct!r}")
    digits, letter, rest = pm.group(1), pm.group(2) or "", pm.group(3)
    rest = re.sub(r"\s+\d+(?:st|nd|rd|th)\s+ward\b", "", rest, flags=re.I)
    words = rest.split()
    sp = ""
    if letter:
        # the split is the shortest trailing run of words starting with
        # the prefix letter ("North Lebanon East" -> East; "Mount Zion"
        # under 19M -> Mount Zion); the longer candidate is always the
        # municipality itself (only its FIRST word can match).
        for i in range(len(words), 0, -1):
            tail = _norm(" ".join(words[i - 1:]))
            if tail[:1] == letter[0].lower():
                sp = " ".join(words[i - 1:])
                words = words[:i - 1]
                break
    km = _norm(" ".join(words))
    return (km, int(digits) if km == "lebanon" else None, _split4(sp))


def _load_dos(dos_path):
    """{join_key: } -> {key: votes} for (key, lastname, party, office)."""
    votes = {}
    prec_n = 0
    seen_prec = set()
    with open(dos_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().rstrip(",")
            if not line:
                continue
            p = next(csv.reader([line]))
            office = _DOS_OFFICES.get(p[8])
            if office is None:
                raise SystemExit(f"unrecognized DOS office code: {p[8]!r}")
            k = _dos_key(p[22].strip())
            if k not in seen_prec:
                seen_prec.add(k)
                prec_n += 1
            votes[(k, _dos_lastname(p[11]), p[9].lower(), office)] = \
                int(p[15] or 0) if p[15].strip() else 0
    return votes, prec_n


def _dos_lastname(dos_name):
    """DOS candidate "HYDER-DARLINGTON" -> "darlington" (the anchor/report
    print "HUGH DARLINGTON")."""
    parts = re.split(r"[\s\-]+", dos_name.strip())
    return re.sub(r"\W", "", parts[-1]).lower() if parts and parts[-1] else "?"


def _apply_dos(rows, dos_path):
    """Replace the ballot-family named rows' values with the certified DOS
    per-precinct votes (breakdowns emptied)."""
    dos, n_dos_prec = _load_dos(dos_path)
    labels = {r[1] for r in rows}
    keys = {}
    for lbl in labels:
        k = _report_key(lbl)
        if k in keys:
            raise SystemExit(f"precinct join key collision: {lbl} / {keys[k]}")
        keys[lbl] = k
    if len(keys) != n_dos_prec:
        raise SystemExit(f"precinct count mismatch: {len(keys)} report vs "
                         f"{n_dos_prec} DOS")
    unmatched = set(dos)
    n_replaced = 0
    deltas = 0
    for row in rows:
        _, prec, office, district, party, cand, votes, ed, prov, ab = row
        if (office not in BALLOT_OFFICES or cand in ("Write-In Totals",)
                or not cand):
            continue
        k = keys[prec]
        dk = (k, _dos_lastname(cand), party.lower(), office)
        if dk not in dos:
            raise SystemExit(f"no DOS row for {prec} {office} {party} {cand}")
        v = dos.pop(dk)
        unmatched.discard(dk)
        if v != int(votes):
            deltas += v - int(votes)
        row[6], row[7], row[8], row[9] = str(v), "", "", ""
        n_replaced += 1
    if unmatched:
        raise SystemExit(f"{len(unmatched)} DOS rows not represented in the "
                         f"report, e.g. {sorted(unmatched)[0]}")
    print(f"certified DOS: replaced {n_replaced} named rows (net "
          f"+{deltas} votes over the report's election-night values); "
          f"{n_dos_prec} DOS precincts")
    return n_replaced, deltas


def _load_anchor(anchor_path):
    """{(office, district, party-dem/rep, lastname): (votes, ed, mail, prov)}
    from the statewide countywide anchor, Lebanon rows only."""
    out = {}
    with open(anchor_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["county"].strip().upper() != COUNTY.upper():
                continue
            party = r["party"].strip().lower()
            party = ("dem" if party.startswith("dem")
                     else "rep" if party.startswith("rep") else party)
            k = (r["office"].strip(), r["district"].strip(), party,
                 _dos_lastname(r["candidate"]))
            out[k] = (r["votes"].strip(), r["election_day"].strip(),
                      r["mail"].strip(), r["provisional"].strip())
    if not out:
        raise SystemExit(f"no {COUNTY} rows in anchor {anchor_path}")
    return out


def _apply_anchor(rows, anchor_path):
    """Replace countywide named rows' values with the certified anchor
    values (votes + ED/mail/provisional)."""
    anchor = _load_anchor(anchor_path)
    n_replaced = 0
    unmatched = []
    for row in rows:
        _, office, district, party, cand, *_ = row
        if cand in ("Write-In Totals",) or not cand:
            continue
        k = (office, district, party.lower(),
             _dos_lastname(cand))
        if k not in anchor:
            unmatched.append((office, party, cand))
            continue
        v, edv, mv, pv = anchor.pop(k)
        row[5], row[6], row[7], row[8] = v, edv, mv, pv
        n_replaced += 1
    if unmatched:
        # summary candidates the anchor omits: keep the report values
        for office, party, cand in unmatched:
            print(f"NOT-ANCHORED (report value kept): {office} / {party} / "
                  f"{cand}")
    leftover = list(anchor)
    if leftover:
        raise SystemExit(f"{len(leftover)} anchor rows not represented in "
                         f"the summary report, e.g. {leftover[0]}")
    print(f"certified anchor: replaced {n_replaced} countywide named rows, "
          f"{len(unmatched)} kept at report values")


def main():
    argv = sys.argv[1:]
    county_source = False
    dos_path, anchor_path = _DOS_SLICE_DEFAULT, _ANCHOR_DEFAULT
    if "--county-source" in argv:
        county_source = True
        argv = [a for a in argv if a != "--county-source"]
    for flag, which in (("--dos", "dos"), ("--anchor", "anchor")):
        while flag in argv:
            i = argv.index(flag)
            arg = argv[i + 1]
            del argv[i:i + 2]
            if which == "dos":
                dos_path = arg
            else:
                anchor_path = arg
    dos = "--no-dos" not in argv
    anchor = "--no-anchor" not in argv
    argv = [a for a in argv if a not in ("--no-dos", "--no-anchor")]
    if len(argv) != 2:
        sys.exit(f"Usage: {sys.argv[0]} [--county-source] [--dos PATH] "
                 f"[--anchor PATH] [--no-dos] [--no-anchor] "
                 f"<input.pdf-or-.txt> <output.csv>")

    src, dst = argv
    rows, warnings, checks = parse(load_text(src),
                                   county_source=county_source)
    if not county_source and dos:
        _apply_dos(rows, dos_path)
    if county_source and anchor:
        _apply_anchor(rows, anchor_path)
    fieldnames = COUNTY_FIELDNAMES if county_source else PRECINCT_FIELDNAMES
    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)
        w.writerows(rows)
    n_prec = len({r[1] for r in rows}) if not county_source else "-"
    n_off = len({r[2] for r in rows})
    print(f"wrote {len(rows)} rows -> {dst} ({n_prec} precincts, {n_off} "
          f"offices)")

    if checks["writein_bad"]:
        for entry in checks["writein_bad"]:
            print("WARNING write-in arithmetic", entry)
    else:
        print("write-in arithmetic: details + Not Assigned == Write-In "
              "Totals for every contest with details")
    if checks["party_bad"]:
        for entry in checks["party_bad"]:
            print("WARNING party-ballot arithmetic", entry)
    else:
        print("party-ballot arithmetic: every contest's candidate+write-in "
              "sum <= Vote For N x that party's ballots cast")
    for w in warnings:
        print("WARN:", w)


if __name__ == "__main__":
    main()
