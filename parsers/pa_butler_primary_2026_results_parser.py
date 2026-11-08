#!/usr/bin/env python3
"""
Butler County PA 2026 General Primary (2026-05-19) precinct parser.

Reproduces 2026/counties/20260519__pa__primary__butler__precinct.csv:

 1. Electionware primary flow.  The county's own source is
    ``Butler_County_2026_Primary_Precinct_Reports_Combined.pdf`` (94
    precincts; each precinct's ``Precinct Summary Report`` is 7 pages;
    OFFICIAL RESULTS, generated 06/09/2026).  It is parsed with the shared
    ``electionware_primary_np`` engine via the generic 2026-primary driver
    (``parsers/pa_electionware_primary_2026.py``) reusing Butler's existing
    ``pa_butler_general_2025_results_parser`` ElectionwareConfig; the same
    flow that originally produced this file.

 2. Conventions applied on top of the engine (this repo, matching the 2026
    statewide county anchor and Butler's 2024-general lineage):
      * ``district`` is filled for U.S. House (16) / State House
        (8, 11, 12, 17) rows -- determined per candidate row from the
        contest titles the engine normalishes away.
      * Candidate suffix "Jr." from the engine -> "Jr" as the report
        prints it ("MICHAEL J OEHLING JR").

 3. DOS fallback for FAIRVIEW TWP (the undercount fix).  The combined
    precinct report has 94 STATISTICS segments, but the PA Department of
    State certified precinct-returns bulk file
    (``PA DOS 2026 General Primary Certified Precinct Returns Statewide.txt``,
    split per county to ``work2026p/dos/butler__dos_precinctreturns.txt``,
    Butler county code 10) carries 95 precinct codes: "FAIRVIEW" exists as
    BOTH code 480 and 490.  Code 490 is digit-exact with the report's
    FAIRVIEW BORO segment (Shapiro 7 / Garrity 8 / Davis 7 / Ventre 2 /
    Richey 6 / Wagner 6 / Kelly 7 / Bonner 8), so code 480 is
    Fairview TOWNSHIP (Butler's 2024-general file carries FAIRVIEW TWP
    beside FAIRVIEW BORO).  The combined PDF simply has no FAIRVIEW TWP
    section, and its per-contest countywide sums miss the county SUMMARY
    report (and the statewide anchor ``2026/20260519__pa__primary__county.csv``)
    by exactly code 480's statewide-contest votes:
        Governor DEM -84, Governor REP -195, Lt Gov DEM -82,
        LtG Richey -124 / Ventre -75, U.S. House Wagner -78 / Kelly -200,
        State House 17 Bonner -200.
    The FAIRVIEW TWP rows are therefore sourced from the DOS bulk file
    (certified; same vintage as the anchor):
      * named candidates only -- the bulk export has no write-in rows, no
        RV/BC rows, no State Committee rows and no vote-method
        breakdowns (breakdown cells left empty);
      * no Undervotes rows (Electionware-only output).

    Known residual: the Member of Dem/Rep State Committee county sums
    remain short of the anchor by Fairview Twp's State Committee votes
    (about -6 to -187 per candidate); those votes exist in no precinct-
    level source (DOS exports do not carry State Committee rows).

Usage:
    python parsers/pa_butler_primary_2026_results_parser.py <combined.pdf> <output.csv>
"""

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pa_electionware_primary_2026 as ew2026  # noqa: E402

COUNTY = "Butler"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "provisional", "absentee",
]

# DOS slice for the FAIRVIEW TWP fallback (Butler county code 10).
DOS_SLICE = Path(__file__).resolve().parent.parent / "work2026p" / "dos" / \
    "butler__dos_precinctreturns.txt"
DOS_FAIRVIEW_TWP_CODE = "480"
DOS_FAIRVIEW_TWP_NAME = "FAIRVIEW TWP"

# DOS candidate tuple (suffix, last, first, middle) 0-indexed fields
# 11=last,12=first,13=middle,14=suffix -> printed name used by the rest of
# the precinct file / statewide county anchor.
DOS_NAME_FIX = {
    ("SHAPIRO", "JOSHUA", "D"): "Josh Shapiro",
    ("GARRITY", "STACY", "LORRAINE"): "Stacy Garrity",
    ("DAVIS", "AUSTIN", ""): "Austin Davis",
    ("RICHEY", "JASON", "L"): "Jason Richey",
    ("VENTRE", "JOHN", ""): "John Ventre",
    ("WAGNER", "JUSTIN", "REED"): "Justin Wagner",
    ("KELLY", "GEORGE", "JOSEPH"): "Mike Kelly",
    ("BONNER", "TIMOTHY", "R"): "Tim Bonner",
}

