#!/usr/bin/env python3
"""
Parse Lebanon County PA 2023 Municipal Primary (May 16, 2023) results.

Source: "Lebanon County Official Precinct Summary 2023 Primary" — the ONLY
Lebanon primary source ( Electionware "Precinct Summary Results Report",
348 pages, per-precinct sections; pre-extracted pdftotext -layout text).
Each page repeats the report header ("Precinct Summary Results Report /
OFFICIAL RESULTS", "May 16, 2023 Lebanon County") followed by the precinct
name; a precinct's section starts with a Statistics block

    Ballots Cast - Total / - DEMOCRATIC / - REPUBLICAN / - NONPARTISAN /
    Ballots Cast - Blank

(no Registered Voters rows in this report), followed by contest sections

      DEM JUSTICE OF THE SUPREME COURT
      Vote For 1
      <column-header junk>
    DANIEL MCCAFFERY                            24   15   9   0
    Write-In Totals                              0    0   0   0
      Not Assigned                               0    0   0   0

There are no per-contest "Total Votes Cast"/"Contest Totals" rows and no
Overvotes/Undervotes rows in this report.

Produces BOTH deliverables from this one source:

  * precinct CSV (10 columns, precinct = the source's own label, e.g.
    "Leb 1st Ward East 01E"): `python parsers/pa_lebanon_primary_2023
    _results_parser.py <input.txt> <output.csv>`
  * county-level CSV (9 columns, sum of the precinct rows over
    (office, district, party, candidate) — same aggregation as
    parsers/aggregate_county.py): add `--county-level`

Header mapping notes (Lebanon-specific, mirroring
parsers/pa_lebanon_general_2023_results_parser.py):

  * "COUNTY COMMISIONER" (source typo) -> office "County Commissioner".
  * "JUDGE OF THE COURT OF COMMON PLEAS" carries no district in the
    primary (the general's "52ND DISTRICT" was in the retention-era
    header); office "Judge of the Court of Common Pleas", district "".
  * "MAGISTERIAL DISTRICT JUDGE 52-03-05" -> district "52-3-05"
    (normalized to the general file's spelling; the general source
    printed both "52-3-05" and "52-03-05" and kept the first).
  * "SCHOOL DIRECTOR AT LARGE CORNWALL LEBANON" and
    "... NORTHERN LEBANON" appear TWICE per precinct per party as
    separate contests (Vote For 5 section first, then Vote For 1 --
    order verified in the source); rows are disambiguated the same way
    the general parser did, as offices "School Director (Vote For 5)" /
    "School Director (Vote For 1)".
  * "MEMBER CITY COUNCIL" prints as "COUNCIL MEMBER CITY" -> office
    "City Council", district "Lebanon City".
  * "COUNCIL MEMBER <boro>" -> "Borough Council" ("(2 Year)" for the
    "-2 YEAR" variant); "TOWNSHIP COMMISSIONER[...] <twp>" ->
    "Township Commissioner"; "SUPERVISOR <twp>" -> "Township Supervisor";
    "AUDITOR <twp>" -> "Township Auditor" ("(2 Year)"/"(4 Year)" variants
    keep the term suffix); "CONSTABLE LEBANON 7TH WARD" -> "Constable",
    district "Lebanon 7th Ward" (primary-only contest).
  * Candidates are ALL CAPS in the source and title-cased with
    pa_2023_summary_common.smart_title / clean_name, matching the
    general parser's titlecase_names=True.
  * Write-ins rows carry the CONTEST's party (DEM/REP) in the party
    column (repo-wide primary convention -- party-empty write-ins make
    the DEM and REP sections of the same office collide as duplicate
    rows in the county-level file).  "Not Assigned" and named
    "Write-In: NAME" detail rows fold into the Write-In Totals
    aggregate; their arithmetic is validated.
  * Per-party "Ballots Cast - DEMOCRATIC/REPUBLICAN/NONPARTISAN"
    statistics rows are NOT emitted (repo convention records only
    totals) but are kept in memory for the party-ballot arithmetic
    check.

Usage:
    python parsers/pa_lebanon_primary_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county-level]
"""

import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

try:
    from pa_2023_summary_common import clean_name
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pa_2023_summary_common import clean_name

COUNTY = "Lebanon"

PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "mail",
                       "provisional"]
COUNTY_FIELDNAMES = PRECINCT_FIELDNAMES[:1] + PRECINCT_FIELDNAMES[2:]

