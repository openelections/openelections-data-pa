#!/usr/bin/env python3
"""
Parse Cameron County PA 2024 Primary (April 23, 2024) precinct results.

Source: 10 scanned per-precinct Electionware "Summary Results Report" PDFs
(Cameron PA 2024 Primary Precinct Results.zip, one 14-page PDF per precinct).
The scans carry no text layer, so pages are OCR'd with tesseract
(pdftoppm -r 300 -gray; --psm 6) into <ocr_dir>/*.txt first:

    zsh work2024p/cameron/scripts/ocr_all.sh
    python parsers/pa_cameron_primary_2024_results_parser.py \\
        work2024p/cameron/ocr 2024/counties/20240423__pa__primary__cameron__precinct.csv

Row parsing, office normalization and the row schema (party on the office
header, mail-votes column reported as ``mail`` to match this county's 2024
general/2025 general files, write-in detail rows skipped, Not Assigned /
Overvotes / Undervotes kept) are reused from the shared Electionware
primary engine (``electionware_primary_np``), configured through Cameron's
2025-general ``CONFIG`` (same county, same report generator).

Cameron-specific handling:
  - Precinct names in the 2024-primary scans are the shortened report names
    ("East Shippen", "Middle Ward"). They are mapped to the county's
    canonical precinct names as printed in the county's own 2024/2025
    general Electionware files ("East Shippen Township", "Middle Ward
    Emporium Borough", ...) so precinct keys line up across election years.
  - Delegate offices: the primary scans print "DEM DELEGATE TO DEMOCRATIC
    NATIONAL CONVENTION", "REP DELEGATE TO REPUBLICAN NATIONAL CONVENTION"
    and "REP ALT DELEGATE TO REPUBLICAN NATIONAL CONVENTION" (Cameron's
    delegate contests are NOT districted in these reports). They normalize
    to "Delegate to the Democratic/Republican National Convention" /
    "Alternate Delegate to the Republican National Convention".
  - OCR quirks normalized in the line preprocessor: "Write-in" -> "Write-In"
    and trailing ':' dropped (e.g. "REP PRESIDENT OF THE UNITED STATES :").
"""

import csv
import re
import sys
from pathlib import Path

from electionware_precinct_np import ElectionwareConfig
from pa_electionware_primary_2024 import load_config
from electionware_primary_np import parse_primary_precinct_rows

# Header matches this county's own 2024/2025 general county files
# (county,precinct,office,district,party,candidate,votes,election_day,mail,
# provisional). The engine calls the vote-method columns provisional/absentee
# (mail); Cameron's reports label them "Mail Votes", so the column is named
# mail here.
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

# ---------------------------------------------------------------- precincts
# Report names in the 2024 primary scans -> canonical names used in this
# county's other OpenElections files (2024/2025 general Electionware CSVs).
CANONICAL_PRECINCTS = {
    "Driftwood Borough": "Driftwood Borough",
    "East Shippen": "East Shippen Township",
    "West Shippen": "West Shippen Township",
    "East Ward": "East Ward Emporium Borough",
    "West Ward": "West Ward Emporium Borough",
    "Middle Ward": "Middle Ward Emporium Borough",
    "Gibson Township": "Gibson Township",
    "Grove Township": "Grove Township",
    "Lumber Township": "Lumber Township",
    "Portage Township": "Portage Township",
}

EXTRA_SKIP_PREFIXES = (
    # Per-precinct banner lines repeated on every page of the scan.
    "PRIMARY ELECTION",
    "April 23, 2024 CAMERON COUNTY",
    # Contest column header (OCR'd as e.g. "TOTAL ao Mail Votes P'ovisional").
    "TOTAL ",
    "Election Summary",
)

EXTRA_EXACT_OFFICES = {
    "DELEGATE TO DEMOCRATIC NATIONAL CONVENTION": (
        "Delegate to the Democratic National Convention",
        "",
    ),
    "DELEGATE TO REPUBLICAN NATIONAL CONVENTION": (
        "Delegate to the Republican National Convention",
        "",
    ),
    "ALT DELEGATE TO REPUBLICAN NATIONAL CONVENTION": (
        "Alternate Delegate to the Republican National Convention",
        "",
    ),
}


WRITE_IN_VARIANTS = ("in", "ln", "tn", "tr", "it", "i")
_WRITE_IN_RE = re.compile(
    r"(?i)^\s*[^\w]*write[-\s]*(in|ln|tn|tr|it|i)\s*[:,]?\s*(.*)$"
)
_FOUR_INT_RE = re.compile(r"\s(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")


