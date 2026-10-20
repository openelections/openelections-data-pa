#!/usr/bin/env python3
"""
Parse Franklin County PA 2022 Primary (2022-05-17) precinct results.

Source: "Franklin PA 2022 Primary PrecinctDetail.pdf" (Electionware
Summary Results Report, 821 pages / 73 precincts). Each contest block is
3-column (candidate, TOTAL, VOTE %) with no election-day/mail/provisional
breakdown, so the output uses the 7-column header.

GOP U.S. Senate recount: the May 17 election-night detail is PRE-recount.
Franklin supplied "Franklin PA 2022 Primary RecountPrecinctDetail.pdf"
(OFFICIAL RECOUNT RESULTS, 06/02/2022, GOP U.S. Senate race only, 73
precincts) plus a matching summary. This parser replaces the per-precinct
REP U.S. Senate rows with the recount values so county sums match the
certified/post-recount anchor. Pass the recount PDF explicitly (or place
it next to the main PDF; it is auto-detected by name):
    python pa_franklin_primary_2022_results_parser.py <main.pdf> <out.csv> [<recount.pdf>]

The recount source carries no STATISTICS sections, so Registered Voters /
Ballots Cast rows come only from the main detail.

Registered Voters / Ballots Cast: the STATISTICS block reports RV and
Ballots Cast per party (Total/Democratic/Republican/NONPARTISAN) plus
"Ballots Cast - Blank". Emitted per party (McKean/Northumberland 2022
primary precedent), with the Total rows carrying an empty party.

Committeeperson contests (DEM COMMITTEEMAN/COMMITTEEWOMAN and REP
COMMITTEEPERSON <precinct>) are kept as printed with the precinct name in
the district column (Beaver 2022 primary precedent). The per-precinct
party-committee offices use office names Committeeman/Committeewoman/
Committeeperson.

Contest Totals / Total Votes Cast / Overvotes / Undervotes / Not Assigned
lines are not emitted as rows; they are used for internal verification.

Usage:
    python parsers/pa_franklin_primary_2022_results_parser.py <input.pdf> <output.csv> [<recount.pdf>]
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

COUNTY = "Franklin"

# --------------------------------------------------------------------------
# pdftotext extraction
# --------------------------------------------------------------------------


def text_from_pdf(pdf_path: str) -> str:
    proc = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout


# --------------------------------------------------------------------------
# Line patterns
# --------------------------------------------------------------------------

COUNTY_LINE_RE = re.compile(r"^May 17, 2022\s+FRANKLIN COUNTY$")
PAGE_HEADER_PREFIXES = ("Summary Results Report", "Primary Election")
FOOTER_PREFIXES = ("Precinct Summary -", "Election Summary -",
                   "Report generated with Electionware")

STAT_RE = re.compile(
    r"^(Registered Voters|Ballots Cast) - "
    r"(Total|Democratic|Republican|NONPARTISAN|Blank)\s+([\d,]+)$"
)
OFFICE_RE = re.compile(r"^(DEM|REP)\s+([A-Z].*)$")
VOTE_FOR_RE = re.compile(r"^Vote For (\d+)$")
COLHEADER_RE = re.compile(r"^TOTAL(\s+VOTE\s*%)?$")
VOTE_PCT_RE = re.compile(r"^(.*\S)\s+([\d,]+)\s+\d+\.\d+%$")
VOTE_ONLY_RE = re.compile(r"^(.*\S)\s+([\d,]+)$")

STAT_LEVEL_PARTY = {
    ("Registered Voters", "Total"): ("Registered Voters", ""),
    ("Registered Voters", "Democratic"): ("Registered Voters", "DEM"),
    ("Registered Voters", "Republican"): ("Registered Voters", "REP"),
    ("Registered Voters", "NONPARTISAN"): ("Registered Voters", "NONPARTISAN"),
    ("Ballots Cast", "Total"): ("Ballots Cast", ""),
    ("Ballots Cast", "Democratic"): ("Ballots Cast", "DEM"),
    ("Ballots Cast", "Republican"): ("Ballots Cast", "REP"),
    ("Ballots Cast", "NONPARTISAN"): ("Ballots Cast", "NONPARTISAN"),
    ("Ballots Cast", "Blank"): ("Ballots Cast - Blank", ""),
}

PRECINCT_TOKEN_MAP = {"WD": "Ward", "DIST": "District"}


def prettify_franklin_precinct(name: str) -> str:
    """'CHAMBERSBURG WD 1 DIST 1' -> 'Chambersburg Ward 1 District 1'."""
    out = []
    for t in name.split():
        up = t.upper()
        if up in PRECINCT_TOKEN_MAP:
            out.append(PRECINCT_TOKEN_MAP[up])
        elif t.isdigit():
            out.append(t)
        else:
            out.append(t.capitalize())
    return " ".join(out)


ASSEMBLY_DIST_RE = re.compile(r"(\d+)(?:ST|ND|RD|TH)\s+LEGISLATIVE DISTRICT")


def map_contest(party: str, rest: str) -> tuple[str, str]:
    """Map a primary office header (party stripped) to (office, district)."""
    r = rest.strip()
    ru = r.upper()
    if ru == "UNITED STATES SENATOR":
        return "U.S. Senate", ""
    if ru == "GOVERNOR":
        return "Governor", ""
    if ru == "LIEUTENANT GOVERNOR":
        return "Lieutenant Governor", ""
    if ru == "REPRESENTATIVE IN CONGRESS":
        # Franklin County lies entirely in the 13th Congressional District
        # (verified: John Joyce is the only named candidate on either
        # ballot across all 73 precincts; ENR anchor reports only CD-13).
        return "U.S. House", "13"
    if ru.startswith("REPRESENTATIVE IN THE GENERAL ASSEMBLY"):
        m = ASSEMBLY_DIST_RE.search(ru)
        if not m:
            raise ValueError(f"State House header without district: {rest!r}")
        return "State House", str(int(m.group(1)))
    if ru in ("MEMBER OF DEMOCRATIC STATE COMMITTEE",
              "DEMOCRATIC STATE COMMITTEE"):
        return "Member of Democratic State Committee", ""
    if ru in ("MEMBER OF REPUBLICAN STATE COMMITTEE",
              "REPUBLICAN STATE COMMITTEE"):
        return "Member of Republican State Committee", ""
    for office in ("COMMITTEEPERSON", "COMMITTEEWOMAN", "COMMITTEEMAN"):
        if ru == office or ru.startswith(office + " "):
            muni = r[len(office):].strip()
            return (office.capitalize() if office != "COMMITTEEPERSON"
                    else "Committeeperson"), prettify_franklin_precinct(muni)
    raise ValueError(f"Unmapped contest header: {party} {rest!r}")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class Contest:
    def __init__(self, party, office, district, vote_for):
        self.party = party
        self.office = office
        self.district = district
        self.vote_for = vote_for
        self.key = (party, office, district, vote_for)
        self.candidates: list[tuple[str, int]] = []
        self.write_ins: int | None = None       # from Write-In Totals
        self.not_assigned: int | None = None
        self.total_votes_cast: int | None = None
        self.overvotes: int | None = None
        self.undervotes: int | None = None
        self.contest_totals: int | None = None

    def merge_parts(self, other: "Contest") -> None:
        """Absorb a continuation-page duplicate of the same contest.

        A contest whose candidate rows print on one page and whose
        Contest Totals line prints on the next page is seen as two
        Contest objects; Electionware re-prints the office header on
        every page. Candidates/Write-ins must never appear in both.
        """
        for a in ("candidates",):
            if not getattr(self, a):
                setattr(self, a, getattr(other, a))
        for a in ("write_ins", "not_assigned", "total_votes_cast",
                  "overvotes", "undervotes", "contest_totals"):
            mine, theirs = getattr(self, a), getattr(other, a)
            if mine is None:
                setattr(self, a, theirs)
            elif theirs is not None and theirs != mine:
                raise ValueError(
                    f"contest {self.key}: conflicting {a}: {mine} vs {theirs}"
                )


class PrecinctBlock:
    def __init__(self, raw_name):
        self.raw_name = raw_name
        self.name = prettify_franklin_precinct(raw_name)
        self.stats: dict[tuple[str, str], int] = {}
        self.contests: list[Contest] = []


def parse_report(text: str, expect: str | None) -> tuple[dict[str, PrecinctBlock], int]:
    """Parse one Summary Results Report into per-precinct blocks.

    ``expect`` is a label used only in error messages.
    """
    blocks: dict[str, PrecinctBlock] = {}
    duplicate_precincts = 0
    for page in text.split("\f"):
        lines = [ln.strip() for ln in page.split("\n")]
        # Find the county header line; the next non-empty line is the
        # precinct banner (repeated on every page of a precinct block).
        start = None
        for i, ln in enumerate(lines):
            if COUNTY_LINE_RE.match(ln):
                start = i
                break
        if start is None:
            continue
        banner_idx = None
        for j in range(start + 1, len(lines)):
            ln = lines[j]
            if not ln:
                continue
            banner_idx = j
            break
        if banner_idx is None:
            continue
        banner = lines[banner_idx]
        if not re.fullmatch(r"[A-Z][A-Z0-9 .&'-]*", banner):
            raise ValueError(
                f"{expect}: unexpected first line after county header "
                f"(expected precinct banner): {banner!r}"
            )
        blk = blocks.get(banner)
        if blk is None:
            blk = PrecinctBlock(banner)
            blocks[banner] = blk
        else:
            if blk.raw_name != banner:
                raise ValueError(f"{expect}: precinct banner mismatch")
            duplicate_precincts += 1
        parse_precinct_body(blk, lines[banner_idx + 1:], expect)
    return blocks, duplicate_precincts


def parse_precinct_body(blk: PrecinctBlock, lines: list[str], expect: str) -> None:
    cur: Contest | None = None
    for k, ln in enumerate(lines):
        if not ln:
            continue
        if ln.startswith(PAGE_HEADER_PREFIXES) or ln.startswith(FOOTER_PREFIXES):
            continue
        if COUNTY_LINE_RE.match(ln):
            continue
        if ln == "STATISTICS":
            cur = None
            continue
        m = STAT_RE.match(ln)
        if m:
            blk.stats[(m.group(1), m.group(2))] = int(m.group(3).replace(",", ""))
            cur = None
            continue
        if COLHEADER_RE.match(ln):
            continue
        if VOTE_FOR_RE.match(ln):
            continue
        if ln.startswith("Write-In:"):
            # Write-in sub-category detail (Scattered/Invalid/Blank/...).
            continue
        m = OFFICE_RE.match(ln)
        if m:
            # An office header iff the next non-empty line is "Vote For N".
            nxt = ""
            rest = lines[k + 1:]
            for cand in rest:
                if cand:
                    nxt = cand
                    break
            if VOTE_FOR_RE.match(nxt):
                vote_for = int(VOTE_FOR_RE.match(nxt).group(1))
                office, district = map_contest(m.group(1), m.group(2))
                new = Contest(m.group(1), office, district, vote_for)
                if blk.contests and blk.contests[-1].key == new.key:
                    # Continuation page re-printing a contest whose rows
                    # appeared on the previous page (or vice versa).
                    blk.contests[-1].merge_parts(new)
                else:
                    blk.contests.append(new)
                cur = blk.contests[-1]
                continue
        m = VOTE_PCT_RE.match(ln)
        if not m:
            m = VOTE_ONLY_RE.match(ln)
        if m:
            head = re.sub(r"\s+", " ", m.group(1)).strip()
            val = int(m.group(2).replace(",", ""))
            if head == "Contest Totals":
                cur.contest_totals = val
                continue
            if head.startswith("Total Votes Cast"):
                cur.total_votes_cast = val
                continue
            if head == "Overvotes":
                cur.overvotes = val
                continue
            if head == "Undervotes":
                cur.undervotes = val
                continue
            if head == "Not Assigned":
                cur.not_assigned = val
                continue
            if head == "Write-In Totals":
                cur.write_ins = val
                continue
            if cur is None:
                raise ValueError(
                    f"{expect} {blk.raw_name}: vote row outside a contest: {ln!r}"
                )
            cur.candidates.append((head, val))
            continue
    return


# --------------------------------------------------------------------------
# Row emission
# --------------------------------------------------------------------------

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes"]


def rows_for_report(blocks: dict[str, PrecinctBlock]) -> list[dict]:
    rows = []
    for name in blocks:
        blk = blocks[name]
        for office, district, party, candidate, votes in rows_for_block(blk):
            rows.append({
                "county": COUNTY,
                "precinct": blk.name,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": votes,
            })
    return rows


def rows_for_block(blk: PrecinctBlock):
    out = []
    for (kind, level), val in sorted(blk.stats.items()):
        office, party = STAT_LEVEL_PARTY[(kind, level)]
        out.append((office, "", party, "", val))
    for c in blk.contests:
        for cand, v in c.candidates:
            out.append((c.office, c.district, c.party, cand, v))
        if c.write_ins is not None:
            out.append((c.office, c.district, c.party, "Write-ins",
                        c.write_ins))
    return out


def merge_recount(main_blocks, recount_blocks):
    """Replace REP U.S. Senate contest rows with OFFICIAL RECOUNT values.

    Returns (n_precincts_replaced, per_candidate_delta_vs_main).
    """
    replaced = 0
    delta: dict[str, int] = {}
    for name, rc in recount_blocks.items():
        blk = main_blocks.get(name)
        if blk is None:
            raise ValueError(f"Recount block {name!r} not in main report")
        senate = [c for c in blk.contests
                  if c.party == "REP" and c.office == "U.S. Senate"]
        if len(senate) != 1:
            raise ValueError(f"{blk.raw_name}: expected 1 REP U.S. Senate contest")
        old = senate[0]
        rc_senate = [c for c in rc.contests
                     if c.party == "REP" and c.office == "U.S. Senate"]
        if len(rc_senate) != 1:
            raise ValueError(f"Recount: {rc.raw_name} has no REP U.S. Senate contest")
        new = rc_senate[0]
        byname = dict(old.candidates)
        if old.write_ins is not None:
            byname["Write-ins"] = old.write_ins
        newbyname = dict(new.candidates)
        if new.write_ins is not None:
            newbyname["Write-ins"] = new.write_ins
        for cand, v in newbyname.items():
            if cand in byname:
                delta[cand] = delta.get(cand, 0) + v - byname[cand]
            else:
                delta[cand] = delta.get(cand, 0) + v
        old.candidates = new.candidates
        old.write_ins = new.write_ins
        old.total_votes_cast = new.total_votes_cast
        old.overvotes = new.overvotes
        old.undervotes = new.undervotes
        old.contest_totals = new.contest_totals
        replaced += 1
    return replaced, delta


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def main(argv):
    if len(argv) < 3 or len(argv) > 4:
        sys.exit("Usage: pa_franklin_primary_2022_results_parser.py "
                 "<main.pdf> <out.csv> [<recount.pdf>]")
    main_pdf, out_csv = argv[1], argv[2]
    recount_pdf = argv[3] if len(argv) == 4 else None
    if recount_pdf is None:
        guess = Path(main_pdf).parent / "Franklin PA 2022 Primary RecountPrecinctDetail.pdf"
        if guess.exists():
            recount_pdf = str(guess)

    main_blocks, dup = parse_report(text_from_pdf(main_pdf), "main")
    print(f"Main report: {len(main_blocks)} precincts "
          f"({dup} continuation pages)")

    recount_info = None
    if recount_pdf:
        recount_blocks, _ = parse_report(text_from_pdf(recount_pdf), "recount")
        replaced, delta = merge_recount(main_blocks, recount_blocks)
        print(f"Recount: replaced REP U.S. Senate in {replaced} precincts; "
              f"delta vs pre-recount: {delta}")

    rows = []
    for name in main_blocks:
        rows.extend(rows_for_report({name: main_blocks[name]}))
    with open(out_csv, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows across {len(main_blocks)} precincts "
          f"to {out_csv}")


if __name__ == "__main__":
    main(sys.argv)