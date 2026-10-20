#!/usr/bin/env python3
"""
Parser for Centre County, PA 2022 Primary Election (May 17, 2022).

Sources (Electionware "Summary Results Report" prints, pdftotext -layout;
same family as the county's 2023-primary parser
``pa_centre_primary_2023_results_parser.py``, which also drives the shared
``electionware_txt`` text engine):

- "Centre PA 6-14-22 OFFICIAL PRECINCT SUMMARY WITHOUT REP US SENATE-WEBSITE.pdf"
  -- per-precinct official report for every contest EXCEPT the Republican
  U.S. Senate race (the county cut this race from the official files ahead
  of the June Oz/McCormick recount; the file's title says "DOES NOT INCLUDE
  ... NO DATE/ISSUE DATE BALLOTS").
- "Centre PA RECOUNT ELECTION PRECINCT ONLY REP US SENATE-WEBSITE.pdf"
  -- per-precinct RECOUNT results for the REP U.S. Senate contest only.
  Those rows fill the race the main file omits, so the county sums match
  the post-recount certified anchor.

Preparation (same approach as the 2023 primary parser):

* per-party statistics labels ("Registered Voters - DEMOCRATIC",
  "Ballots Cast - DEMOCRATIC", ...) are dropped for the shared engine and
  re-emitted by this wrapper's own scanner, keyed off the printed labels
  (2020-precedent Registered Voters / Ballots Cast rows that carry a
  party); totals and the blank-ballots row are left for the shared engine;
* contest headers carry the ballot party ("DEM GOVERNOR") -> the DEM/REP
  prefix is stripped and prepended to each candidate row of the contest so
  the shared PARTY_RE stamps the contest party (primary convention;
  write-in aggregate rows carry the contest party too);
* named "Write-In: ..." detail rows are skipped, as in every County file.

Office mapping mirrors the repo standards (U.S. Senate / Governor / ...
; "REPRESENTATIVE IN THE GENERAL ASSEMBLY Nth Legislative Distict" [sic --
the county's typo for District] -> State House N).  The U.S. House header
carries no district; all 87 precincts report GLENN GT THOMPSON, i.e. PA-15.

Usage:
    python parsers/pa_centre_primary_2022_results_parser.py \
        "<main.pdf|txt>" "<gopsenate_recount.pdf|txt>" "<output.csv>"

Output: 10-column precinct schema
(county,precinct,office,district,party,candidate,votes,election_day,mail,
provisional); the source's "Absentee/Mail-in" column -> mail.
"""

import collections
import csv
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from electionware_txt import (TxtConfig, parse_txt,
                              FIELDNAMES as PRECINCT_FIELDNAMES)
from electionware_precinct_np import prettify_huntingdon_precinct
import pa_centre_general_2023_results_parser as cg

COUNTY = "Centre"
HEADER_PARTY_RE = re.compile(r"^(DEM|REP) (.+)$")
STATS_MARK_RE = re.compile(r"^STATISTICS\b", re.I)
VOTE_FOR_RE = re.compile(r"^\s*Vote For\s+\d+\s*$", re.I)
# Per-party statistics labels this wrapper re-emits (with a party).
PARTY_STATS_LABEL_RE = re.compile(
    r"^(Registered Voters|Ballots Cast) - (DEMOCRATIC|REPUBLICAN)\b", re.I)

US_HOUSE_DISTRICT = "15"  # GLENN GT THOMPSON on every precinct's ballot


def normalize_office(line: str):
    """Map a printed contest header (party prefix stripped) to
    (office, district)."""
    line = re.sub(r"\s+", " ", line).strip()
    exact = {
        "UNITED STATES SENATOR": ("U.S. Senate", ""),
        "GOVERNOR": ("Governor", ""),
        "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
        "REPRESENTATIVE IN CONGRESS": ("U.S. House", US_HOUSE_DISTRICT),
        "MEMBER OF DEMOCRATIC STATE COMMITTEE":
            ("Member of Democratic State Committee", ""),
        "MEMBER OF REPUBLICAN STATE COMMITTEE":
            ("Member of Republican State Committee", ""),
        "COUNTY REPUBLICAN COMMITTEE CHAIR":
            ("County Republican Committee Chair", ""),
        "COUNTY REPUBLICAN COMMITTEE VICE-CHAIR":
            ("County Republican Committee Vice-Chair", ""),
    }
    if line in exact:
        return exact[line]
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY (\d+)", line)
    if m:
        return ("State House", m.group(1))
    m = re.match(r"^COUNTY DEMOCRATIC COMMITTEE PRECINCT PERSON (\d+)",
                 line)
    if m:
        # One contest per precinct; the printed slot number goes in district.
        # The precinct-name suffix after " - " duplicates the precinct column
        # (and is truncated in several headers), so it is dropped.
        return ("County Democratic Committee Precinct Person", m.group(1))
    return cg.normalize_office(line)


