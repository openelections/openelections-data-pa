#!/usr/bin/env python3
"""
Parse Indiana County PA 2023 General (Municipal) Election results.

Source: Indiana County Official Summary Results 2023 General.pdf (148 pages,
Electionware "Summary Results Report").  The pdftotext -layout output
(work2023/txt/) separates the columns cleanly, so this parser works on text
rather than the natural-pdf engines.

This file is a summary report, NOT a precinct report: sections are one per
CONTEST (multi-page sections repeat the header for write-in-list
continuations).  Countywide contests (statewide judicial races, County
Commissioner, County Auditor, District Attorney, Sheriff, Prothonotary and
Clerk of Courts, the two judicial retentions, and the two Magisterial
District Judge districts) are reported once, countywide; local contests are
per municipality/ward (e.g. "Auditor Six Year Burrell Township", "Member of
Council Four Year Blairsville Ward #1", "School Director Region 2 (2) River
Valley Region 2").

Because no finer granularity exists in the source, the jurisdiction named in
each local contest header is the row's finest granularity:
  * local contests (Auditor/Supervisor/Member of Council/Mayor/Tax
    Collector/Constable) use the municipality name as the ``precinct``
    value (it also stays in ``district``, matching the other 2023 parsers);
  * school-district rows use the school district name as ``precinct``
    (office carries the "Region N" / "At Large" designator);
  * countywide contest rows (statewide judicial races, County Commissioner,
    County Auditor, District Attorney, Sheriff, Prothonotary and Clerk of
    Courts, the two judicial retentions, the Magisterial District Judge
    districts) keep the county name ("Indiana") as ``precinct`` — the
    missing_values data test requires a non-empty precinct.

Header mappings:
  * "Auditor Six Year X Township"    -> Township Auditor (6 Year), X Township
  * "Supervisor Four Year X Township"-> Township Supervisor (4 Year)
  * "Member of Council Four Year X"  -> Borough Council (4 Year), X
  * "Mayor Two Year X Borough"       -> Mayor (2 Year), X
  * "School Dir Region 3 (1) Armstrong Co Region 3" ->
       School Director Region 3 / Armstrong Co   ("(N)" = Vote For N)
  * "School Director At Large (5) Indiana At Large" ->
       School Director At Large / Indiana
  * "Magisterial District Judge 40-2-01" -> MDJ, district 40-2-01
  * "Retention Jack Panella"      -> Superior Court Retention - Jack Panella
  * "Retention Victor P. Stabile" -> Commonwealth Court Retention - V P Stabile

Usage:
    python parsers/pa_indiana_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Indiana]
"""

import re
import sys

try:
    from electionware_txt_2023 import parse_report, write_csv, load_text
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from electionware_txt_2023 import parse_report, write_csv, load_text


COUNTY = "Indiana"
PAGE_HEADER_KEY = None            # summary-style file: no per-page precincts
COUNTYWIDE_PRECINCT = "Indiana"

EXACT_OFFICES = {
    "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "County Auditor": ("County Auditor", ""),
    "County Commissioner": ("County Commissioner", ""),
    "District Attorney": ("District Attorney", ""),
    "Sheriff": ("Sheriff", ""),
    "Prothonotary and Clerk of Courts": ("Prothonotary and Clerk of Courts", ""),
}

RETENTION_OFFICES = {
    "Retention Jack Panella": "Superior Court Retention - Jack Panella",
    "Retention Victor P. Stabile":
        "Commonwealth Court Retention - Victor P Stabile",
}

MDJ_RE = re.compile(r"^Magisterial District Judge (\d{2}-\d-\d{2})$")

# Local offices: "<Office> [Term] <Municipality>".
LOCAL_RES = [
    (re.compile(r"^Auditor Six Year (.+)$"), "Township Auditor (6 Year)"),
    (re.compile(r"^Auditor Four Year (.+)$"), "Township Auditor (4 Year)"),
    (re.compile(r"^Auditor Two Year (.+)$"), "Township Auditor (2 Year)"),
    (re.compile(r"^Auditor (.+)$"), "Township Auditor"),
    (re.compile(r"^Supervisor Six Year (.+)$"), "Township Supervisor (6 Year)"),
    (re.compile(r"^Supervisor Four Year (.+)$"), "Township Supervisor (4 Year)"),
    (re.compile(r"^Supervisor Two Year (.+)$"), "Township Supervisor (2 Year)"),
    (re.compile(r"^Supervisor (.+)$"), "Township Supervisor"),
    (re.compile(r"^Member of Council Four Year (.+)$"), "Borough Council (4 Year)"),
    (re.compile(r"^Member of Council Two Year (.+)$"), "Borough Council (2 Year)"),
    (re.compile(r"^Member of Council (.+)$"), "Borough Council"),
    (re.compile(r"^Mayor Two Year (.+)$"), "Mayor (2 Year)"),
    (re.compile(r"^Mayor Four Year (.+)$"), "Mayor (4 Year)"),
    (re.compile(r"^Mayor (.+)$"), "Mayor"),
    (re.compile(r"^Tax Collector (.+)$"), "Tax Collector"),
    (re.compile(r"^Constable (.+)$"), "Constable"),
]

SCHOOL_RE = re.compile(
    r"^School Dir(?:ector)?\s+(.+?)\s+\((\d+)\)\s+(.+?)\s+Region (\d+)$")
SCHOOL_AT_LARGE_RE = re.compile(
    r"^School Director At Large(?:\s+\((\d+)\))?\s+(.+?)\s+At Large$")


def indiana_map_header(header: str):
    h = header.strip()
    if h in EXACT_OFFICES:
        return EXACT_OFFICES[h]
    if h in RETENTION_OFFICES:
        return (RETENTION_OFFICES[h], "")
    m = MDJ_RE.match(h)
    if m:
        return ("Magisterial District Judge", m.group(1))
    # School districts first (their names can contain local-office words).
    m = SCHOOL_RE.match(h)
    if m:
        return (f"School Director Region {m.group(4)}", m.group(3).strip())
    m = SCHOOL_AT_LARGE_RE.match(h)
    if m:
        return ("School Director At Large", m.group(2).strip())
    for rx, office in LOCAL_RES:
        m = rx.match(h)
        if m:
            return (office, m.group(1).strip())
    return None


def _is_header(line: str) -> bool:
    h = line.strip()
    return indiana_map_header(h) is not None


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input_path> <output_path> "
                 f"[--county NAME]")
    rows, warnings = parse_report(
        load_text(args[0]), COUNTY, indiana_map_header,
        page_header_key=PAGE_HEADER_KEY,
        countywide_precinct=COUNTYWIDE_PRECINCT,
        is_header=_is_header,
        log=lambda m: print(m, file=sys.stderr),
    )
    # The jurisdiction in a local contest header is the row's finest
    # granularity: promote it to precinct (it also stays in district).
    # Countywide contests keep the county name as precinct; their district
    # is either empty or a Magisterial District Judge court code.
    for r in rows:
        if r["district"] and not MDJ_RE.match(r["district"]):
            r["precinct"] = r["district"]
    write_csv(rows, args[1], COUNTY)
    print(f"wrote {len(rows)} rows -> {args[1]} ({len(warnings)} warnings)")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)