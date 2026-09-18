#!/usr/bin/env python3
"""Mercer County PA 2023 Municipal Primary (May 16, 2023) — results parser.

Source files (Electionware, pdftotext -layout):

- "Mercer County Summary Results 2023 Primary.pdf" — county-level
  "Summary Results Report" -> 9-column county CSV
- "Mercer County Precinct Results 2023 Primary.pdf" — per-precinct
  "Precinct Summary Results Report" -> 10-column precinct CSV

The input mode is auto-detected from the first page-header line.

Format notes (Electionware primary print):

- Contest headers carry the ballot party as a prefix: "DEM JUSTICE OF THE
  SUPREME COURT" / "REP COUNTY COMMISSIONER".  The contest party goes into
  the party column of every candidate row of that contest, including the
  Write-ins and Overvotes/Undervotes rows (repo primary convention —
  e.g. 20240423__pa__primary__adams__precinct.csv; empty-party rows would
  collide between the DEM and REP sections of one office in the
  duplicate_entries data test, which hashes rows ignoring vote columns).
- Data-row columns: TOTAL, Election Day, Absentee, Provisional.  The
  summary's STATISTICS header prints "Absentee" for the mail column
  (mapped to `mail` per the 2023 general convention).
- Statistics block: "Ballots Cast - Total" / "Ballots Cast - Blank" carry
  ED/Mail/Provisional breakdowns; per-party Registered Voters / Ballots
  Cast rows are not recorded (repo convention).  The county summary also
  prints a per-contest "Precincts Reporting N of M" row (skipped).
- "Write-In Totals" aggregate row -> candidate "Write-ins"; named
  "Write-In:" detail rows would be folded into it (this county's sources
  carry no detail rows).  "Not Assigned" is not emitted.
- Referendum questions ("LIQUOR LICENSE REFERENDUM DEER CREEK TWP", ...)
  have no party prefix; YES/NO rows are emitted with an empty party.
- Several Mercer contests share one header within a party (two supervisor
  seats, two auditor terms, second School Director region seats).  The
  source distinguishes them only by ballot order, so repeated
  (party, office, district) sections get a " (2)"/" (3)" occurrence
  suffix in ballot order — per precinct in precinct mode, per party in
  county mode — exactly the relabeling the county's 2023 general files use
  (pa_mercer_general_2023_results_parser.disambiguate).
- Office/district mapping mirrors the county's 2023 general parser
  (pa_mercer_general_2023_results_parser.normalize, which reuses the 2025
  office tables): party prefix and hyphenated term tokens ("6-YR",
  "AUDITOR - TWP 6YR", "MAYOR 2-YEAR", "SUPERVISOR - 2YR") are stripped
  before it, matching the general file's term-free office names.
- Candidate names keep their printed (ALL-CAPS) casing, as in the county's
  2023 general files.

Usage:
    python parsers/pa_mercer_primary_2023_results_parser.py <input.txt|pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile

from pa_2023_summary_common import (
    is_junk, nums_from, split_candidate, vals_from,
)
from pa_mercer_general_2023_results_parser import (
    normalize as mercer_normalize,
)
from pa_mercer_general_2025_results_parser import mercer_muni, title_case

COUNTY = "Mercer"

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
PRECINCT_FIELDNAMES = ["county", "precinct", "office", "district", "party",
                       "candidate", "votes", "election_day", "mail",
                       "provisional"]

PARTY_PREFIX = re.compile(r"^(DEM|REP)\s+(.+)$")
VOTE_FOR = re.compile(r"^vote for \d+$", re.I)
WRITE_IN_TOTALS = re.compile(r"^write-in totals", re.I)
WRITE_IN_DETAIL = re.compile(r"^write-in:", re.I)
NOT_ASSIGNED = re.compile(r"^not assigned", re.I)
TOTAL_VOTES_CAST = re.compile(r"^total votes cast", re.I)
CONTEST_TOTALS = re.compile(r"^contest totals", re.I)
OVER_UNDER = re.compile(r"^(over ?votes|under ?votes)", re.I)
NO_CANDIDATE = re.compile(r"^no candidate filed", re.I)
PRECINCTS_REPORTING = re.compile(r"^precincts reporting\b", re.I)
REFERENDUM = re.compile(r"^(.+?)\s+REFERENDUM\s+(.+)$", re.IGNORECASE)

# Leading hyphenated term tokens after the office word:
#   "AUDITOR - TWP 6YR WORTH TWP", "AUDITOR - TWP 2-YR WEST SALEM TWP",
#   "SUPERVISOR - 2YR PINE TWP", "SUPERVISOR 6-YR WORTH TWP",
#   "COUNCIL 2-YEAR JAMESTOWN", "MAYOR 2-YEAR MERCER BOROUGH".
# Stripped before the office mapping so the office/district output matches
# the term-free forms the county's 2023 general file used (term
# distinctions there were carried by occurrence suffixes " (2)"/" (3)").
TERM_TOKEN = re.compile(r"^(?:-|TWP|\d+(?:\s*-)?\s*(?:YR|YEARS?))$", re.I)

# page furniture not caught by pa_2023_summary_common.is_junk
EXTRA_JUNK = re.compile(
    r"^(2023 municipal primary\b"
    r"|precinct summary results report\b"
    r"|precinct summary -"
    r"|statistics\b)",
    re.IGNORECASE)


def junk(s):
    return is_junk(s) or bool(EXTRA_JUNK.match(s.strip()))


# --------------------------------------------------------------------------
# office / district / party mapping (mirrors the county 2023 general parser)
# --------------------------------------------------------------------------


def map_contest(header):
    """(office, district, party) from a contest header line."""
    h = re.sub(r"\s+", " ", header.strip())
    party = ""
    m = re.match(r"^(DEM|REP)\s+(.+)$", h)
    if m:
        party, h = m.group(1), m.group(2).strip()

    m = REFERENDUM.match(h)
    if m:
        return (title_case(m.group(1)) + " Referendum",
                mercer_muni(m.group(2)), party)

    # strip leading hyphenated term tokens after the office word
    toks = h.split()
    if len(toks) > 1:
        k = 1
        while k < len(toks) and TERM_TOKEN.match(toks[k]):
            k += 1
        h = " ".join(toks[:1] + toks[k:])

    office, district = mercer_normalize(h)
    return office, district, party


class Contest:
    def __init__(self, mapped, raw, precinct, occurrence):
        office, district, party = mapped
        if occurrence:
            office = f"{office} ({occurrence + 1})"
        self.office = office
        self.district = district
        self.party = party
        self.raw = raw
        self.precinct = precinct
        self.agg = False            # saw a "Write-In Totals" aggregate row
        self.agg_vals = None
        self.details = [0, 0, 0, 0]  # "Write-In:" detail-row accumulator
        self.na = [0, 0, 0, 0]      # "Not Assigned" row
        self.has_detail = False
        self.has_na = False
        self.expected = None        # "Total Votes Cast" [total, ed, mi, pr]
        self.ct = None              # "Contest Totals" row (county mode)
        self.over_under = 0
        self.cand_sum = [0, 0, 0, 0]  # candidate + No-Candidate-Filed rows

    def label(self):
        head = self.precinct or ""
        return f"{head}|{self.office}|{self.district}|{self.party}"


class Parser:
    def __init__(self, county):
        self.county = county
        self.rows = []
        self.warnings = []
        self.recon = []      # (label, message)
        self.writein_checks = []  # (label, aggregate, details+NA)
        self.ballot_checks = []   # (label, contest_totals, seats*ballots)
        self.tvc_checks = []     # (label, total votes cast, party ballots*seats)
        self.contest_count = 0
        self.occurrence = {}      # (precinct, party, office, district) -> n
        self.party_ballots = {}   # county mode: party -> ballots cast total
        self.precinct_party_ballots = {}  # precinct mode: precinct -> party -> n

    def warn(self, msg):
        self.warnings.append(msg)

    def emit(self, contest, party, candidate, vals):
        r = dict.fromkeys(FIELDNAMES, "")
        r["county"] = self.county
        r["precinct"] = contest.precinct or ""
        r["office"] = contest.office
        r["district"] = contest.district
        r["party"] = party
        r["candidate"] = candidate
        (r["votes"], r["election_day"], r["mail"],
         r["provisional"]) = [str(v) for v in vals]
        self.rows.append(r)

    def emit_meta(self, precinct, office, vals):
        """vals: [total] or [total, ed, mi, pr]; None drops a breakdown."""
        r = dict.fromkeys(FIELDNAMES, "")
        r["county"] = self.county
        r["precinct"] = precinct or ""
        r["office"] = office
        r["candidate"] = ""
        r["votes"] = str(vals[0])
        for field, v in zip(("election_day", "mail", "provisional"), vals[1:]):
            if v is not None:
                r[field] = str(v)
        self.rows.append(r)

    def stats_row(self, precinct, label, nums):
        if label == "Registered Voters - Total":
            self.emit_meta(precinct, "Registered Voters", [int(nums[0])])
        elif label == "Ballots Cast - Total":
            self.emit_meta(precinct, "Ballots Cast", [int(x) for x in nums])
        elif label == "Ballots Cast - Blank":
            self.emit_meta(precinct, "Ballots Cast - Blank",
                           [int(x) for x in nums])
        elif label.startswith("Ballots Cast - "):
            party = label.split(" - ", 1)[1]
            if party in ("DEMO", "DEMOCRATIC"):
                party = "DEM"
            elif party in ("REP", "REPUBLICAN"):
                party = "REP"
            else:
                return
            if precinct:
                self.precinct_party_ballots.setdefault(precinct, {})[party] = \
                    int(nums[0])
            else:
                self.party_ballots[party] = int(nums[0])
        # per-party Registered Voters rows: not recorded (repo convention)

    def flush_contest(self, contest):
        """Close a contest: write-in fallback, then reconciliation checks."""
        if contest is None:
            return
        label = contest.label()
        if not contest.agg and (contest.has_detail or contest.has_na):
            # no aggregate row -> one Write-ins row from details + Not Assigned
            s = [a + b for a, b in zip(contest.details, contest.na)]
            self.emit(contest, contest.party, "Write-ins", s)
        # 1. candidate rows + write-ins == Total Votes Cast
        if contest.expected is not None:
            s = contest.cand_sum[0] + self.wi_sum(contest, 0)
            if s != contest.expected[0]:
                self.recon.append((label, f"rows sum {s} != Total Votes Cast "
                                          f"{contest.expected[0]}"))
            for k, field in enumerate(("election_day", "mail", "provisional")):
                v = contest.cand_sum[k + 1] + self.wi_sum(contest, k + 1)
                if contest.expected[k + 1] != v:
                    self.recon.append(
                        (label, f"{field} sum {v} != "
                         f"{contest.expected[k + 1]}"))
        # 2. write-in arithmetic: aggregate == details + Not Assigned
        if contest.agg and (contest.has_detail or contest.has_na):
            d = [a + b for a, b in zip(contest.details, contest.na)]
            a = contest.agg_vals
            if list(a) != list(d):
                self.writein_checks.append((label, list(a), list(d)))
        # 3. Contest Totals vs the party's ballots cast x seats.
        #    Exact only for countywide/statewide contests (empty district):
        #    local contests' Contest Totals cover only their jurisdictions,
        #    so for those the check is the upper bound.
        if contest.ct is not None and contest.party in self.party_ballots:
            exp = self.party_ballots[contest.party] * contest.seats
            if (contest.ct[0] != exp and not contest.district) \
                    or contest.ct[0] > exp:
                self.ballot_checks.append(
                    (label, contest.ct[0], exp))
        # 4. precinct-mode party-ballot arithmetic: Total Votes Cast must
        #    not exceed the party's ballots cast x seats
        if (contest.expected is not None and self.precinct_mode
                and contest.party in self.precinct_party_ballots.get(
                    contest.precinct or "", {})):
            exp = (self.precinct_party_ballots[contest.precinct]
                   [contest.party] * contest.seats)
            if contest.expected[0] > exp:
                self.ballot_checks.append((label, contest.expected[0], exp))

    @staticmethod
    def wi_sum(contest, k=0):
        if contest.agg:
            return contest.agg_vals[k]
        return (contest.details[k] + contest.na[k])

    def run(self, text, precinct_mode):
        self.precinct_mode = precinct_mode
        lines = [l.rstrip() for l in text.split("\n")]

        # precinct names (the non-blank line above each "Statistics" line)
        names = set()
        if precinct_mode:
            for k, l in enumerate(lines):
                if l.strip().startswith("Statistics"):
                    j = k - 1
                    while j >= 0 and not lines[j].strip():
                        j -= 1
                    if j >= 0:
                        names.add(lines[j].strip())

        # lines that are contest headers (the line above "Vote For N")
        header_lines = set()
        for k, l in enumerate(lines):
            if VOTE_FOR.match(l.strip()):
                j = k - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    header_lines.add(j)

        precinct = None
        current = None
        i = 0
        n = len(lines)

        while i < n:
            stripped = lines[i].strip()
            if not stripped:
                i += 1
                continue

            # precinct name = non-blank line before the "Statistics" line
            if precinct_mode and stripped.startswith("Statistics"):
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                precinct = lines[j].strip() if j >= 0 else None
                if current is not None:
                    self.flush_contest(current)
                current = None
                i += 1
                continue

            # repeated precinct-name banner on continuation pages
            if precinct_mode and stripped in names:
                i += 1
                continue

            # statistics rows (only when no contest is open)
            m = re.match(r"^(Registered Voters|Ballots Cast) - (.+)$",
                         stripped)
            if m and current is None:
                label = (m.group(1) + " - "
                         + m.group(2).strip().split()[0])
                nums = nums_from(stripped.split())
                if nums and label in ("Registered Voters - Total",
                                      "Ballots Cast - Total",
                                      "Ballots Cast - Blank"):
                    if precinct_mode and precinct is None:
                        self.warn("statistics rows before any precinct name")
                    self.stats_row(precinct, label, nums)
                elif nums and label.startswith("Ballots Cast - "):
                    self.stats_row(precinct, label, nums)
                i += 1
                continue

            # contest header: the line just above "Vote For N"
            if VOTE_FOR.match(stripped):
                j = i - 1
                while j >= 0 and (not lines[j].strip()
                                  or junk(lines[j].strip())):
                    j -= 1
                header = lines[j].strip() if j >= 0 else ""
                mapped = map_contest(header)
                # note: unlike some counties, a repeated header here is
                # always a NEW contest section (two supervisor/auditor/
                # school-director seats); contest blocks never span pages,
                # so there are no page continuations to merge.
                if current is not None:
                    self.flush_contest(current)
                key = ((precinct or ""), mapped[2], mapped[0], mapped[1])
                occ = self.occurrence.get(key, 0)
                self.occurrence[key] = occ + 1
                current = Contest(mapped, header, precinct, occ)
                current.seats = int(stripped.split()[-1])
                self.contest_count += 1
                i += 1
                continue

            if i in header_lines or junk(stripped) or current is None:
                i += 1
                continue

            if PRECINCTS_REPORTING.match(stripped):
                i += 1
                continue

            tokens = stripped.split()
            nums = nums_from(tokens)
            lead = tokens[0].upper().rstrip(":")

            if TOTAL_VOTES_CAST.match(stripped):
                if len(nums) >= 4:
                    current.expected = [int(x) for x in nums[:4]]
                elif nums:
                    current.expected = [int(nums[0]), 0, 0, 0]
                i += 1
                continue
            if CONTEST_TOTALS.match(stripped):
                if len(nums) >= 4:
                    current.ct = [int(x) for x in nums[:4]]
                # nothing data-bearing follows "Contest Totals" inside a
                # section; close it here so page furniture between sections
                # can't be mistaken for a data row of the open contest
                self.flush_contest(current)
                current = None
                i += 1
                continue
            if OVER_UNDER.match(stripped):
                kind = ("Overvotes" if stripped.lower().startswith("over")
                        else "Undervotes")
                total, ed, mi, pr = vals_from(nums)
                self.emit(current, current.party, kind,
                          [int(total or 0), int(ed or 0), int(mi or 0),
                           int(pr or 0)])
                current.over_under += int(total or 0)
                i += 1
                continue
            if WRITE_IN_TOTALS.match(stripped):
                total, ed, mi, pr = vals_from(nums)
                current.agg = True
                current.agg_vals = [int(total or 0), int(ed or 0),
                                    int(mi or 0), int(pr or 0)]
                self.emit(current, current.party, "Write-ins",
                          current.agg_vals)
                i += 1
                continue
            if WRITE_IN_DETAIL.match(stripped):
                if len(nums) >= 4:
                    current.details = [a + int(b) for a, b in
                                       zip(current.details, nums[:4])]
                elif nums:
                    current.details[0] += int(nums[0])
                current.has_detail = True
                i += 1
                continue
            if NOT_ASSIGNED.match(stripped):
                if len(nums) >= 4:
                    current.na = [a + int(b) for a, b in
                                  zip(current.na, nums[:4])]
                elif nums:
                    current.na[0] += int(nums[0])
                current.has_na = True
                i += 1
                continue
            if NO_CANDIDATE.match(stripped):
                total, ed, mi, pr = vals_from(nums)
                vals = [int(total or 0), int(ed or 0), int(mi or 0),
                        int(pr or 0)]
                self.emit(current, current.party, "No Candidate Filed", vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
                i += 1
                continue
            if lead in ("YES", "NO"):
                # referendum questions (no party header): Yes/No rows
                total, ed, mi, pr = vals_from(nums)
                vals = [int(total or 0), int(ed or 0), int(mi or 0),
                        int(pr or 0)]
                self.emit(current, "", lead.capitalize(), vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
                i += 1
                continue

            # candidate row
            head, row_party, allnums = split_candidate(tokens)
            name = re.sub(r"\s+", " ", " ".join(head)).strip()
            if name and allnums:
                total, ed, mi, pr = vals_from(allnums)
                if row_party and row_party != current.party:
                    self.warn(f"{current.label()}: row party {row_party} != "
                              f"header party {current.party} for {name!r} "
                              f"(header wins)")
                vals = [int(total or 0), int(ed or 0), int(mi or 0),
                        int(pr or 0)]
                self.emit(current, current.party, name, vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
            i += 1

        self.flush_contest(current)
        return self


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)
        for r in rows:
            w.writerow([r[c] for c in fieldnames])
    return len(rows)


def text_from_input(path):
    if path.lower().endswith(".pdf"):
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False)
        out.close()
        subprocess.run(["pdftotext", "-layout", path, out.name], check=True,
                       capture_output=True, text=True)
        with open(out.name, encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def main(argv):
    if len(argv) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt|pdf> <output.csv>")
    src, dst = argv[0], argv[1]
    text = text_from_input(src)
    first = next((l for l in text.split("\n") if l.strip()), "")
    precinct_mode = first.startswith("Precinct Summary Results Report")
    P = Parser(COUNTY).run(text, precinct_mode)
    fields = PRECINCT_FIELDNAMES if precinct_mode else FIELDNAMES
    n = write_csv(dst, P.rows, fields)
    mode = "precinct" if precinct_mode else "county"
    print(f"wrote {n} rows -> {dst} ({mode} mode)")
    print(f"contests parsed: {P.contest_count}")
    for w in P.warnings[:20]:
        print("WARN:", w)
    if len(P.warnings) > 20:
        print(f"... {len(P.warnings) - 20} more warnings")
    if P.recon:
        for label, msg in P.recon[:30]:
            print(f"RECON {label}: {msg}")
        print(f"contest totals check: {len(P.recon)} MISMATCHES")
    else:
        print("contest totals check: all contests reconcile")
    if P.writein_checks:
        for label, a, d in P.writein_checks[:20]:
            print(f"WRITE-IN ARITH {label}: aggregate {a} != details+NA {d}")
    if P.ballot_checks:
        for label, ct, exp in P.ballot_checks[:20]:
            print(f"BALLOT-CHECK {label}: Contest Totals {ct} != "
                  f"party ballots cast x seats {exp}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))