TERM_RE = re.compile(r"[-\s](\d)\s*YEAR\b", re.IGNORECASE)
VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.IGNORECASE)
# candidate / aggregate / statistics rows: name + 4 integers
ROW_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
CONTEST_HEADER_RE = re.compile(r"^(DEM|REP)\s+(.+)$")

SCHOOL_DUP = {"CORNWALL LEBANON", "NORTHERN LEBANON"}

# per-precinct, per-party occurrence counter for the duplicated
# School Director headers (1st section = Vote For 5, 2nd = Vote For 1)
_school_seen = {}


def _term_suffix(header):
    m = TERM_RE.search(header)
    if not m:
        return ""
    return f" ({m.group(1)} Year)"


def _title(name):
    return " ".join(w.capitalize() for w in name.split())


def map_contest(header, vote_for, occurrence):
    """(office, district) for a contest header with the DEM/REP prefix
    stripped.  ``occurrence`` is the 1-based count of this header within
    the current precinct+party (only used for the duplicated school
    sections).  Raises on unrecognized headers."""
    h = re.sub(r"\s+", " ", header).strip()
    hu = h.upper()

    if hu == "JUSTICE OF THE SUPREME COURT":
        return ("Justice of the Supreme Court", "")
    if hu == "JUDGE OF THE SUPERIOR COURT":
        return ("Judge of the Superior Court", "")
    if hu == "JUDGE OF THE COMMONWEALTH COURT":
        return ("Judge of the Commonwealth Court", "")
    if hu.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
        m = re.search(r"(\d+)(ST|ND|RD|TH)\s+DISTRICT", hu)
        return ("Judge of the Court of Common Pleas",
                f"{m.group(1)}{m.group(2).lower()} District" if m else "")
    if hu in ("COUNTY COMMISSIONER", "COUNTY COMMISIONER"):
        return ("County Commissioner", "")
    if hu == "CONTROLLER":
        return ("County Controller", "")
    if hu == "RECORDER OF DEEDS":
        return ("Recorder of Deeds", "")
    if hu == "TREASURER":
        return ("County Treasurer", "")
    if hu == "CORONER":
        return ("Coroner", "")
    if hu == "PROTHONOTARY":
        return ("Prothonotary", "")

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2})-(\d{1,2})-(\d{2})", hu)
    if m:
        # normalize "52-03-05" -> "52-3-05" (general file spelling)
        return ("Magisterial District Judge",
                f"{m.group(1)}-{int(m.group(2))}-{m.group(3)}")

    if hu.startswith("SCHOOL DIRECTOR AT LARGE"):
        district = _title(h[len("SCHOOL DIRECTOR AT LARGE"):].strip())
        if district.upper() in SCHOOL_DUP:
            office = f"School Director (Vote For {5 if occurrence == 1 else 1})"
            if int(vote_for) != (5 if occurrence == 1 else 1):
                raise SystemExit(
                    f"school section order mismatch: {h!r} occurrence "
                    f"{occurrence} but Vote For {vote_for}")
        else:
            office = "School Director"
        return (office, district)

    if hu.startswith("MEMBER CITY COUNCIL") or hu == "COUNCIL MEMBER CITY":
        return ("City Council", "Lebanon City")

    if hu.startswith("COUNCIL MEMBER"):
        rest = re.sub(r"^COUNCIL MEMBER[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Borough Council" + _term_suffix(hu), _title(rest))

    if hu.startswith("TOWNSHIP COMMISSIONER"):
        rest = re.sub(r"^TOWNSHIP COMMISSIONER[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Township Commissioner" + _term_suffix(hu), _title(rest))

    if hu.startswith("SUPERVISOR"):
        return ("Township Supervisor", _title(hu[len("SUPERVISOR"):].strip()))

    if hu.startswith("AUDITOR"):
        rest = re.sub(r"^AUDITOR[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Township Auditor" + _term_suffix(hu), _title(rest))

    if hu.startswith("CONSTABLE"):
        return ("Constable", _title(hu[len("CONSTABLE"):].strip()))

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


PAGE_JUNK_RE = re.compile(
    r"^(Precinct Summary Results Report\b|May 16, 2023\b"
    r"|Report generated with Electionware\b|Precinct Summary - \d"
    r"|Statistics\b|TOTAL\b|Day In/Absentee|ee$|al$)")