def prettify_precinct(name: str) -> str:
    """'N - X' -> 'N X' (the county's 2023/2024 primary files drop the dash
    and '#'), then the shared title-caser."""
    name = re.sub(r"^(\d+)\s*-\s*", r"\1 ", name.strip())
    name = re.sub(r"\s{2,}", " ", name).replace("#", "")
    return prettify_huntingdon_precinct(name.strip())


def load_text(input_path):
    if input_path.lower().endswith(".pdf"):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = tf.name
        subprocess.run(["pdftotext", "-layout", input_path, tmp], check=True)
        with open(tmp, encoding="utf-8") as fh:
            text = fh.read()
        os.unlink(tmp)
        return text
    with open(input_path, encoding="utf-8") as fh:
        return fh.read()


def run_engine(text, cfg, problems, label):
    prepared = prepare_precinct_text(text, problems)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(prepared)
        tmp = tf.name
    try:
        return parse_txt(pathlib.Path(tmp), cfg)
    finally:
        os.unlink(tmp)


def prepare_precinct_text(text, problems):
    """Strip the DEM/REP ballot-party prefix from contest headers; prepend
    the contest party token to candidate rows (the shared PARTY_RE then
    assigns the party); drop per-party statistics label lines (rewritten by
    scan_party_stats instead)."""
    lines = text.split("\n")
    n = len(lines)
    # Office headers: next non-empty line is "Vote For N".
    header_idx = set()
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and VOTE_FOR_RE.match(lines[j].strip()):
            header_idx.add(i)

    out = []
    party = ""
    in_contest = False
    for i, l in enumerate(lines):
        s = l.strip()
        if STATS_MARK_RE.match(s):
            in_contest = False
            party = ""
            out.append(l)
            continue
        if i in header_idx:
            m = HEADER_PARTY_RE.match(s)
            if m:
                party = m.group(1)
                out.append(m.group(2))
            else:
                party = ""
                out.append(l)
                problems.append("contest header without DEM/REP prefix: %r"
                                % s)
            in_contest = True
            continue
        if VOTE_FOR_RE.match(s):
            out.append(l)
            continue
        if re.match(r"^\s*Total Votes Cast\b", s, re.I):
            in_contest = False
            out.append(l)
            continue
        # Per-party stats labels (with any same-line numbers) go through
        # scan_party_stats instead of the shared engine.
        if PARTY_STATS_LABEL_RE.match(s):
            continue
        if in_contest and party:
            m = re.match(r"^(.*?)\s+(-?\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)"
                         r"\s+(\d[\d,]*)\s*$", s)
            if m:
                head = m.group(1).strip()
                if head.upper().startswith("WRITE-IN:"):
                    out.append(l)  # named write-in detail: skipped downstream
                    continue
                if head.upper() == "WRITE-IN TOTALS":
                    # Stamp the contest party: party-empty write-in rows from
                    # the DEM and REP sections of the same office would
                    # collide in the repo's duplicate_entries test.
                    out.append("%s Write-ins%s" % (party, s[len(head):]))
                    continue
                if head.upper() == "NOT ASSIGNED":
                    out.append(l)
                    continue
                if head:
                    out.append("%s %s" % (party, s))
                    continue
        out.append(l)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Per-party Registered Voters / Ballots Cast scanner (2020 precedented rows).
# In these reports BC numbers are printed on the line above the label; RV
# numbers trail the label.
# ---------------------------------------------------------------------------

RV_LABEL_RE = re.compile(
    r"^Registered Voters - (Total|DEMOCRATIC|REPUBLICAN)\s+([\d,]+)\s*$",
    re.I)
BC_LABEL_RE = re.compile(
    r"^Ballots Cast - (Total|DEMOCRATIC|REPUBLICAN|Blank)\s*$")
BC_INLINE_RE = re.compile(
    r"^Ballots Cast - (Total|DEMOCRATIC|REPUBLICAN|Blank)\s+(-?\d[\d,]*)"
    r"(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?(?:\s+(\d[\d,]*))?\s*$")
NUM_ONLY_RE = re.compile(r"^[\d,]+(?:\s+[\d,]+)*$")
BC_PARTY = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}


