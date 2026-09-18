#!/usr/bin/env python3
"""
Parse Lebanon County PA 2023 General (Municipal) Election results.

Lebanon's precinct-level source ("Lebanon County Precinct Level Summary
Report 2023 General.pdf", 276 pages) is a scanned image with no text layer,
so the only parseable source is the countywide "County Summary Results
Report" (20 pages, Electionware "Summary Results Report" layout, OFFICIAL
RESULTS).  This parser therefore produces COUNTY-LEVEL rows only (no
precinct column data exists in the source):

    county,office,district,party,candidate,votes,election_day,mail,provisional

Header mapping notes (Lebanon-specific):
  * "JUDGE OF THE COURT OF COMMON PLEAS 52ND DISTRICT" -> office
    "Judge of the Court of Common Pleas", district "52nd District".
  * "MAGISTERIAL DISTRICT JUDGE 52-3-01 52-3-01" (district token printed
    twice) -> district "52-3-01"; "MAGISTERIAL DISTRICT JUDGE 52-3-05
    52-03-05" -> district "52-3-05" (source's second token spells it
    "52-03-05"; the first token is authoritative).
  * "MEMBER CITY COUNCIL CITY" is Lebanon City's council contest ->
    office "City Council", district "Lebanon City" (Lebanon City is the
    county's only city; the trailing "CITY" is the municipality label).
  * "COUNCIL MEMBER <boro>" -> office "Borough Council", district <boro>;
    "COUNCIL MEMBER-2 YEAR <boro>" -> "Borough Council (2 Year)".
  * "TOWNSHIP COMMISSIONER[...] <twp>" -> "Township Commissioner"
    ("(2 Year)" for the "-2 YEAR" variant).
  * "SUPERVISOR <twp>" -> "Township Supervisor"; "AUDITOR <twp>" ->
    "Township Auditor" ("AUDITOR-4 YEAR"/"AUDITOR-2 YEAR" variants keep the
    term suffix).
  * "SCHOOL DIRECTOR AT LARGE <district>" -> "School Director",
    district <district>.  NOTE: "Cornwall Lebanon" and "Northern Lebanon"
    each appear TWICE as separate contests (Vote For 5 and Vote For 1
    sections); rows are kept under the same district label.
  * "RETENTION QUESTION PANELLA" -> "Superior Court Retention - Jack
    Panella"; "RETENTION QUESTION STABILE" -> "Superior Court Retention -
    Victor P. Stabile" (both 2023 retention questions are Superior Court
    seats, verified statewide).

Usage:
    python parsers/pa_lebanon_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Lebanon]
"""

import re
import sys

try:
    from pa_2023_summary_common import parse_esr, run_cli
except ImportError:  # allow running from repo root
    sys.path.insert(0, __import__("os").path.join(
        __import__("os").path.dirname(__file__)))
    from pa_2023_summary_common import parse_esr, run_cli

COUNTY = "Lebanon"

TERM_RE = re.compile(r"[-\s](\d)\s*YEAR\b", re.IGNORECASE)

# "SCHOOL DIRECTOR AT LARGE CORNWALL LEBANON" and "... NORTHERN LEBANON"
# each appear TWICE as separate contests (Vote For 5 -- the regular seats --
# then Vote For 1, in that order in the source).  The rows would otherwise
# collide on (office, district, party, candidate), so the second contest
# section is disambiguated by appending the Vote For count to the office.
# Occurrence order verified against the source summary (Vote For 5 section
# precedes the Vote For 1 section for both districts).
_SCHOOL_DUP = {"CORNWALL LEBANON", "NORTHERN LEBANON"}
_SCHOOL_SEEN = {}


def _term_suffix(header):
    m = TERM_RE.search(header)
    if not m:
        return ""
    return f" ({m.group(1)} Year)"


def _title(name):
    return " ".join(w.capitalize() for w in name.split())


def map_contest(header):
    h = re.sub(r"\s+", " ", header).strip()
    hu = h.upper()

    if hu == "JUSTICE OF THE SUPREME COURT":
        return ("Justice of the Supreme Court", "")
    if hu == "JUDGE OF THE SUPERIOR COURT":
        return ("Judge of the Superior Court", "")
    if hu == "JUDGE OF THE COMMONWEALTH COURT":
        return ("Judge of the Commonwealth Court", "")
    if hu.startswith("JUDGE OF THE COURT OF COMMON PLEAS"):
        m = re.search(r"(\d+)(ST|ND|RD|TH)\s+DISTRICT", hu)
        return ("Judge of the Court of Common Pleas",
                f"{m.group(1)}{m.group(2).lower()} District" if m else "")
    if hu == "COUNTY COMMISSIONER":
        return ("County Commissioner", "")
    if hu == "CONTROLLER":
        return ("County Controller", "")
    if hu == "RECORDER OF DEEDS":
        return ("Recorder of Deeds", "")
    if hu == "TREASURER":
        return ("County Treasurer", "")
    if hu == "CORONER":
        return ("Coroner", "")
    if hu == "PROTHONOTARY":
        return ("Prothonotary", "")

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d{1,2}-\d{2})(?:\s+\S+)?$", hu)
    if m:
        return ("Magisterial District Judge", m.group(1))

    if hu.startswith("SCHOOL DIRECTOR AT LARGE"):
        district = _title(h[len("SCHOOL DIRECTOR AT LARGE"):].strip())
        if district.upper() in _SCHOOL_DUP:
            k = _SCHOOL_SEEN.get(district.upper(), 0)
            _SCHOOL_SEEN[district.upper()] = k + 1
            # 1st section = Vote For 5 (regular seats), 2nd = Vote For 1
            office = f"School Director (Vote For {5 if k == 0 else 1})"
        else:
            office = "School Director"
        return (office, district)

    if hu.startswith("MEMBER CITY COUNCIL"):
        return ("City Council", "Lebanon City")

    if hu.startswith("COUNCIL MEMBER"):
        rest = re.sub(r"^COUNCIL MEMBER[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Borough Council" + _term_suffix(hu), _title(rest))

    if hu.startswith("TOWNSHIP COMMISSIONER"):
        rest = re.sub(r"^TOWNSHIP COMMISSIONER[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Township Commissioner" + _term_suffix(hu), _title(rest))

    if hu.startswith("SUPERVISOR"):
        return ("Township Supervisor", _title(hu[len("SUPERVISOR"):].strip()))

    if hu.startswith("AUDITOR"):
        rest = re.sub(r"^AUDITOR[-\s]*(\d\s*YEAR)?", "", hu).strip()
        return ("Township Auditor" + _term_suffix(hu), _title(rest))

    if hu == "RETENTION QUESTION PANELLA":
        return ("Superior Court Retention - Jack Panella", "")
    if hu == "RETENTION QUESTION STABILE":
        return ("Superior Court Retention - Victor P. Stabile", "")

    return None


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input> <output> [--county NAME]")
    src, dst = args
    if src.lower().endswith(".pdf"):
        from pa_2023_summary_common import text_from_pdf
        src = text_from_pdf(src)
    R = parse_esr(src, COUNTY, map_contest, titlecase_names=True)
    from pa_2023_summary_common import write_csv, check_totals
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")
    for bad in check_totals(R):
        print(f"WARNING {bad[0]}: rows sum {bad[1]} != Total Votes {bad[2]}")