#!/usr/bin/env python3
"""Juniata County, PA 2022 General Primary (May 17, 2022) -- precinct results.

Source: "Juniata PA 2022-Primary-Precinct-Official-Results-1.pdf"
(Electionware "Precinct Summary Results Report", dated 06/07/2022 2:45 PM,
307 pages / 18 precincts). The write-in/unofficial companion
"Juniata PA 22-Primary-Unofficial-Results-WI.pdf" is a county-level
"Election Summary" used only as a secondary cross-check.

Same report family as Huntingdon's 2022 primary (both counties print the
all-caps "STATISTICS" variant). Wrapper over the shared engines
``electionware_precinct_np`` (block extraction, prettify) and
``electionware_primary_np`` (primary row parsing: party on the office
header, e.g. "DEM UNITED STATES SENATOR"). No shared-engine edits.
Quirks handled by this wrapper:

- Per-party statistics: the report also prints "Registered Voters -
  DEMOCRATIC|REPUBLICAN" and "Ballots Cast - DEMOCRATIC|REPUBLICAN |
  Total | Blank" whose vote values sit on the line ABOVE the label line
  under pdftotext -layout (on the same line under natural_pdf's
  extraction) -- the engine's same-tail matchers see only the labels.
  ``stats_rows`` re-reads these into the 2020-precedent party rows
  (Registered Voters; Ballots Cast as Total/DEM/REP; Ballots Cast -
  Blank), handling both layouts. "Ballots Cast - NONPARTISAN"/
  RV - NONPARTISAN (all zero, no nonpartisan contest) are dropped per
  the Centre 2022 precedent. Engine-emitted Registered Voters / Ballots
  Cast rows are dropped and re-read in source order by the wrapper.
- Ballots Cast - Blank is written as the spec office "Ballots Cast - Blank"
  (the engine prints "Ballots Cast Blank").
- "Write-In Totals" rows become candidate "Write-ins" carrying the
  contest's party (2022-primary brief); the "Write-In: ..." detail rows
  and "Not Assigned" residuals are dropped. Overvotes/Undervotes rows the
  engine picks up off the wrapped layout are dropped too (2022-primary
  convention; the "Total Votes Cast" excludes them).
- Per-precinct COMMITTEEPERSON local contests ("REP WALKER TOWNSHP
  REPUBLICAN COMMITTEEPERSON"; "DEM PORT ROYAL BOROUGH DEMOCRATIC
  COMMITTEEPERSON"; the DEM "/E. WATERFORD" header is printed truncated --
  it ends at "... DISTRICT DEMOCRATIC" with no COMMITTEEPERSON word).
  Office "<Muni> Democratic/Republican Committeeperson" mirroring the
  precinct labels for the Tuscarora split precincts ("Tuscarora Twp_E.
  Waterford Democratic Committeeperson"), district empty (Montgomery-style).
- Districts: printed here ("REPRESENTATIVE IN CONGRESS 13TH DISTRICT",
  "SENATOR IN THE GENERAL ASSEMBLY 30TH DISTRICT", "REPRESENTATIVE IN THE
  GENERAL ASSEMBLY 85TH/86TH DISTRICT") and extracted by the shared engine.
  "MEMBER OF (DEMO|REP)UBLICAN STATE COMMITTEE" districts are stamped 34 to
  match the DOS-certified county-level anchor ("Member of Republican State
  Committee,34"); the DEM State Committee sections are write-in-only.
- Candidate names title-cased by the engine's _finalize_candidate (the
  report prints all-caps), matching the county's 2022 general file and the
  2022-primary Huntingdon file ("John Fetterman", "Douglas V. Mastriano").

Output schema (7-column + breakdowns):
  county,precinct,office,district,party,candidate,votes,election_day,mail,provisional

Recount note (verified): this report's vintage (06/07/2022 2:45 PM)
already incorporates the Oz/McCormick statewide recount -- REP U.S.
Senate per-candidate sums exactly match the post-recount ENR anchor
(Oz 1,007 vs 1,006 in the pre-recount 05/20 unofficial companion;
+1 Oz recount vote).

Usage:
    python parsers/pa_juniata_primary_2022_results_parser.py \
        "<input.pdf>" "<output.csv>"
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from electionware_precinct_np import (
    ElectionwareConfig,
    extract_precinct_blocks,
)
from electionware_primary_np import parse_primary_precinct_rows

COUNTY = "Juniata"

SKIP_PREFIXES = (
    "Precinct Summary Results Report",
    "2022 Primary Precinct Official Results",
    "Primary Election",
    "May 17, 2022",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "Total Votes Cast",
    "Not Assigned",
    "Voter Turnout",
    "Election Day",
    "Election                    ",  # wrapped column header
    "Provisional",
    "Vote For ",
)

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

# ---------------------------------------------------------------------------
# Precinct prettify: match the county's 2023 primary/general file naming
# ("Beale Township", "Port Royal Borough", "Tuscarora Twp_E. Waterford").
# ---------------------------------------------------------------------------

_BOROUGH_PRECINCTS = {"PORT ROYAL", "MIFFLIN", "MIFFLINTOWN", "THOMPSONTOWN"}
_TUSCARORA_TAILS = {"E. WATERFORD": "E. Waterford", "TUSC": "Tuscarora"}


def prettify_precinct(raw: str) -> str:
    name = re.sub(r"[\s/]+", " ", raw.strip()).strip()
    name = re.sub(r"^TUSCARORA E\. WATERFORD$", "TUSCARORA/E. WATERFORD", name)
    name = re.sub(r"^TUSCARORA TUSC$", "TUSCARORA/TUSC", name)
    if name.upper().startswith("TUSCARORA/"):
        tail = name.split("/", 1)[1].strip().upper()
        if tail == "TUSC":  # 2023 general file spells the district out
            tail = "TUSCARORA"
        tail = re.sub(r"[A-Z]+", lambda m: m.group(0).capitalize(), tail)
        return "Tuscarora Twp_" + tail
    if name.upper() in _BOROUGH_PRECINCTS:
        return name.title() + " Borough"
    return name.title() + " Township"


# ---------------------------------------------------------------------------
# Per-precinct COMMITTEEPERSON contests.
# ---------------------------------------------------------------------------

COMMITTEE_OFFICES = {
    "BEALE": "Beale Township",
    "DELAWARE": "Delaware Township",
    "FAYETTE": "Fayette Township",
    "FERMANAGH": "Fermanagh Township",
    "GREENWOOD": "Greenwood Township",
    "LACK": "Lack Township",
    "MIFFLIN": "Mifflin Borough",
    "MIFFLINTOWN": "Mifflintown Borough",
    "MILFORD": "Milford Township",
    "MONROE": "Monroe Township",
    "PORT ROYAL": "Port Royal Borough",
    "SPRUCE HILL": "Spruce Hill Township",
    "SUSQUEHANNA": "Susquehanna Township",
    "THOMPSONTOWN": "Thompsontown Borough",
    "TURBETT": "Turbett Township",
    "WALKER": "Walker Township",
}


_COMMITTEE_RE = re.compile(
    r"^(.+?)\s+(DEMOCRATIC|REPUBLICAN)(\s+COMMITTEEPERSON)?$"
)


def committeeperson_handler(line: str):
    """Office header -> (office, district) for '<Muni> <Party> Committeeperson'.

    Handles the source's quirks: "TOWNSHP" misspelling, the truncated
    DEM "/E. WATERFORD DISTRICT DEMOCRATIC" header (no COMMITTEEPERSON
    word printed), and the slash-form Tuscarora split precincts.
    """
    t = re.sub(r"\s+", " ", line.strip()).upper()
    m = _COMMITTEE_RE.match(t)
    if not m:
        return None
    party_word, muni = m.group(2), m.group(1).strip()
    muni = re.sub(r"\s+DISTRICT\b$", "", muni).strip()
    parts = [p.strip() for p in muni.split("/", 1)] if "/" in muni else [muni]
    if len(parts) == 2:
        # "TUSCARORA TOWNSHIP / E. WATERFORD|TUSC" -> county-file style tail
        tail = parts[1]
        if tail == "TUSC":
            tail = "TUSCARORA"
        tail = re.sub(r"[A-Z]+", lambda mm: mm.group(0).capitalize(), tail)
        name = "Tuscarora Twp_" + tail
    else:
        name = parts[0]
        name = re.sub(r"\s+TOWNSHP\b", " TOWNSHIP", name)
        name = " ".join(w.capitalize() for w in name.split())
        if name in COMMITTEE_OFFICES:
            name = COMMITTEE_OFFICES[name]
    return (f"{name} {party_word.capitalize()} Committeeperson", "")


# ---------------------------------------------------------------------------
# Wrapped per-party statistics ("Ballots Cast - <kind>" numbers sit on the
# physical line above the label line).
# ---------------------------------------------------------------------------

_PURE_INTS_RE = re.compile(r"^(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")
_INT_RE = re.compile(r"^(\d[\d,]*)$")
_RV_LINE_RE = re.compile(r"^Registered Voters - (\w+)\s+(\d[\d,]*)$")
_BC_LABEL_RE = re.compile(r"^Ballots Cast - (\w+)(?:\s+(.*))?$")
_PARTY_WORD = {"TOTAL": "", "DEMOCRATIC": "DEM", "REPUBLICAN": "REP",
               "BLANK": "", "NONPARTISAN": None}
# kind -> office; None = drop (Centre 2022 precedent)
_BC_OFFICE = {"TOTAL": "Ballots Cast", "DEMOCRATIC": "Ballots Cast",
              "REPUBLICAN": "Ballots Cast", "BLANK": "Ballots Cast - Blank"}


def _tail4(tail: str):
    toks = [t for t in tail.split()]
    if len(toks) == 4 and all(_INT_RE.match(t) for t in toks):
        return [int(t.replace(",", "")) for t in toks]
    return None


def stats_rows(precinct: str, text: str) -> list:
    """Registered Voters / Ballots Cast rows, in source order.

    Registered Voters lines carry their value on the same line; Ballots
    Cast lines carry none and rely on the pure-int line printed above.
    """
    def row(office, party, votes, ed="", mail="", prov=""):
        return {
            "county": COUNTY,
            "precinct": precinct,
            "office": office,
            "district": "",
            "party": party,
            "candidate": "",
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        }

    rows = []
    pending = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _PURE_INTS_RE.match(line):
            vals = [int(t.replace(",", "")) for t in _PURE_INTS_RE.match(line).groups()]
            pending = vals
            continue
        m = _RV_LINE_RE.match(line)
        if m:
            kind = m.group(1).upper()
            party = {"TOTAL": "", "DEMOCRATIC": "DEM", "REPUBLICAN": "REP",
                     "NONPARTISAN": None}.get(kind, "")
            if party is None:
                continue  # NONPARTISAN dropped (all zero)
            pending = None
            rows.append(row("Registered Voters", party,
                            int(m.group(2).replace(",", ""))))
            continue
        m = _BC_LABEL_RE.match(line)
        if m:
            kind = m.group(1).upper()
            office = _BC_OFFICE.get(kind)
            tail = m.group(2)
            if tail:
                vals = _tail4(tail)
                if vals is None:
                    vals = None
                    pending = None
                    sys.stderr.write(
                        f"WARNING {precinct}: odd 'Ballots Cast - "
                        f"{m.group(1)}' tail {tail!r}\n")
                    continue
            else:
                vals = pending
            pending = None
            party = _PARTY_WORD.get(kind, None)
            if party is None:
                continue  # NONPARTISAN dropped
            if vals is None:
                sys.stderr.write(f"WARNING {precinct}: no wrapped vote tail "
                                 f"for 'Ballots Cast - {m.group(1)}'\n")
                continue
            total, ed, mail, prov = vals
            rows.append(row(office, party, total, ed, mail, prov))
            continue
        if not re.match(r"^[\d,.%\s]+$", line):
            pending = None
    return rows


# ---------------------------------------------------------------------------
# Block assembly.
# ---------------------------------------------------------------------------

# Engine row offices to drop (re-read into source order by stats_rows, or
# aggregate detail rows dropped per the 2022-primary conventions above).
_DROP_OFFICES = {"Registered Voters", "Ballots Cast", "Ballots Cast Blank"}


def parse_pdf(pdf_path: Path) -> list:
    import natural_pdf as npdf

    pdp = npdf.PDF(str(pdf_path))
    config = ElectionwareConfig(
        county=COUNTY,
        skip_prefixes=SKIP_PREFIXES,
        county_header_suffix="Juniata",
        extra_office_handlers=[committeeperson_handler],
        prettify_precinct=prettify_precinct,
    )
    out = []
    for name, text in extract_precinct_blocks(pdp, config):
        pretty = re.sub(r"\s{2,}", " ", prettify_precinct(name)).strip()
        contest_rows = []
        for r in parse_primary_precinct_rows(pretty, text, config):
            if r["candidate"] == "Not Assigned":
                # Write-in detail residual already inside the Write-In Totals.
                continue
            if r["office"] in _DROP_OFFICES:
                continue  # re-read into ordered party layout by stats_rows
            if r["candidate"] == "Write-In Totals":
                r["candidate"] = "Write-ins"
            if r["office"].startswith("Member of "):
                # State Committee district per the county-level anchor
                # ("Member of Republican State Committee,34,REP,...").
                r["district"] = "34"
            # The engine carries the 3rd int under "absentee" (its 2024-key);
            # rename into the 2022 "mail" column and align column order.
            r["mail"] = r.get("absentee", "")
            contest_rows.append(r)
        out.extend(stats_rows(pretty, text) + contest_rows)
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.pdf> <output.csv>")
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    rows = parse_pdf(src)
    # Drop engine-picked-up Overvotes/Undervotes rows (2022-primary
    # convention; the report prints them on the same line so they parse,
    # unlike Huntingdon's wrapped variant).
    rows = [r for r in rows
            if r["candidate"] not in ("Overvotes", "Undervotes",
                                      "Contest Totals")]
    n_committee = sum(1 for r in rows if r["office"].endswith("Committeeperson"))
    with dst.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({
                k: ("" if r.get(k, "") is None else r.get(k, "")) for k in FIELDNAMES
            })
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts "
          f"({n_committee} CommitteePerson rows) to {dst}")


if __name__ == "__main__":
    main()