def parse(text, county=COUNTY):
    """Return (rows, warnings, stats).  rows: 10-column precinct rows.
    stats: per-precinct party ballots cast for the arithmetic check."""
    lines = text.splitlines()
    rows = []
    warnings = []
    stats = {}          # precinct -> {"DEM": n, "REP": n}
    blank = {}          # precinct -> "Ballots Cast - Blank" values

    def warn(msg):
        warnings.append(msg)

    precinct = None
    contest = None      # (party, office, district, vote_for)
    contest_header_idx = set()
    n_detail = 0

    # pre-mark contest-header lines: a DEM/REP line whose next nonblank
    # line is "Vote For N"
    for i, l in enumerate(lines):
        s = l.strip()
        m = CONTEST_HEADER_RE.match(s) if re.match(r"^  (DEM|REP)\b", l) else None
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and VOTEFOR_RE.match(lines[j].strip()):
            contest_header_idx.add(i)

    # pre-compute precinct for each "Ballots Cast - Total" / contest line:
    # the precinct name is the nonblank line right before the "Statistics"
    # column-header line that precedes the statistics block
    prec_of = {}
    prec = None
    for i, l in enumerate(lines):
        s = l.strip()
        if s.startswith("Statistics"):
            for j in range(i - 1, max(i - 8, -1), -1):
                t = lines[j].strip()
                if t and not t.startswith("Ballots") and "TOTAL" not in t \
                        and not PAGE_JUNK_RE.match(t):
                    prec = t
                    break
        prec_of[i] = prec
    precinct_names = {v for v in prec_of.values() if v is not None}

    school_seen = defaultdict(int)
    detail = defaultdict(int)     # (precinct, party, office, district) -> sum
    notassigned = defaultdict(int)
    writetot = defaultdict(int)
    contest_sum = defaultdict(int)
    skipped_nodigit = 0

    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if PAGE_JUNK_RE.match(s):
            continue

        if i in contest_header_idx:
            m = CONTEST_HEADER_RE.match(s)
            party, header = m.group(1).upper(), m.group(2)
            vf = lines[i + 1].strip() if i + 1 < len(lines) else ""
            vfm = VOTEFOR_RE.match(vf)
            occurrence = 1
            key = (precinct, party, header)
            if header.upper().startswith("SCHOOL DIRECTOR AT LARGE"):
                occurrence = school_seen[key] = school_seen.get(key, 0) + 1
            office, district = map_contest(header, vfm.group(1), occurrence)
            contest = (party, office, district, int(vfm.group(1)))
            continue

        if s.startswith("Ballots Cast - "):
            m = ROW_RE.match(s)
            if not m:
                warn(f"{precinct}: no numbers on statistics row: {s!r}")
                continue
            head = m.group(1).strip()
            vals = [int(v.replace(",", "")) for v in m.groups()[1:]]
            # a Statistics block always opens a (new) precinct section
            precinct = prec_of[i] if prec_of[i] is not None else precinct
            p = precinct
            if p is None:
                raise SystemExit(f"statistics block with no precinct: {s!r}")
            if head == "Ballots Cast - Total":
                rows.append([county, p, "Ballots Cast", "", "", "",
                             str(vals[0]), str(vals[1]), str(vals[2]),
                             str(vals[3])])
            elif head == "Ballots Cast - Blank":
                blank[p] = vals
                rows.append([county, p, "Ballots Cast - Blank", "", "", "",
                             str(vals[0]), str(vals[1]), str(vals[2]),
                             str(vals[3])])
            else:
                # per-party statistics: keep for the arithmetic check only
                m2 = re.match(r"^Ballots Cast - (DEMOCRATIC|REPUBLICAN|"
                              r"NONPARTISAN)$", head)
                if not m2:
                    warn(f"{p}: unrecognized statistics row: {s!r}")
                else:
                    stats.setdefault(p, {})[m2.group(1)] = vals
            continue

        if VOTEFOR_RE.match(s):
            continue  # consumed by the contest-header detection above

        if s in precinct_names:
            continue  # precinct label at the top of a page

        if contest is None:
            # anything numeric-looking outside a contest is unexpected
            if ROW_RE.match(s):
                warn(f"{precinct}: data row outside any contest: {s[:70]!r}")
            else:
                skipped_nodigit += 1
            continue

        m = ROW_RE.match(s)
        if not m:
            if not any(c.isdigit() for c in s):
                # column-header fragments of the wrapped table header
                skipped_nodigit += 1
                continue
            warn(f"{precinct}: unrecognized row in contest {contest}: "
                 f"{s[:70]!r}")
            continue

        head = m.group(1).strip()
        vals = [int(v.replace(",", "")) for v in m.groups()[1:]]
        k = (precinct,) + contest
        vfn = contest[3]
        if head == "Write-In Totals":
            rows.append([county, precinct, contest[1], contest[2], contest[0],
                         "Write-ins", str(vals[0]), str(vals[1]),
                         str(vals[2]), str(vals[3])])
            writetot[k] += vals[0]
        elif head == "Not Assigned":
            notassigned[k] += vals[0]
            continue  # folded into Write-In Totals; not part of the sum
        elif head.startswith("Write-In:"):
            detail[k] += vals[0]
            continue  # folded into Write-In Totals; not part of the sum
        elif head in ("Overvotes", "Undervotes"):
            rows.append([county, precinct, contest[1], contest[2], "",
                         head, str(vals[0]), str(vals[1]), str(vals[2]),
                         str(vals[3])])
        else:
            name = clean_name(head, titlecase=True)
            rows.append([county, precinct, contest[1], contest[2], contest[0],
                         name, str(vals[0]), str(vals[1]), str(vals[2]),
                         str(vals[3])])
        contest_sum[k] += vals[0]

    # ---- write-in arithmetic: details + Not Assigned == Write-In Totals --
    writein_bad = []
    for k in sorted(set(writetot) | set(detail) | set(notassigned)):
        wt = writetot.get(k, 0)
        parts = detail.get(k, 0) + notassigned.get(k, 0)
        if (k in detail or k in notassigned) and parts != wt:
            writein_bad.append((k, parts, wt))

    # ---- party-ballot arithmetic: per contest, candidate+write-in rows
    # <= Vote For N x that party's Ballots Cast for the precinct ----
    party_bad = []
    contest_by_party = defaultdict(int)
    for (p, party, office, district, vfn), v in contest_sum.items():
        contest_by_party[(p, party, office, district, vfn)] += v
    for (p, party, office, district, vfn), v in contest_by_party.items():
        stat_key = {"DEM": "DEMOCRATIC", "REP": "REPUBLICAN"}.get(party)
        b = stats.get(p, {}).get(stat_key)
        if b is None:
            warn(f"{p}: no Ballots Cast - {stat_key} statistics row")
            continue
        if v > vfn * b[0]:
            party_bad.append((p, party, office, district, vfn, v, b[0]))

    return rows, warnings, {
        "stats": stats, "blank": blank, "detail": detail,
        "notassigned": notassigned, "writetot": writetot,
        "writein_bad": writein_bad, "party_bad": party_bad,
        "contest_sum": dict(contest_sum), "skipped_nodigit": skipped_nodigit,
    }