def scan_party_stats(text, problems):
    """Per-precinct per-party Registered Voters and Ballots Cast rows,
    keyed off the printed labels.  Returns rows in (precinct, dict) form."""
    lines = text.split("\n")
    # Precinct name above each STATISTICS marker (same rule as the engine's
    # find_precinct_name: first non-blank line upward).
    marker_name = {}
    prev_nonblank = ""
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if STATS_MARK_RE.match(s):
            marker_name[i] = prev_nonblank
        prev_nonblank = s

    rows = []
    precinct = ""
    in_stats = False
    last_num = None
    seen = collections.defaultdict(set)
    for i, l in enumerate(lines):
        s = l.strip()
        if not s:
            continue
        if STATS_MARK_RE.match(s):
            in_stats = True
            precinct = marker_name.get(i, precinct)
            last_num = None
            continue
        if not in_stats:
            continue
        # Column-header junk printed between the marker and the first label.
        if "Absentee/" in s or "ail-in" in s or "Provisional" in s or \
                any(s.lower().startswith(p) for p in
                    ("total", "election day")):
            continue
        m = RV_LABEL_RE.match(s)
        if m:
            kind = m.group(1).upper()
            party = "" if kind == "TOTAL" else BC_PARTY.get(kind)
            last_num = None
            if not party:
                continue  # total row: the shared engine emits it
            key = ("Registered Voters", party)
            if key in seen[precinct]:
                problems.append("stats: duplicate %s %s rows in %r"
                                % (key[0], party, precinct))
            seen[precinct].add(key)
            rows.append((precinct, "Registered Voters", party,
                         [m.group(2).replace(",", "")], "RV"))
            continue
        m = BC_INLINE_RE.match(s)
        if m:
            kind = m.group(1).upper()
            nums = [t.replace(",", "") for t in m.groups()[1:]
                    if t is not None]
            if kind in BC_PARTY:
                rows.append((precinct, "Ballots Cast", BC_PARTY[kind], nums,
                             "BC"))
            last_num = nums or None
            continue
        m = BC_LABEL_RE.match(s)
        if m:
            kind = m.group(1).upper()
            if kind in BC_PARTY:
                rows.append((precinct, "Ballots Cast", BC_PARTY[kind],
                             last_num, "BC"))
            last_num = None
            continue
        if NUM_ONLY_RE.match(s):
            last_num = s.split()
            continue
        # First non-statistics line ("Voter Turnout - ...", a contest header,
        # a page header) ends the block.
        in_stats = False
    return rows


def stats_rows_to_engine_form(party_rows):
    out = []
    for precinct, office, party, nums, kind in party_rows:
        if kind == "RV":
            votes = nums[0] if nums else ""
            ed = mail = prov = ""
        else:
            if not nums:
                continue
            if len(nums) == 1:
                votes, ed, mail, prov = nums[0], "", "", ""
            elif len(nums) == 4:
                votes, ed, mail, prov = nums
            else:
                continue
        out.append({
            "county": COUNTY,
            "precinct": prettify_precinct(precinct),
            "office": office,
            "district": "",
            "party": party,
            "candidate": "",
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        })
    return out


# ---------------------------------------------------------------------------
# Assembly.
# ---------------------------------------------------------------------------

STATS_ORDER = [
    ("Registered Voters", ""), ("Registered Voters", "DEM"),
    ("Registered Voters", "REP"), ("Ballots Cast", ""),
    ("Ballots Cast", "DEM"), ("Ballots Cast", "REP"),
    ("Ballots Cast - Blank", ""),
]


def arrange_stats(rows, problems):
    """Re-place each precinct's Registered Voters / Ballots Cast rows at the
    head of its block in the conventional order (total, DEM, REP, ...,
    blank), keeping candidate rows in parsed order.  (The recount report's
    U.S. Senate rows arrive after every precinct's main rows, so body rows
    are bucketed per precinct first.)"""
    stat = collections.OrderedDict()  # precinct -> {key: row}
    body = collections.OrderedDict()  # precinct -> [rows]
    for r in rows:
        key = (r["office"], r["party"])
        if key in STATS_ORDER:
            g = stat.setdefault(r["precinct"], collections.OrderedDict())
            if key in g:
                problems.append("duplicate stats row %s | %s in %r"
                                % (r["office"], r["party"], r["precinct"]))
            else:
                g[key] = r
        else:
            body.setdefault(r["precinct"], []).append(r)
    out = []
    for p, crows in body.items():
        for key in STATS_ORDER:
            if key in stat.get(p, {}):
                out.append(stat[p][key])
        # The race the main file omits (REP U.S. Senate, recount) leads the
        # Republican section on the ballot; insert it before the first REP
        # candidate row when one exists.
        sen = [r for r in crows if r["office"] == "U.S. Senate"
               and r["party"] == "REP"]
        rest = [r for r in crows if not (r["office"] == "U.S. Senate"
                                         and r["party"] == "REP")]
        first_rep = next((i for i, r in enumerate(rest)
                          if r["party"] == "REP"), None)
        if first_rep is None or not sen:
            out.extend(rest)
        else:
            out.extend(rest[:first_rep])
            out.extend(sen)
            out.extend(rest[first_rep:])
        del sen
    for p, g in stat.items():
        if p not in body:
            for key in STATS_ORDER:
                if key in g:
                    out.append(g[key])
    return out


