#!/usr/bin/env python3
"""Parse Washington County PA 2022 General Primary results (precinct level).

Source: ``Washington County Official Results Precinct Summary 2022 Primary
.pdf`` (Electionware "Summary Results Report", Official Results with
Undated Abs/Mail, canvassed 05/27/2022, 1980 pages, 180 precincts) -- the
same line-based Electionware family as this county's 2023 primary report,
parsed via the shared ``electionware_txt`` (pdftotext -layout) engine in
the manner of ``pa_washington_primary_2023_results_parser.py``.

Contests (identical set for both parties except the committee seats):
U.S. Senate, Governor, Lieutenant Governor, Senator in the General
Assembly (SD-46 only), Representative in Congress (CD-14 only),
Representative in the General Assembly (15/39/40/46/48/50), Member of
the [Democratic/Republican] State Committee, and per-precinct Member of
[Democratic/Republican] County Committee - Man/Woman <precinct>.

Primary-specific handling:
  - Contest headers carry the party as a prefix ("Dem US SENATOR" /
    "Rep REPRESENTATIVE IN CONGRESS").  A preprocessor strips the prefix
    off each contest-header line and records the contest's party in file
    order; a normalize_office wrapper embeds the party in the office name
    and it is stamped onto every candidate row afterwards -- including
    Write-ins/Overvotes/Undervotes rows (party-empty write-in rows fail
    duplicate_entries in primaries).  There are no ballot questions, so
    nothing stays party-empty.
  - Per-party statistics labels ("Registered Voters - Democratic",
    "Ballots Cast - Republican", ...) are dropped so the engine keeps
    only the Total/Blank statistics rows (same convention as this
    county's 2022 general and 2023 primary files).
  - Office names are normalized per repo convention: U.S. Senate,
    Governor, Lieutenant Governor, U.S. House (district 14 from the
    county summary/report roster -- only CD-14 is on Washington's
    ballot), State Senate (district 46, only SD-46 on the ballot),
    State House N, "Member of Democratic/Republican State Committee" as
    printed on the ENR anchor, and the per-precinct county committee
    races keep the printed "Member of ... County Committee - Man/Woman
    <Precinct>" name as their office (Blair 2023 convention).
  - "Write-In Totals" rows are emitted as candidate "Write Ins" with the
    contest party; the named "Write-In: ..." detail rows below are a
    breakdown of that aggregate and are skipped.
  - NO post-recount data: the official canvass file predates the June
    2022 Republican U.S. Senate recount.  Any residual vs the
    post-recount ENR anchor in that contest is documented in the ticket,
    not invented.

Usage:
    python parsers/pa_washington_primary_2022_results_parser.py \\
        <input.pdf|input.txt> <output.csv>
"""

import csv
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_txt import FIELDNAMES, TxtConfig, parse_txt  # noqa: E402
import pa_washington_general_2023_results_parser as g23  # noqa: E402

COUNTY = "Washington"

MARK = "\x01"  # party marker appended to office names, stripped post-parse

CONGRESS_DIST = "14"  # only the 14th Congressional District is on the ballot
SENATE_DIST = "46"  # only the 46th Senatorial District is on the ballot