def _ocr_line_preprocessor(line: str) -> str:
    ln = line.strip()
    # Write-in lines OCR under many spellings: "Write-in; X", "Write-tn: X",
    # ": Write tn X". The engine only recognizes "Write-In:"/"Totals".
    m = _WRITE_IN_RE.match(ln)
    if m and m.group(1).lower() in WRITE_IN_VARIANTS:
        rest = m.group(2).strip()
        if rest.lower().startswith("totals"):
            ln = "Write-In Totals" + rest[6:]
        else:
            ln = "Write-In: " + rest
    # Vote-count lines end in digit noise after the 4-int tail
    # ("KEN LAYNG 8 6 2 0 :", "JOYCE C HAAS 51 38 13 0 |").
    ln = re.sub(
        r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*[^\d\s]*\s*$",
        r"\1 \2 \3 \4",
        ln,
    )
    # OCR "1" read as "]" inside the vote tail ("TIM DEFOOR 2] 16 5 0").
    if "]" in ln:
        cand = ln.replace("]", "1")
        if _FOUR_INT_RE.search(cand) and not _FOUR_INT_RE.search(ln):
            ln = cand
    # "Vote For ]" (OCR of "1") / "Vote For3" — the office-header detector
    # needs a digit here.
    ln = re.sub(r"(?i)^vote\s+for\s+[^\w\d]\s*$", "Vote For 1", ln)
    ln = re.sub(r"(?i)^vote\s+for(\d+)$", r"Vote For \1", ln)
    # Leading OCR noise (". Not Assigned", "' Overvotes", "_DEM SENATOR ...").
    ln = re.sub(r"^[\W_]+", "", ln)
    # "Total: Votes Cast" -> "Total Votes Cast" (skip-prefix match).
    ln = re.sub(r"(?i)^total\s*:?\s+votes\s+cast\b", "Total Votes Cast", ln)
    # Stray punctuation tokens inside the vote tail ("3 3 - 0 0",
    # "25 25 : 0 0", "2 2 . 0 0").
    ln = re.sub(r"(?<=\d)\s+[.:;,\"\x27-]\s+(?=\d)", " ", ln)
    # Stray period separator in aggregate heads ("Undervotes . 5 3 2 0").
    ln = re.sub(r"(?i)^(undervotes|overvotes)\s+\.\s*", r"\1 ", ln)
    return ln


def build_config() -> ElectionwareConfig:
    config = load_config("Cameron")
    config.skip_prefixes = tuple(
        dict.fromkeys(config.skip_prefixes + EXTRA_SKIP_PREFIXES)
    )
    config.exact_offices = {**EXTRA_EXACT_OFFICES, **config.exact_offices}
    config.prettify_precinct = lambda name: name
    config.line_preprocessor = _ocr_line_preprocessor
    return config


def read_blocks(ocr_dir: Path):
    """Yield (report_name, text) per OCR'd per-precinct file."""
    for path in sorted(ocr_dir.glob("*.txt")):
        text = path.read_text(errors="replace")
        names = re.findall(r"PRIMARY ELECTION (.+)$", text, re.MULTILINE)
        names = [n.strip() for n in names if n.strip()]
        if not names:
            sys.exit(f"{path}: no 'PRIMARY ELECTION <precinct>' header found")
        unique = sorted(set(names))
        if len(unique) != 1:
            sys.exit(f"{path}: ambiguous precinct headers {unique}")
        yield unique[0], text


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <ocr_dir> <output.csv>")
    ocr_dir = Path(argv[1])
    out_path = Path(argv[2])
    config = build_config()
    rows = []
    names = []
    for report_name, text in read_blocks(ocr_dir):
        if report_name not in CANONICAL_PRECINCTS:
            sys.exit(f"Unknown precinct name {report_name!r} (add to map)")
        precinct = CANONICAL_PRECINCTS[report_name]
        names.append(precinct)
        rows.extend(parse_primary_precinct_rows(precinct, text, config))
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "county": r["county"],
                    "precinct": r["precinct"],
                    "office": r["office"],
                    "district": r["district"],
                    "party": r["party"],
                    "candidate": r["candidate"],
                    "votes": r["votes"],
                    "election_day": r["election_day"],
                    "mail": r["absentee"],
                    "provisional": r["provisional"],
                }
            )
    print(
        f"Wrote {len(rows)} rows across {len(names)} precincts "
        f"({', '.join(names)}) to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)