def merge_recount(recount_rows, problems):
    """Republican U.S. Senate rows only from the recount report (candidate
    rows plus its Write-ins row); statistics rows stay with the main
    report's values."""
    keep = [r for r in recount_rows
            if r["office"] == "U.S. Senate"
            and r["party"] == "REP"]
    extra = [r for r in recount_rows if r not in keep]
    stat_extra = [r for r in extra
                  if r["office"] in ("Registered Voters", "Ballots Cast",
                                     "Ballots Cast - Blank")]
    extra = [r for r in extra if r not in stat_extra]
    if extra:
        problems.append("recount report: %d rows outside the REP U.S. "
                        "Senate contest were dropped: %r"
                        % (len(extra), sorted({(r["office"], r["party"])
                                               for r in extra})))
    return keep


def run(main_src, recount_src, dst, problems):
    cfg = TxtConfig(
        county=COUNTY,
        normalize_office=normalize_office,
        prettify_precinct=prettify_precinct,
        extra_junk=(r"(?i)^may 17, 2022\b",
                    r"(?i)^june 1, 2022\b",
                    r"(?i)^general primary\b",
                    r"(?i)^no date/issue date ballots\b",
                    r"(?i)^recount\b",
                    r"(?i)^(unofficial|official) results\b"),
    )

    main_text = load_text(main_src)
    main_rows, precinct_count, warnings, unnamed = run_engine(
        main_text, cfg, problems, "main")

    # Per-party RV/BC rows come from the printed labels (the shared engine
    # handles only the total/blank rows).
    party_rows = scan_party_stats(main_text, problems)
    stats_extra = stats_rows_to_engine_form(party_rows)
    if any(not r["precinct"] for r in stats_extra):
        problems.append("per-party stats rows with no precinct name")

    recount_text = load_text(recount_src)
    recount_rows, recount_count, recount_warnings, recount_unnamed = \
        run_engine(recount_text, cfg, problems, "recount")
    sen_rows = merge_recount(recount_rows, problems)

    rows = arrange_stats(main_rows + stats_extra + sen_rows, problems)

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRECINCT_FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    n_sen = len(sen_rows)
    main_names = {r["precinct"] for r in main_rows}
    recount_names = {r["precinct"] for r in recount_rows}
    print("wrote %d rows -> %s" % (len(rows), dst))
    print("precincts: main report %d (%d unnamed skipped), recount report "
          "%d (%d unnamed skipped)" % (precinct_count, unnamed,
                                       recount_count, recount_unnamed))
    print("recount U.S. Senate (REP) rows merged: %d" % n_sen)
    if main_names != recount_names:
        print("PROBLEM: precinct sets differ; recount-only: %r; "
              "main-only: %r" % (sorted(recount_names - main_names),
                                 sorted(main_names - recount_names)))
    print("per-party RV/BC rows from labels: %d" % len(stats_extra))
    if warnings:
        print("WARN (main): %d unmatched lines" % len(warnings))
        for w_ in warnings[:20]:
            print("  WARN:", w_)
    if recount_warnings:
        print("WARN (recount): %d unmatched lines" % len(recount_warnings))
        for w_ in recount_warnings[:20]:
            print("  WARN:", w_)
    return rows


def main(argv):
    if len(argv) != 4:
        sys.exit("Usage: %s <main.pdf|txt> <gopsenate_recount.pdf|txt> "
                 "<output.csv>" % argv[0])
    problems = []
    run(argv[1], argv[2], argv[3], problems)
    for p in problems[:30]:
        print("PROBLEM:", p)
    if len(problems) > 30:
        print("... %d more problems" % (len(problems) - 30))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))