DOS_OFFICE = {"GOV": "Governor", "LTG": "Lieutenant Governor",
              "USC": "U.S. House", "STH": "State House"}

# The report's own office-title district numbers (per the statewide
# county anchor rows for Butler).
HD_DISTRICT = {
    "Brandon Dukes": "12", "Rick Telesz": "8",
    "Aaron Bernstine": "8", "Ethan Nicholas": "12", "Scott Timko": "12",
    "Marci Mustello": "11", "Tim Bonner": "17",
    "Justin Wagner": "16", "Mike Kelly": "16",
}


def normalize_suffix(name: str) -> str:
    """Electionware title-cases "JR" to "Jr."; the report prints "JR"."""
    out = name
    for suf in ("Jr.", "Sr."):
        out = out.replace(suf, suf[:-1])
    return out


def run_electionware(pdf_path: Path) -> list[dict]:
    """Run the shared electionware primary flow and return its rows."""
    config = ew2026.load_config(COUNTY)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        ew2026.run_cli(config, argv=["pa_electionware_primary_2026",
                                     str(pdf_path), str(tmp)])
        with tmp.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))
    finally:
        if tmp.exists():
            tmp.unlink()


def fairview_twp_rows_from_dos() -> list[dict]:
    """FAIRVIEW TWP (DOS precinct code 480) named-candidate rows."""
    if not DOS_SLICE.exists():
        raise RuntimeError(f"DOS slice missing: {DOS_SLICE}")
    rows = []
    with DOS_SLICE.open(newline="", encoding="utf-8-sig") as fh:
        import csv as _csv
        for fields in _csv.reader(fh):
            if fields[1] != "P" or fields[2] != "10":
                continue
            if fields[3] != DOS_FAIRVIEW_TWP_CODE:
                continue
            office = DOS_OFFICE.get(fields[8])
            if office is None:
                raise RuntimeError(f"unexpected DOS office {fields[8]!r}")
            name = DOS_NAME_FIX.get(
                (fields[11], fields[12], fields[13]),
                None if fields[13] else None)
            if name is None:
                raise RuntimeError(
                    "unmapped DOS candidate "
                    f"{(fields[11], fields[12], fields[13])!r}")
            district = ""
            if office in ("U.S. House", "State House"):
                district = str(int(fields[5]))
            rows.append({
                "county": COUNTY, "precinct": DOS_FAIRVIEW_TWP_NAME,
                "office": office, "district": district,
                "party": fields[9], "candidate": name,
                "votes": str(int(fields[15])),
                "election_day": "", "provisional": "", "absentee": "",
            })
    if not rows:
        raise RuntimeError("no FAIRVIEW TWP rows found in the DOS slice")
    # The precinct pages order all DEM-side contests before the REP-side
    # ones; mirror that block order for the appended precinct.
    party_rank = {"DEM": 0, "REP": 1}
    office_rank = {"Governor": 0, "Lieutenant Governor": 1, "U.S. House": 2,
                   "State House": 3}
    rows.sort(key=lambda r: (party_rank[r["party"]], office_rank[r["office"]]))
    return rows


def fill_districts(rows: list[dict]) -> None:
    """Fill the district column for State House / U.S. House rows from the
    contest titles' candidate/district pairing (engine drops it)."""
    for row in rows:
        if row["office"] in ("State House", "U.S. House") and not row["district"]:
            d = HD_DISTRICT.get(row["candidate"])
            if d is not None:
                row["district"] = d


def parse(pdf_path: Path) -> list[dict]:
    rows = run_electionware(pdf_path)
    fill_districts(rows)
    ft = fairview_twp_rows_from_dos()
    # Insert FAIRVIEW TWP ahead of FAIRVIEW BORO (the report's own order:
    # FAIRVIEW TWP = DOS code 480 < FAIRVIEW BORO = 490; both sit between
    # EVANS CITY BORO and FORWARD TWP).
    idx_fb = next(i for i, r in enumerate(rows)
                  if r["precinct"].upper() == "FAIRVIEW BORO")
    rows = rows[:idx_fb] + ft + rows[idx_fb:]
    for r in rows:  # header/order canonicalization
        r["candidate"] = normalize_suffix(r["candidate"])
    return rows


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <combined.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    rows = parse(pdf_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    precincts = len({r["precinct"] for r in rows})
    print(f"Wrote {len(rows)} rows across {precincts} precincts to {out_path}")


if __name__ == "__main__":
    main(sys.argv)