def aggregate_county(rows):
    """Sum the precinct rows over (office, district, party, candidate)."""
    agg = {}
    order = []
    for r in rows:
        key = (r[2], r[3], r[4], r[5])
        if key not in agg:
            agg[key] = [0, 0, 0, 0]
            order.append(key)
        for k in range(4):
            v = r[6 + k]
            if v != "":
                agg[key][k] += int(v)
    out = []
    for key in order:
        s = agg[key]
        out.append([COUNTY, key[0], key[1], key[2], key[3],
                    str(s[0]), str(s[1]), str(s[2]), str(s[3])])
    return out


def main():
    args = [a for a in sys.argv[1:] if a != "--county-level"]
    county_level = "--county-level" in sys.argv[1:]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt-or-.pdf> <output.csv> "
                 f"[--county-level]")
    src, dst = args
    text = load_text(src)
    rows, warnings, checks = parse(text)

    if county_level:
        agg = aggregate_county(rows)
        with open(dst, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COUNTY_FIELDNAMES)
            w.writerows(agg)
        print(f"wrote {len(agg)} county-level rows -> {dst}")
    else:
        with open(dst, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(PRECINCT_FIELDNAMES)
            w.writerows(rows)
        prec = {r[1] for r in rows}
        print(f"wrote {len(rows)} rows -> {dst} ({len(prec)} precincts, "
              f"{len(checks['contest_sum'])} contest sections)")

    if checks["writein_bad"]:
        for k, parts, wt in checks["writein_bad"]:
            print(f"WARNING write-in arithmetic {k}: details+not-assigned "
                  f"{parts} != Write-In Totals {wt}")
    else:
        print("write-in arithmetic: details + Not Assigned == Write-In "
              "Totals for all contests with details")
    if checks["party_bad"]:
        for p, party, office, district, vfn, v, b in checks["party_bad"]:
            print(f"WARNING party-ballot arithmetic {p} {party} {office} | "
                  f"{district}: rows sum {v} > {vfn} x Ballots Cast {b}")
    else:
        print("party-ballot arithmetic: every contest's rows sum <= "
              "Vote For N x that party's ballots cast")
    for w in warnings:
        print("WARN:", w)


if __name__ == "__main__":
    main()