#!/usr/bin/env python3
"""Venango County PA 2022 General (November 8, 2022) — precinct results.

Source: "Venango PA Precinct Results November 2022" (Electionware
"Precinct Summary Report - No Write-ins", 88 pages, pdftotext -layout).

Format notes (Electionware general, no write-in detail):

- Contest headers carry no party prefix (unlike the primary files); the
  general file's candidate rows carry no party either, so party is mapped
  from the closed candidate list (verified against the ENR-derived
  2022/20221108__pa__general__county.csv).
- Data-row columns: TOTAL, VOTE %, Election Day, Mail, Provisional; the
  percentage token (contains '%') is dropped.  Statistics/Overvotes/
  Undervotes/Contest-Totals rows carry no percentage token.
- "Registered Voters - Total" prints only a total (no breakdown);
  "Ballots Cast - Total" / "Ballots Cast - Blank" carry the
  ED/Mail/Provisional breakdown.  "Voter Turnout - Total" is dropped.
- "Write-In Totals" aggregate row -> candidate "Write Ins" (party "").
- The three per-township ballot questions ("Clinton Question" /
  "Irwin Question" / "Victory Question") are Yes/No contests; emitted
  with office as printed and party "".
- "Representative in the General Assembly" is the unopposed State House
  District 64 contest (R. Lee James; ENR confirms countywide single
  district), so district "64" is applied.
- Reconciliation: candidate+write-in rows must equal "Total Votes Cast";
  "Contest Totals" must equal Total Votes Cast + Overvotes + Undervotes.

Usage: pa_venango_general_2022_results_parser.py <input.txt|pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile

COUNTY = "Venango"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "early_voting",
              "provisional"]

PARTY_MAP = {
    "JOHN FETTERMAN": "DEM",
    "MEHMET OZ": "REP",
    "ERIK GERHARDT": "LIB",
    "RICHARD L WEISS": "GRN",
    "DANIEL WASSMER": "KEY",
    "JOSH SHAPIRO": "DEM",
    "DOUGLAS V MASTRIANO": "REP",
    "MATT HACKENBURG": "LIB",
    "CHRISTINA DIGIULIO": "GRN",
    "JOE SOLOSKI": "KEY",
    "DAN PASTORE": "DEM",
    "MIKE KELLY": "REP",
    "MIKE MOLESEVICH": "DEM",
    "GLENN GT THOMPSON": "REP",
    "R LEE JAMES": "REP",
}

PAGE_HEAD = re.compile(r"^(?:\x0c)?Precinct Summary Report", re.I)
DATE_LINE = re.compile(r"^November 8, ?2022\b", re.I)
FOOTER = re.compile(r"^(Report generated with Electionware\b"
                    r"|Precinct Summary No Write-in Names\b)", re.I)
TITLE_LINE = re.compile(r"^Venango County General", re.I)
VOTE_FOR = re.compile(r"^Vote For \d+", re.I)
TOTAL_VOTES_CAST = re.compile(r"^Total Votes Cast\b", re.I)
CONTEST_TOTALS = re.compile(r"^Contest Totals\b", re.I)
OVER_UNDER = re.compile(r"^(Over ?votes|Under ?votes)\b", re.I)
WRITE_IN_TOTALS = re.compile(r"^Write-?In Totals\b", re.I)
STAT_LABELS = ("Registered Voters - Total", "Ballots Cast - Total",
               "Ballots Cast - Blank")
TURNOUT = re.compile(r"^Voter Turnout\b", re.I)
DIGITS = re.compile(r"^\d[\d,]*$")
QUESTION = re.compile(r"^\S+ Question$")


def nums_from(tokens):
    """Numeric tokens (commas stripped); '%' percentage tokens dropped."""
    out = []
    for tok in tokens:
        tok = tok.strip()
        if not tok or "%" in tok:
            continue
        if DIGITS.match(tok):
            out.append(int(tok.replace(",", "")))
    return out


def furniture(s):
    return (PAGE_HEAD.match(s) or FOOTER.match(s) or TITLE_LINE.match(s)
            or TURNOUT.match(s))


def map_contest(header):
    """(office, district) from a contest header line."""
    h = re.sub(r"\s+", " ", header.strip())
    m = re.match(r"^Representative in Congress District (\d+)$", h)
    if m:
        return "U.S. House", m.group(1)
    if h == "United States Senator":
        return "U.S. Senate", ""
    if h == "Governor":
        return "Governor", ""
    if h == "Representative in the General Assembly":
        return "State House", "64"
    if QUESTION.match(h):
        return h, ""
    return h, ""


class Contest:
    def __init__(self, mapped, precinct):
        self.office, self.district = mapped
        self.precinct = precinct
        self.cand_sum = [0, 0, 0, 0]
        self.expected = None
        self.over_under = [0, 0, 0, 0]
        self.ct = None

    def label(self):
        return f"{self.precinct}|{self.office}|{self.district}"


class Parser:
    def __init__(self, county):
        self.county = county
        self.rows = []
        self.warnings = []
        self.recon = []
        self.contest_count = 0

    def warn(self, msg):
        self.warnings.append(msg)

    def emit(self, office, district, precinct, party, candidate, vals):
        r = dict.fromkeys(FIELDNAMES, "")
        r["county"] = self.county
        r["precinct"] = precinct or ""
        r["office"] = office
        r["district"] = district
        r["party"] = party
        r["candidate"] = candidate
        (r["votes"], r["election_day"], r["early_voting"],
         r["provisional"]) = [str(v) for v in vals]
        self.rows.append(r)

    def emit_meta(self, precinct, office, nums):
        vals = [str(nums[0])] + [str(x) for x in nums[1:4]]
        while len(vals) < 4:
            vals.append("")
        r = dict.fromkeys(FIELDNAMES, "")
        r["county"] = self.county
        r["precinct"] = precinct or ""
        r["office"] = office
        (r["votes"], r["election_day"], r["early_voting"],
         r["provisional"]) = vals
        self.rows.append(r)

    def flush_contest(self, contest):
        if contest is None:
            return
        label = contest.label()
        if contest.expected is not None:
            s = contest.cand_sum[0]
            if s != contest.expected[0]:
                self.recon.append(
                    (label, f"rows sum {s} != Total Votes Cast "
                            f"{contest.expected[0]}"))
            for k, field in enumerate(("ED", "mail", "provisional")):
                if contest.cand_sum[k + 1] != contest.expected[k + 1]:
                    self.recon.append(
                        (label, f"{field} sum {contest.cand_sum[k + 1]} != "
                                f"{contest.expected[k + 1]}"))
        if contest.ct is not None:
            if contest.expected is not None:
                tv = [contest.expected[0] + contest.over_under[0],
                      contest.expected[1] + contest.over_under[1],
                      contest.expected[2] + contest.over_under[2],
                      contest.expected[3] + contest.over_under[3]]
                if contest.ct != tv:
                    self.recon.append(
                        (label, f"Contest Totals {contest.ct} != "
                                f"Total Votes Cast {contest.expected} + "
                                f"over/under {contest.over_under}"))

    def run(self, text):
        lines = [l.rstrip() for l in text.split("\n")]
        n = len(lines)

        # contest header lines: nearest non-blank, non-furniture line above
        # each "Vote For N" (contest headers are digit-free, so furniture
        # must be excluded by pattern, not by digit-ness)
        header_of = {}
        header_lines = set()
        for k, l in enumerate(lines):
            if VOTE_FOR.match(l.strip()):
                j = k - 1
                while j >= 0 and (not lines[j].strip()
                                  or furniture(lines[j].strip())):
                    j -= 1
                if j >= 0:
                    header_of[k] = lines[j].strip()
                    header_lines.add(j)

        precinct = None
        current = None
        i = 0
        while i < n:
            raw = lines[i]
            stripped = raw.strip()
            if not stripped:
                i += 1
                continue

            # page furniture: the precinct name is the first non-blank,
            # non-furniture line after the "November 8,2022" page header
            if PAGE_HEAD.match(stripped) or TITLE_LINE.match(stripped):
                i += 1
                continue
            if FOOTER.match(stripped):
                if current is not None:
                    self.flush_contest(current)
                    current = None
                i += 1
                continue
            if DATE_LINE.match(stripped):
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and not furniture(lines[j].strip()):
                    name = lines[j].strip()
                    if current is not None:
                        self.flush_contest(current)
                        current = None
                    precinct = name
                    i = j + 1      # skip past the precinct-name line
                    continue
                i += 1
                continue

            if VOTE_FOR.match(stripped):
                header = header_of.get(i, "")
                if header:
                    if current is not None:
                        self.flush_contest(current)
                    current = Contest(map_contest(header), precinct)
                    self.contest_count += 1
                i += 1
                continue

            # contest header line (digit-free but indexed by header_of):
            # never a data row
            if i in header_lines:
                i += 1
                continue

            if TURNOUT.match(stripped):
                i += 1
                continue

            tokens = stripped.split()
            nums = nums_from(tokens)
            if not nums:
                # digit-free furniture inside/outside contests
                # (column-header lines etc.)
                i += 1
                continue
            label_toks = [t for t in tokens if not any(
                ch.isdigit() for ch in t)]

            # statistics rows (appear only outside any contest section)
            if current is None and stripped.startswith(STAT_LABELS[0]):
                self.emit_meta(precinct, "Registered Voters", nums)
                i += 1
                continue
            if current is None and stripped.startswith(STAT_LABELS[1]):
                self.emit_meta(precinct, "Ballots Cast", nums)
                i += 1
                continue
            if current is None and stripped.startswith(STAT_LABELS[2]):
                self.emit_meta(precinct, "Ballots Cast - Blank", nums)
                i += 1
                continue

            if current is None:
                self.warn(f"data row outside any contest: {stripped!r}")
                i += 1
                continue

            if TOTAL_VOTES_CAST.match(stripped):
                current.expected = [int(x) for x in nums[:4]]
                i += 1
                continue
            if CONTEST_TOTALS.match(stripped):
                current.ct = [int(x) for x in nums[:4]]
                self.flush_contest(current)
                current = None
                i += 1
                continue
            if OVER_UNDER.match(stripped):
                kind = ("Overvotes" if stripped.lower().startswith("over")
                        else "Undervotes")
                vals = [int(x) for x in nums[:4]]
                self.emit(current.office, current.district, current.precinct,
                          "", kind, vals)
                current.over_under = [a + b for a, b in
                                      zip(current.over_under, vals)]
                i += 1
                continue
            if WRITE_IN_TOTALS.match(stripped):
                vals = [int(x) for x in nums[:4]]
                self.emit(current.office, current.district, current.precinct,
                          "", "Write Ins", vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
                i += 1
                continue
            if label_toks and label_toks[0] in ("Yes", "No"):
                vals = [int(x) for x in nums[:4]]
                self.emit(current.office, current.district, current.precinct,
                          "", label_toks[0], vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
                i += 1
                continue

            # candidate row: name from the digit-free leading tokens (the
            # percentage token sits between the name and the breakdown)
            name = re.sub(r"\s+", " ", " ".join(label_toks)).strip()
            if name:
                vals = [int(x) for x in nums[:4]]
                party = PARTY_MAP.get(name.upper().replace(".", ""), "")
                if not party:
                    self.warn(f"no party mapping for {name!r} in "
                              f"{current.label()}")
                self.emit(current.office, current.district, current.precinct,
                          party, name.title(), vals)
                current.cand_sum = [a + b for a, b in
                                    zip(current.cand_sum, vals)]
            i += 1

        self.flush_contest(current)
        return self


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for r in rows:
            w.writerow([r[c] for c in FIELDNAMES])
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
    P = Parser(COUNTY).run(text)
    n = write_csv(dst, P.rows)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {P.contest_count}")
    precincts = sorted({r["precinct"] for r in P.rows if r["precinct"]})
    print(f"precincts: {len(precincts)}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))