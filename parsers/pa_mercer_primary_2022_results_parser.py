#!/usr/bin/env python3
"""
Parse Mercer County PA 2022 General Primary (May 17, 2022) precinct results.

Source PDFs (Electionware, pdftotext -layout):

- "Mercer PA 2022 Primary PRECINCT.pdf" - per-precinct "Summary Results
  Report" (90 precincts, generated 06/03/2022 12:59 PM).  Carries all DEM
  contests and every REP contest EXCEPT the Republican U.S. Senate race,
  which the county removed from the tabulation pending the June recount.
- "Mercer PA 2022 Primary .pdf" - per-precinct "Summary Results Report"
  for the GOP U.S. Senate RECOUNT only (90 pages, 06/03/2022 1:03 PM).
  These are the post-recount per-precinct Republican Senate rows; their
  sums match the countywide "Mercer PA 2022 Primary SUMMARY.Recount.pdf".

The parser takes BOTH per-precinct files and merges them:
  python parsers/pa_mercer_primary_2022_results_parser.py \
      <precinct.pdf|txt> <recount.pdf|txt> <output.csv>

Format notes:
- Contest headers carry the ballot party as a prefix ("DEM GOVERNOR");
  the party goes into every row of the contest, including "Write-In
  Totals" rows (primary convention - empty-party rows collide between
  the DEM and REP sections of one office in duplicate_entries).
- Candidate rows: NAME  TOTAL  Election Day  Absentee  Provisional.
  Per the 2022-primary convention the Absentee column maps to `mail`.
- Statistics block: "Ballots Cast" labels print WITHOUT their numbers in
  the -layout text of these 2022 reports (the four numbers sit on the
  line ABOVE the label); the parser carries that pending numbers line.
  RV/BC meta rows: total + per-party (DEM/REP) rows with party set (2020
  precedent, as in the Centre/Blair 2022 primary files); all-zero
  NONPARTISAN RV/BC rows are dropped, per the Centre 2022 convention.
- "DEM PRECINCT COMMITTEEMAN <precinct>" / "...COMMITTEEWOMAN..." local
  party contests are included as printed (office carries the
  municipality, party column DEM/REP), like the only other 2022 primary
  county with such contests (Butler).
- Office names: statewide races map to the standard names; the state
  committee contests map to the anchor's "Member of Democratic State
  Committee" / "Member of Republican State Committee".
- "Write-In Totals" -> candidate "Write Ins" with the contest party.
- Countywide candidate names are title-cased via NAME_MAP, matching the
  county's 2022 general file (NAME_MAP covers every candidate in the
  statewide contests; per-precinct committee candidates keep their
  printed ALL-CAPS casing, as in Butler's 2022 primary file).
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COUNTY = "Mercer"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

STAT_LABEL = re.compile(r"^(Registered Voters|Ballots Cast) - (\w+)\b")
TURNOUT = re.compile(r"^Voter Turnout", re.I)
VOTE_FOR = re.compile(r"^Vote For (\d+)$", re.I)
STAT_LINE = re.compile(r"^STATISTICS$")
WRITE_IN = re.compile(r"^Write-In Totals\b", re.I)
TVC = re.compile(r"^Total Votes Cast\b", re.I)
CT = re.compile(r"^Contest Totals\b", re.I)
NUM_TOKEN = re.compile(r"^\d[\d,]*$")
PCT_LINE = re.compile(r"^[\d,\.]+%$")
PARTY_PREFIX = re.compile(r"^(DEM|REP)\s+(.+)$")

# page furniture / report decoration
FURNITURE = re.compile(
    r"^(Summary Results Report\b"
    r"|.*\bOFFICIAL RESULTS$"
    r"|2022 Federal\b.*"
    r"|May 17, 2022\b.*"
    r"|US Senate Recount$"
    r"|Precinct Summary -"
    r"|Election Summary -"
    r"|Report generated with\b"
    r"|(?:Election Day|Absentee/? ?Early) Precincts Reporting\b.*"
    r"|Precincts Complete\b.*"
    r"|Precincts Partially Reported\b.*"
    r"|Precincts Reporting\b.*"
    r")",
    re.I)
COLUMN_WORDS = {"TOTAL", "ELECTION", "DAY", "ABSENTEE", "PROVISIONAL",
                "PROVISION", "AL"}


def title_case(s):
    """CLARK BORO -> Clark Boro; COOLSPRING TWP -> Coolspring Twp."""
    out = []
    for tok in s.split():
        if tok.upper() in ("NW", "NE", "SW", "SE", "TWP"):
            out.append(tok.upper())
        else:
            out.append(tok.title())
    return " ".join(out)


def map_office(header):
    """(office, district) from the DEM/REP-stripped contest header."""
    u = header.strip().upper()
    if u == "UNITED STATES SENATOR":
        return "U.S. Senate", ""
    if u == "GOVERNOR":
        return "Governor", ""
    if u == "LIEUTENANT GOVERNOR":
        return "Lieutenant Governor", ""
    m = re.match(r"^REPRESENTATIVE IN CONGRESS DISTRICT (\d+)$", u)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^SENATOR IN THE GENERAL ASSEMBLY DISTRICT (\d+)$", u)
    if m:
        return "State Senate", m.group(1)
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY DISTRICT (\d+)$",
                 u)
    if m:
        return "State House", m.group(1)
    if u == "MEMBER OF THE DEMOCRATIC STATE COMMITTEE":
        return "Member of Democratic State Committee", ""
    if u == "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        return "Member of Republican State Committee", ""
    m = re.match(r"^PRECINCT COMMITTEE(MAN|WOMAN)\s+(.+)$", u)
    if m:
        word = "Committeeman" if m.group(1) == "MAN" else "Committeewoman"
        return (f"Precinct {word} "
                f"{title_case(m.group(2).strip())}"), ""
    raise ValueError(f"Unrecognized contest header: {header!r}")


# Title-cased candidate names, per the county's 2022 general parser.
NAME_MAP = {
    "ALEX KHALIL": "Alex Khalil",
    "CONOR LAMB": "Conor Lamb",
    "MALCOLM KENYATTA": "Malcolm Kenyatta",
    "JOHN FETTERMAN": "John Fetterman",
    "KATHY BARNETTE": "Kathy Barnette",
    "MEHMET OZ": "Mehmet Oz",
    "GEORGE BOCHETTO": "George Bochetto",
    "JEFF BARTOS": "Jeff Bartos",
    "DAVE MCCORMICK": "Dave McCormick",
    "SEAN GALE": "Sean Gale",
    "CARLA SANDS": "Carla Sands",
    "JOSH SHAPIRO": "Josh Shapiro",
    "LOU BARLETTA": "Lou Barletta",
    "DOUGLAS V MASTRIANO": "Douglas V. Mastriano",
    "NCHE ZAMA": "Nche Zama",
    "DAVE WHITE": "Dave White",
    "MELISSA HART": "Melissa Hart",
    "BILL MCSWAIN": "Bill McSwain",
    "CHARLIE GEROW": "Charlie Gerow",
    "JOE GALE": "Joe Gale",
    "JAKE CORMAN": "Jake Corman",
    "AUSTIN DAVIS": "Austin Davis",
    "RAY SOSA": "Ray Sosa",
    "BRIAN SIMS": "Brian Sims",
    "CLARICE SCHILLINGER": "Clarice Schillinger",
    "JAMES EARL JONES": "James Earl Jones",
    "RICK SACCONE": "Rick Saccone",
    "JOHN BROWN": "John Brown",
    "CHRIS FRYE": "Chris Frye",
    "JEFF COLEMAN": "Jeff Coleman",
    "RUSS DIAMOND": "Russ Diamond",
    "CARRIE LEWIS DELROSSO": "Carrie Lewis DelRosso",
    "TEDDY DANIELS": "Teddy Daniels",
    "DAN PASTORE": "Dan Pastore",
    "RICK TELESZ": "Rick Telesz",
    "MIKE KELLY": "Mike Kelly",
    "RIANNA CZECH": "Rianna Czech",
    "MICHELE BROOKS": "Michele Brooks",
    "TIMOTHY M MCGONIGLE": "Timothy M McGonigle",
    "MITCHEL HENDERSON": "Mitchel Henderson",
    "TIM BONNER": "Tim Bonner",
    "PARKE WENTLING": "Parke Wentling",
    "DIANE SYPHRIT": "Diane Syphrit",
    "GAGE BARTHOLOMEW": "Gage Bartholomew",
    "JARED R GRANDY": "Jared R Grandy",
    "JIM RICHARDSON": "Jim Richardson",
    "GERALD M STUMPFF II": "Gerald M Stumpff II",
    "ANN COLEMAN": "Ann Coleman",
    "JULIE K SWARTFAGER": "Julie K Swartfager",
}


def is_junk(s):
    if not s:
        return True
    if FURNITURE.match(s):
        return True
    t = s.upper().split()
    if t and all(w in COLUMN_WORDS for w in t):
        return True
    return False


def is_number_line(s):
    toks = s.split()
    return len(toks) == 4 and all(NUM_TOKEN.match(t) for t in toks)


def int_or(s):
    return int(s.replace(",", ""))


class Contest:
    def __init__(self, office, district, party, precinct):
        self.office, self.district, self.party = office, district, party
        self.precinct = precinct
        self.cand = []            # (candidate, [t, ed, ab, pr])
        self.tv = None            # Total Votes Cast
        self.ct = None            # Contest Totals
        self.writeins = None      # [t, ed, ab, pr]

    def label(self):
        return f"{self.precinct}|{self.office}|{self.party}"

    def check(self, recon):
        if self.tv is None:
            recon.append(f"{self.label()}: no Total Votes Cast row")
            return 0
        s = [sum(v[k] for _, v in self.cand) for k in range(4)]
        if self.writeins is not None:
            s = [s[k] + self.writeins[k] for k in range(4)]
        if list(s) != list(self.tv):
            recon.append(
                f"{self.label()}: candidate+writein sum {s} != Total Votes "
                f"Cast {self.tv}")
        return self.tv


def parse_file(text, stats_mode):
    """Parse one per-precinct Electionware report.

    stats_mode: emit Registered Voters / Ballots Cast meta rows (main
    file) or skip them (recount file - its ballot-cast figures are
    re-scan-only and don't match the main report's RV/BC; those come
    from the main report alone).
    """
    lines = [l.rstrip() for l in text.split("\n")]
    n = len(lines)

    # prepass: precinct names (non-blank line above each "STATISTICS")
    names = set()
    for k, l in enumerate(lines):
        if STAT_LINE.match(l.strip()):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                names.add(lines[j].strip())

    # prepass: contest header lines (the line above each "Vote For N");
    # they stream past BEFORE the Vote For line opens the contest, so they
    # are skipped when encountered and re-read from lines[] at that point
    header_idx = set()
    for k, l in enumerate(lines):
        if VOTE_FOR.match(l.strip()):
            j = k - 1
            while j >= 0 and (not lines[j].strip()
                              or is_junk(lines[j].strip())):
                j -= 1
            if j >= 0:
                header_idx.add(j)

    rows = []        # dicts: precinct, office, district, party,
                     # candidate, votes, election_day, mail, provisional
    recon = []
    warnings = []
    contest_count = 0
    precinct_count = 0
    seen_precincts = set()
    pending = None       # numbers-only line awaiting its stat label
    precinct = None
    cur = None

    def emit(office, district, party, candidate, votes, ed, mail, prov):
        rows.append({
            "precinct": precinct,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        })

    def close_current():
        nonlocal cur
        if cur is not None:
            cur.check(recon)
            for name, v in cur.cand:
                up = name.upper()
                emit(cur.office, cur.district, cur.party,
                     NAME_MAP.get(up, name), str(v[0]), str(v[1]),
                     str(v[2]), str(v[3]))
            if cur.writeins is not None:
                emit(cur.office, cur.district, cur.party, "Write Ins",
                     str(cur.writeins[0]), str(cur.writeins[1]),
                     str(cur.writeins[2]), str(cur.writeins[3]))
            cur = None

    i = 0
    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        if STAT_LINE.match(s):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            close_current()
            precinct = lines[j].strip() if j >= 0 else None
            if precinct is not None and precinct not in seen_precincts:
                seen_precincts.add(precinct)
                precinct_count += 1
            i += 1
            continue

        if s in names:
            # precinct-name banner (page top / first page)
            i += 1
            continue

        if i in header_idx:
            # contest header consumed by the "Vote For N" handler below
            i += 1
            continue

        # statistics rows (no contest open)
        if cur is None:
            m = STAT_LABEL.match(s)
            if m:
                inline = [t for t in s.split()[len(m.group(1).split()) + 1:]
                          if NUM_TOKEN.match(t)]
                nums = None
                if inline:
                    nums = [int_or(t) for t in inline]
                elif pending is not None:
                    nums = pending
                    pending = None
                label_word, group_word = m.group(1), m.group(2)
                if nums is None:
                    warnings.append(f"{precinct}: no numbers for {s!r}")
                    i += 1
                    continue
                if group_word.upper() == "NONPARTISAN":
                    if any(nums):
                        warnings.append(
                            f"{precinct}: non-zero NONPARTISAN row {s!r}")
                elif stats_mode:
                    party = {
                        "DEMOCRATIC": "DEM",
                        "REPUBLICAN": "REP",
                    }.get(group_word.upper(), "")
                    if label_word == "Registered Voters":
                        office, vals = "Registered Voters", (nums[0], "", "",
                                                             "")
                    else:
                        # Ballots Cast: Total / per-party / - Blank
                        office = ("Ballots Cast" if group_word.upper()
                                  in ("TOTAL", "DEMOCRATIC", "REPUBLICAN")
                                  else "Ballots Cast - Blank")
                        if len(nums) == 4:
                            vals = (nums[0], nums[1], nums[2], nums[3])
                        else:
                            vals = (nums[0], "", "", "")
                            warnings.append(f"{precinct}: BC row with "
                                            f"{len(nums)} numbers: {s!r}")
                    emit(office, "", party, "", *vals)
                i += 1
                continue

        # vote-for header -> new contest
        m = VOTE_FOR.match(s)
        if m:
            j = i - 1
            while j >= 0 and (not lines[j].strip()
                              or is_junk(lines[j].strip())):
                j -= 1
            header = lines[j].strip() if j >= 0 else ""
            pm = PARTY_PREFIX.match(header.strip())
            if pm:
                contest_party, rest = pm.groups()
                office, district = map_office(rest)
            else:
                warnings.append(f"{precinct}: header without party: "
                                f"{header!r}")
                office, district, contest_party = "", "", ""
            close_current()
            cur = Contest(office, district, contest_party, precinct)
            contest_count += 1
            i += 1
            continue

        if cur is not None:
            if is_junk(s):
                # column headers ("TOTAL Election Absentee Provisional"),
                # page furniture crossing into an open section
                i += 1
                continue
            if WRITE_IN.match(s):
                nums = [int_or(t) for t in s.split() if NUM_TOKEN.match(t)]
                cur.writeins = nums if len(nums) == 4 else None
                if len(nums) != 4:
                    warnings.append(f"{precinct}: writein row {s!r}")
                i += 1
                continue
            if TVC.match(s):
                nums = [int_or(t) for t in s.split() if NUM_TOKEN.match(t)]
                cur.tv = nums if len(nums) == 4 else None
                if len(nums) != 4:
                    warnings.append(f"{precinct}: TVC row {s!r}")
                i += 1
                continue
            if CT.match(s):
                nums = [int_or(t) for t in s.split() if NUM_TOKEN.match(t)]
                cur.ct = nums if len(nums) == 4 else None
                i += 1
                continue
            # candidate row
            toks = s.split()
            nums = [t for t in toks if NUM_TOKEN.match(t)]
            name_toks = [t for t in toks if not NUM_TOKEN.match(t)]
            if len(nums) == 4 and name_toks:
                name = " ".join(name_toks)
                v = [int_or(t) for t in nums]
                cur.cand.append((name, v))
                i += 1
                continue
            warnings.append(f"{precinct}: unmatched data line: {s!r}")
            i += 1
            continue

        # no contest open: numbers-only line + page furniture
        if is_number_line(s) and pending is None:
            pending = [int_or(t) for t in s.split()]
            i += 1
            continue
        if TURNOUT.match(s) or PCT_LINE.match(s):
            i += 1
            continue
        if is_junk(s):
            i += 1
            continue
        warnings.append(f"{precinct}: unmatched line: {s!r}")
        i += 1

    close_current()
    return rows, precinct_count, recon, warnings, contest_count


def text_from_input(src):
    if src.lower().endswith(".pdf"):
        out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False)
        out.close()
        subprocess.run(["pdftotext", "-layout", src, out.name], check=True,
                       capture_output=True, text=True)
        with open(out.name, encoding="utf-8") as fh:
            return fh.read()
    with open(src, encoding="utf-8") as fh:
        return fh.read()


def main(argv):
    if len(argv) != 4:
        sys.exit(f"Usage: {Path(argv[0]).name} "
                 f"<precinct.pdf|txt> <recount.pdf|txt> <output.csv>")

    main_rows, pc, recon, warns, cc = parse_file(
        text_from_input(argv[1]), True)
    rec_rows, rpc, rrecon, rwarns, rcc = parse_file(
        text_from_input(argv[2]), False)

    for r in main_rows + rec_rows:
        r["county"] = COUNTY

    rows = main_rows + rec_rows
    out = Path(argv[3])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows ({pc} main precincts, {rpc} recount "
          f"precincts) -> {out}")
    print(f"contests parsed: main {cc} + recount {rcc} = {cc + rcc}")
    for w in warns[:20]:
        print("WARN:", w)
    if len(warns) > 20:
        print(f"... {len(warns) - 20} more warnings")
    for w in rwarns[:20]:
        print("RECOUNT WARN:", w)
    if len(rwarns) > 20:
        print(f"... {len(rwarns) - 20} more warnings")
    if recon or rrecon:
        for msg in (recon + rrecon)[:40]:
            print("RECON:", msg)
        print(f"Total-Votes-Cast check: {len(recon) + len(rrecon)} "
              "MISMATCHES")
    else:
        print("Total-Votes-Cast check: all contests reconcile")
    return 0


if __name__ == "__main__":
    main(sys.argv)