VOTEFOR_LINE_RE = re.compile(r"^\s*vote for \d+\s*$", re.IGNORECASE)
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)
PARTY_STAT_RE = re.compile(
    r"^\s*(?:Registered Voters|Ballots Cast) - "
    r"(?:Democratic|Republican|NONPARTISAN)\b",
    re.IGNORECASE,
)
STATEHOUSE_RE = re.compile(
    r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)\s*(?:ST|ND|RD|TH)?\s+DISTRICT$",
    re.IGNORECASE,
)
COUNTY_COMMITTEE_RE = re.compile(
    r"^MEMBER OF (DEMOCRATIC|REPUBLICAN) COUNTY COMMITTEE - (MAN|WOMAN) (.+)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Header -> (office, district) for a party-stripped contest header.
# ---------------------------------------------------------------------------


def title_case_name(name: str) -> str:
    """ALL-CAPS candidate names -> Title Case.  Handles apostrophes
    (O'CONNELL -> O'Connell), hyphens (STOUT-BASHIOUM -> Stout-Bashioum),
    Mc prefixes (MCCORMICK -> McCormick, MCSWAIN -> McSwain), initial
    tokens (J D MARTIN -> J. D. Martin) and keeps JR/SR/II/III as printed,
    mirroring this county's 2022 general parser."""
    out = []
    for w in name.split():
        up = w.upper()
        if up in ("JR", "SR", "II", "III"):
            out.append(up)
        elif len(w) == 1:
            out.append(w.capitalize() + ".")
        else:
            t = w.title()  # apostrophe/hyphen aware
            if len(t) >= 4 and t.lower().startswith("mc"):
                t = "Mc" + t[2:].capitalize()
            out.append(t)
    return " ".join(out)


def map_header(header: str):
    """Map a party-stripped contest header to (office, district)."""
    h = re.sub(r"\s+", " ", header.strip()).upper()

    if h == "US SENATOR":
        return ("U.S. Senate", "")
    if h == "GOVERNOR":
        return ("Governor", "")
    if h == "LIEUTENANT GOVERNOR":
        return ("Lieutenant Governor", "")
    if h == "REPRESENTATIVE IN CONGRESS":
        return ("U.S. House", CONGRESS_DIST)
    if h == "SENATOR IN THE GENERAL ASSEMBLY":
        return ("State Senate", SENATE_DIST)

    m = STATEHOUSE_RE.match(h)
    if m:
        return ("State House", m.group(1))

    m = re.match(
        r"^MEMBER OF (?:THE )?(DEMOCRATIC|REPUBLICAN) STATE COMMITTEE$", h
    )
    if m:
        return ("Member of %s State Committee" % m.group(1).capitalize(), "")

    m = COUNTY_COMMITTEE_RE.match(h)
    if m:
        # Office keeps the printed party/seat words (Blair 2023 keeps the
        # printed "Member of ... County Committee" contest name as the
        # office; here the MAN/WOMAN seat marker is also kept):
        return (
            "Member of %s County Committee - %s %s"
            % (m.group(1).capitalize(), m.group(2).capitalize(),
               g23.prettify_precinct(m.group(3))),
            "",
        )

    raise SystemExit(f"unmapped contest header: {header!r}")


# ---------------------------------------------------------------------------
# Preprocessing: strip party prefixes off contest headers, collect parties.
# ---------------------------------------------------------------------------


def preprocess(lines):
    """Drop per-party statistics labels, then blank the Dem/Rep prefix off
    each contest-header line.

    Returns (new lines, [party, ...] in contest order).
    """
    kept = [ln for ln in lines if not PARTY_STAT_RE.match(ln)]
    out = list(kept)
    parties = []
    for i, line in enumerate(kept):
        if not line.strip():
            continue
        j = i + 1
        while j < len(kept) and not kept[j].strip():
            j += 1
        if j >= len(kept) or not VOTEFOR_LINE_RE.match(kept[j]):
            continue
        m = PARTY_PREFIX_RE.match(line.strip())
        parties.append(m.group(1).upper() if m else "")
        rest = m.group(2) if m else line.strip()
        out[i] = " " + rest
    return out, parties


def preprocess_text(text: str) -> str:
    lines, parties = preprocess(text.split("\n"))
    return "\n".join(lines) + "\n", parties


# ---------------------------------------------------------------------------
# Engine hooks: normalize_office wrapper + post-parse party stamp.
# ---------------------------------------------------------------------------


class PrecinctNormalizer:
    """TxtConfig.normalize_office hook: pops this contest's queued party and
    attaches the MARK marker to the office name."""

    def __init__(self, parties):
        self.parties = list(parties)
        self.i = 0

    def __call__(self, line):
        if self.i >= len(self.parties):
            raise SystemExit(f"unqueued contest header: {line!r}")
        party = self.parties[self.i]
        self.i += 1
        office, district = map_header(line)
        if party:
            office = office + MARK + party
        return (office, district)


def stamp_parties(rows):
    """Split the MARK party out of office names into the party column for
    every row (all rows here are contest rows; there are no Yes/No
    question rows in this file).  Title-cases the ALL-CAPS candidate
    names the report prints."""
    out = []
    for r in rows:
        office = r["office"]
        if MARK in office:
            office, party = office.split(MARK)
            r["office"] = office
            r["party"] = party
        cand = r["candidate"]
        if cand and cand.upper() == cand:
            cand = title_case_name(cand)
        if cand == "Write-ins":
            cand = "Write Ins"
        r["candidate"] = cand
        out.append(r)
    return out


# ---------------------------------------------------------------------------


def parse_precinct(path: Path, parties):
    """parse_txt on the (already preprocessed) extract at ``path``."""
    norm = PrecinctNormalizer(parties)
    cfg = TxtConfig(
        county=COUNTY,
        normalize_office=norm,
        prettify_precinct=g23.prettify_precinct,
        party_optional=True,
        # "No Candidate Filed" rows (if the county prints any) are kept --
        # none are known in this file.
        skip_candidates=(),
    )
    rows, precinct_count, warnings, unnamed = parse_txt(path, cfg)
    if norm.i != len(parties):
        print(f"WARNING: party queue consumed {norm.i} of {len(parties)} "
              f"contest headers")
    return stamp_parties(rows), precinct_count, warnings, unnamed


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf|input.txt> <output.csv>")
    src, dst = argv[1], argv[2]
    out_path = Path(dst)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if src.lower().endswith(".pdf"):
        import subprocess

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
        subprocess.run(["pdftotext", "-layout", src, str(tmp)], check=True)
        text = tmp.read_text(errors="replace")
        tmp.unlink(missing_ok=True)
    else:
        text = Path(src).read_text(errors="replace")

    text, parties = preprocess_text(text)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(text)
        tmp = Path(tf.name)
    try:
        rows, precinct_count, warnings, unnamed = parse_precinct(tmp, parties)
    finally:
        tmp.unlink(missing_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows across {precinct_count} precincts "
          f"to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)