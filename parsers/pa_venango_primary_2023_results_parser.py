#!/usr/bin/env python3
"""Venango County PA 2023 Municipal Primary (May 16, 2023) — results parser.

Source files (Electionware, pdftotext -layout):

- "Venango County Summary Results 2023 Primary.pdf" — county-level
  "Election Summary Report - w/Write-in Names" -> 9-column county CSV
- "Venango County Precinct Summary Results 2023 Primary.pdf" — per-precinct
  "Precinct Summary Report - w/Write-in Names" -> 10-column precinct CSV

The input mode is auto-detected from the first page-header line.

Format notes (Electionware primary "w/Write-in Names" prints):

- Contest headers carry the ballot party as a prefix: "DEM Justice of the
  Supreme Court" / "REP County Commissioner".  The contest party goes into
  the party column of every row of that contest, including the Write-ins and
  Overvotes/Undervotes rows: the repo's duplicate_entries data test hashes
  all non-vote columns, so party-empty rows would collide between the DEM
  and REP sections of the same office (this also matches the published
  2020/2024 primary files and the sibling 2023 primary county files).
- Data-row columns: TOTAL, VOTE %, Election Day, Mail Votes, Provisional
  (the pct token is dropped).
- Multi-page write-in lists repeat the contest header + "Vote For N" on
  continuation pages; a section closes at "Contest Totals" and a repeated
  header inside an open section is a continuation, not a new contest.
- Statistics block: "Ballots Cast - Total" / "Ballots Cast - Blank" carry
  ED/Mail/Provisional breakdowns; per-party Registered Voters / Ballots Cast
  rows are not recorded (repo convention).
- "Write-In Totals" aggregate row -> candidate "Write-ins"; named
  "Write-In: <name>" detail rows are folded into it (not emitted).  "Not
  Assigned" is not emitted (the aggregate includes it).  If a contest has no
  aggregate row, the details (+ Not Assigned) are summed into one Write-ins
  row.
- Office/district mapping mirrors the Venango 2023 general parser
  (pa_venango_general_2023_results_parser.map_contest), with the party
  prefix stripped; the general file's two Superior-Court retention questions
  do not exist in a primary.

Usage:
    python parsers/pa_venango_primary_2023_results_parser.py <input.txt|pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile

from pa_2023_summary_common import (
    is_junk, nums_from, split_candidate, clean_name, vals_from,
)

COUNTY = "Venango"

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

# page furniture not caught by pa_2023_summary_common.is_junk
EXTRA_JUNK = re.compile(
    r"^(venango county municipal primary 2023\b"
    r"|precinct summary report\b"
    r"|venango county$)",
    re.IGNORECASE)


def junk(s):
    return is_junk(s) or bool(EXTRA_JUNK.match(s.strip()))


# --------------------------------------------------------------------------
# office / district mapping (mirrors the 2023 general Venango parser)
# --------------------------------------------------------------------------

CITY_COUNCIL = re.compile(r"^City Council (District \d+|At Large OC? Oil City)$")


def map_contest(header):
    """(office, district, party) from a party-prefixed contest header;
    None if the header is not a DEM/REP contest header."""
    h = re.sub(r"\s+", " ", header.strip())
    m = PARTY_PREFIX.match(h)
    if not m:
        return None
    party, body = m.group(1), m.group(2).strip()

    # The primary file doubles the Oil City district label on the city
    # council headers: "City Council District 3 City Council District 3".
    m2 = re.match(r"^City Council (District \d+) City Council District \d+$",
                  body)
    if m2:
        return "City Council", "Oil City " + m2.group(1), party
    if body == "City Council At Large Oil City":
        return "City Council", "Oil City At Large", party

    m = re.match(r"^(Mayor|City Council)\s+(Oil City)$", body)
    if m:
        return m.group(1), "Oil City", party

    m = CITY_COUNCIL.match(body)
    if m:
        tail = m.group(1)
        if tail.startswith("District"):
            return "City Council", "Oil City " + tail, party
        return "City Council", "Oil City At Large", party

    m = re.match(r"^Magisterial District Judge\s+(Magisterial District .+)$",
                 body)
    if m:
        return "Magisterial District Judge", m.group(1), party

    m = re.match(r"^(Constable)\s+(.+)$", body)
    if m:
        return "Constable", m.group(2), party

    # Sugarcreek Borough ward seats: the primary headers carry no T<n> term
    # code (the general file had "Borough Council Sugarcreek T4 Sugarcreek 2");
    # mirror the general mapping's district shape.
    m = re.match(r"^Borough Council Sugarcreek Sugarcreek (\d)$", body)
    if m:
        return ("Borough Council", "Sugarcreek Borough Ward " + m.group(1),
                party)

    # term-coded offices: "<base> [T<n>[V<k>]] <jurisdiction...> [T<n> ...]"
    m = re.match(r"^(Township Supervisor|Township Tax Collector|"
                 r"Township Auditor|Borough Council|Borough Tax Collector|"
                 r"Borough Auditor|School Director)(?:\s+T(\d)(?:V\d+))?"
                 r"\s+(.+)$", body)
    if not m:
        return body, "", party
    base, term, rest = m.group(1), m.group(2), m.group(3).strip()
    if not term:
        m2 = re.search(r"\s+T(\d)(?:V\d+)?\s+", rest)
        if m2:
            term = m2.group(1)
            rest = (rest[:m2.start()] + " " + rest[m2.end():]).strip()
        else:
            return body, "", party

    if base.startswith("Township"):
        office = f"{base} ({term} Year)"
        toks = rest.split(None, 1)
        # "Mineral" -> Mineral Township ; "Sugarcreek 2" -> keep as given
        if re.match(r"^\d+$", toks[-1]) and len(toks) > 1:
            return office, toks[0] + " Township Ward " + toks[1], party
        return office, rest + " Township", party
    if base.startswith("Borough"):
        office = f"{base} ({term} Year)"
        parts = rest.split()
        if parts and re.match(r"^\d+$", parts[-1]) and len(parts) > 1:
            return office, parts[0] + " Borough Ward " + parts[-1], party
        return office, rest + " Borough", party
    # School Director: rest = "<school district>[- <municipality>]"
    return f"School Director ({term} Year)", rest, party


# --------------------------------------------------------------------------
# parse loop
# --------------------------------------------------------------------------

class Contest:
    def __init__(self, mapped, raw, precinct):
        self.office, self.district, self.party = mapped
        self.raw = raw
        self.precinct = precinct
        self.agg = False            # saw a "Write-In Totals" aggregate row
        self.agg_vals = None
        self.details = [0, 0, 0, 0]  # Write-In: detail-row accumulator
        self.na = [0, 0, 0, 0]      # "Not Assigned" row
        self.has_detail = False
        self.has_na = False
        self.expected = None        # "Total Votes Cast" [total, ed, mi, pr]
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
        self.recon = []      # (label, contest, rows_sum, expected_total)
        self.writein_checks = []  # (label, agg, details+na)
        self.ct_checks = []  # (label, contest_totals, expected, over+under)
        self.contest_count = 0

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
        # per-party Registered Voters / Ballots Cast rows: not recorded

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
        # 3. Contest Totals == Total Votes Cast + Over/Undervotes
        if contest.ct is not None and contest.expected is not None:
            if contest.ct[0] != contest.expected[0] + contest.over_under:
                self.ct_checks.append(
                    (label, contest.ct[0], contest.expected[0],
                     contest.over_under))

    @staticmethod
    def wi_sum(contest, k=0):
        if contest.agg:
            return contest.agg_vals[k]
        return (contest.details[k] + contest.na[k])

    def run(self, text, precinct_mode):
        lines = [l.rstrip() for l in text.split("\n")]

        # lines that are contest headers (the line above "Vote For N")
        header_lines = set()
        for k, l in enumerate(lines):
            if VOTE_FOR.match(l.strip()):
                j = k - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    header_lines.add(j)

        # In precinct mode every page repeats the precinct name as its first
        # content line (after the "May 16, 2023" page-furniture line).  Those
        # lines are structural; when a page break falls inside a section they
        # would otherwise be parsed as a candidate row of the open contest.
        skip_lines = set()
        if precinct_mode:
            expect_name = False
            for k, l in enumerate(lines):
                s = l.strip()
                if expect_name:
                    if s and not junk(s) and not s.startswith("Statistics"):
                        skip_lines.add(k)
                        expect_name = False
                    continue
                expect_name = bool(re.match(r"^May 16, 2023\b", s))

        precinct = None
        current = None
        i = 0
        n = len(lines)

        while i < n:
            stripped = lines[i].strip()
            if not stripped or i in skip_lines:
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

            # statistics rows (only when no contest is open)
            stats_label = next(
                (s for s in ("Registered Voters - Total",
                             "Ballots Cast - Total",
                             "Ballots Cast - Blank")
                 if stripped.startswith(s)), None)
            if stats_label and current is None:
                nums = nums_from(stripped.split())
                if nums:
                    if precinct_mode and precinct is None:
                        self.warn("statistics rows before any precinct name")
                    self.stats_row(precinct, stats_label, nums)
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
                if mapped is None:
                    if current is None:
                        self.warn(f"unmapped contest header before "
                                  f"{stripped!r}: {header!r}")
                    i += 1
                    continue
                if current is not None and header == current.raw:
                    # page continuation of the open section (multi-page
                    # write-in list): header + "Vote For N" repeat
                    i += 1
                    continue
                if current is not None:
                    self.flush_contest(current)
                current = Contest(mapped, header, precinct)
                self.contest_count += 1
                i += 1
                continue

            if i in header_lines or junk(stripped) or current is None:
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
                # section; close it here so page furniture (e.g. the repeated
                # precinct-name line) between sections can't be mistaken
                # for a data row of the open contest
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
                self.warn(f"retention-style Yes/No row in {header!r} - not "
                          f"expected in a primary: {stripped!r}")
                i += 1
                continue

            # candidate row
            head, row_party, allnums = split_candidate(tokens)
            name = clean_name(" ".join(head), titlecase=True)
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
    precinct_mode = first.startswith("Precinct Summary Report")
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
    if P.ct_checks:
        for label, ct, exp, ou in P.ct_checks[:20]:
            print(f"CONTEST TOTALS {label}: {ct} != {exp} + over/under {ou}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))