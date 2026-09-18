#!/usr/bin/env python3
"""
Parse Lackawanna County PA 2023 General (Municipal) Election results.

Source: "Lackawanna County Certified Precinct Results 2023 General.pdf"
(547 pages, Electionware "Summary Results Report" per-precinct print, 163
precincts).  The pdftotext -layout output (work2023/txt/) separates the
columns cleanly, so this parser works on text via the shared
``electionware_txt_2023`` engine rather than the slow natural-pdf engines.

Format quirks handled here:
  * candidate rows carry a VOTE % column -> ``strip_pct=True``;
  * party code ``DNR`` (Democrat, No Republican cross-endorsement) is not in
    the shared engine's party list -- the engine's PARTY_RE is extended here
    (monkeypatched, shared file untouched) with DNR/AAI;
  * the first page has no preceding "LACKAWANNA PRECINCT - ..." footer, so a
    synthetic footer is prepended to the text so the page_header_key
    machinery names the first precinct too.

Header mapping (office, district):
  * "SUPERIOR COURT - RETAIN JACK PANELLA" -> "Superior Court Retention -
    Jack Panella"; "... RETAIN VICTOR P. STABILE" -> "Superior Court
    Retention - Victor P. Stabile" (both 2023 retentions are Superior Court
    seats);
  * "COURT OF COMMON PLEAS - RETAIN JAMES A. GIBBONS" -> "Court of Common
    Pleas Retention - James A. Gibbons";
  * "MAGISTERIAL DISTRICT JUDGE DISTRICT 45-1-06" -> office "Magisterial
    District Judge", district "45-1-06";
  * "SCRANTON SCHOOL DIRECTOR" -> "School Director" / "Scranton";
    "<DIST> SCHOOL DIRECTOR REGION n [TERM] [REGION n]" -> "School Director
    Region n [ (N Year)]" / "<DIST>"; AT-LARGE -> "School Director";
  * "CITY COUNCIL ... TERM SCRANTON" -> "City Council [(N Year)]" /
    "Scranton"; "CITY CONTROLLER SCRANTON" -> "City Controller";
  * "COUNCIL <boro>" / "COUNCIL [TERM] <boro>" -> "Borough Council [...]" /
    <boro>; "CONTROLLER BLAKELY" -> "Borough Controller";
  * "MAYOR <muni>" [+ "TWO YEAR UNEXPIRED TERM"] -> "Mayor [(2 Year)]";
  * "SUPERVISOR <twp>" -> "Township Supervisor"; "AUDITOR <twp>" ->
    "Township Auditor"; "TAX COLLECTOR <twp>" -> "Tax Collector", each with
    a "(N Year)" suffix when the header names a term.

Usage:
    python parsers/pa_lackawanna_general_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv> [--county Lackawanna]
"""

import re
import sys

try:
    import electionware_txt_2023 as ew
except ImportError:  # allow running from repo root
    sys.path.insert(0, __import__("os").path.join(
        __import__("os").path.dirname(__file__)))
    import electionware_txt_2023 as ew

COUNTY = "Lackawanna"

PAGE_HEADER_KEY = "LACKAWANNA PRECINCT"

# extend the shared engine's party list with Lackawanna's extra codes
_EXTRA_PARTIES = ["DEM/REP/IND", "DNR", "AAI", "AID"]
for _p in reversed(_EXTRA_PARTIES):
    if _p not in ew.PARTY_CODES:
        ew.PARTY_CODES.insert(0, _p)
ew.PARTY_RE = re.compile(
    r"^(" + "|".join(re.escape(p) for p in ew.PARTY_CODES) + r")\s+(.+)$",
    re.IGNORECASE,
)

TERM_RE = re.compile(r"(TWO|THREE|FOUR|SIX|\d)\s*YEAR", re.IGNORECASE)
WORD_NUM = {"TWO": "2", "THREE": "3", "FOUR": "4", "SIX": "6"}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE(?:\s+DISTRICT)?\s+(\d{2}-\d-\d{2})$")

SCHOOL_DISTRICTS = [
    "ABINGTON HEIGHTS", "CARBONDALE AREA", "DUNMORE", "FOREST CITY REGIONAL",
    "LACKAWANNA TRAIL", "LAKELAND", "MID VALLEY", "NORTH POCONO",
    "OLD FORGE", "RIVERSIDE", "SCRANTON", "VALLEY VIEW",
]


def _term_suffix(header):
    m = TERM_RE.search(header)
    if not m:
        return ""
    n = m.group(1).upper()
    n = WORD_NUM.get(n, n)
    return f" ({n} Year)"


def _title(name):
    out = []
    for w in name.split():
        out.append("Mc" + w[2:].capitalize() if w.lower().startswith("mc") and len(w) > 2
                   else w.capitalize())
    return " ".join(out)


def _school(header):
    """Return (rest, region) for '<DIST> SCHOOL DIRECTOR ...' headers."""
    m = re.match(r"^(.*?)\s+SCHOOL DIRECTOR(.*)$", header)
    if not m:
        return None
    district = m.group(1).strip()
    rest = m.group(2).strip()
    region = None
    mr = re.search(r"REGION\s*(\d)$", rest, re.IGNORECASE)
    if mr:
        region = mr.group(1)
        rest = rest[:mr.start()].strip()
    rest = re.sub(r"^(AT-?LARGE)\b", "", rest, flags=re.IGNORECASE).strip()
    term = ""
    mt = re.search(r"(TWO|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM", rest, re.IGNORECASE)
    if mt:
        n = mt.group(1).upper()
        term = f" ({WORD_NUM.get(n, n)} Year)"
        rest = (rest[:mt.start()] + rest[mt.end():]).strip()
    return district, rest, region, term


def map_header(header):
    h = re.sub(r"\s+", " ", header).strip()
    hu = h.upper()

    if hu == "JUSTICE OF THE SUPREME COURT":
        return ("Justice of the Supreme Court", "")
    if hu == "JUDGE OF THE SUPERIOR COURT":
        return ("Judge of the Superior Court", "")
    if hu == "JUDGE OF THE COMMONWEALTH COURT":
        return ("Judge of the Commonwealth Court", "")
    if hu == "JUDGE OF THE COURT OF COMMON PLEAS":
        return ("Judge of the Court of Common Pleas", "")
    if hu.startswith("SUPERIOR COURT - RETAIN JACK PANELLA"):
        return ("Superior Court Retention - Jack Panella", "")
    if hu.startswith("SUPERIOR COURT - RETAIN VICTOR P. STABILE"):
        return ("Superior Court Retention - Victor P. Stabile", "")
    if hu.startswith("COURT OF COMMON PLEAS - RETAIN "):
        judge = _title(hu[len("COURT OF COMMON PLEAS - RETAIN "):].strip())
        return (f"Court of Common Pleas Retention - {judge}", "")

    if hu == "COUNTY COMMISSIONERS":
        return ("County Commissioner", "")
    if hu == "CLERK OF JUDICIAL RECORDS":
        return ("Clerk of Judicial Records", "")
    if hu == "COUNTY CONTROLLER":
        return ("County Controller", "")
    if hu == "COUNTY TREASURER":
        return ("County Treasurer", "")
    if hu == "COUNTY CORONER":
        return ("Coroner", "")

    m = MDJ_RE.match(hu)
    if m:
        return ("Magisterial District Judge", m.group(1))

    school = _school(hu)
    if school:
        district, rest, region, term = school
        office = "School Director"
        if region:
            office += f" Region {region}"
        office += term
        return (office, _title(district))

    if hu.startswith("CITY COUNCIL"):
        rest = _title(re.sub(r"^CITY COUNCIL", "", hu).strip())
        term = ""
        mt = re.search(r"(TWO|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM\b", rest,
                       re.IGNORECASE)
        if mt:
            n = mt.group(1).upper()
            term = f" ({WORD_NUM.get(n, n)} Year)"
            rest = (rest[:mt.start()] + rest[mt.end():]).strip()
        return ("City Council" + term, _title(rest))
    if hu.startswith("CITY CONTROLLER"):
        return ("City Controller", _title(hu[len("CITY CONTROLLER"):].strip()))

    if hu.startswith("MAYOR"):
        rest = _title(hu[len("MAYOR"):].strip())
        term = ""
        mt = re.search(r"(TWO|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM\b", rest,
                       re.IGNORECASE)
        if mt:
            n = mt.group(1).upper()
            term = f" ({WORD_NUM.get(n, n)} Year)"
            rest = (rest[:mt.start()] + rest[mt.end():]).strip()
        return ("Mayor" + term, rest)

    if hu.startswith("CONTROLLER"):
        return ("Borough Controller", _title(hu[len("CONTROLLER"):].strip()))

    if hu.startswith("COUNCIL"):
        rest = _title(re.sub(r"^COUNCIL", "", hu).strip())
        term = ""
        mt = re.search(r"(TWO|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM\b", rest,
                       re.IGNORECASE)
        if mt:
            n = mt.group(1).upper()
            term = f" ({WORD_NUM.get(n, n)} Year)"
            rest = (rest[:mt.start()] + rest[mt.end():]).strip()
        return ("Borough Council" + term, rest)

    if hu.startswith("SUPERVISOR"):
        return ("Township Supervisor", _title(hu[len("SUPERVISOR"):].strip()))

    if hu.startswith("TAX COLLECTOR"):
        rest = _title(hu[len("TAX COLLECTOR"):].strip())
        term = ""
        mt = re.search(r"(TWO|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM\b", rest,
                       re.IGNORECASE)
        if mt:
            n = mt.group(1).upper()
            term = f" ({WORD_NUM.get(n, n)} Year)"
            rest = (rest[:mt.start()] + rest[mt.end():]).strip()
        return ("Tax Collector" + term, rest)

    if hu.startswith("AUDITOR"):
        rest = _title(hu[len("AUDITOR"):].strip())
        term = ""
        mt = re.search(r"(TWO|SIX|FOUR|\d)\s*YEAR(\s+UNEXPIRED)?\s+TERM\b", rest,
                       re.IGNORECASE)
        if mt:
            n = mt.group(1).upper()
            term = f" ({WORD_NUM.get(n, n)} Year)"
            rest = (rest[:mt.start()] + rest[mt.end():]).strip()
        return ("Township Auditor" + term, rest)

    return None


def main():
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input> <output> [--county NAME]")
    src, dst = args
    text = ew.load_text(src)
    # the first page has no footer before it; give the engine one so the
    # first precinct is captured
    text = PAGE_HEADER_KEY + " - 11/27/2023         12:36 PM   0 of 547\n" + text
    rows, warnings = ew.parse_report(
        text, COUNTY, map_header,
        page_header_key=PAGE_HEADER_KEY,
        header_skip=("MUNICIPAL ELECTION", "CERTIFIED RESULTS"),
        strip_pct=True,
        log=lambda m: print(m, file=sys.stderr),
    )
    ew.write_csv(rows, dst, COUNTY)
    print(f"wrote {len(rows)} rows -> {dst} ({len(warnings)} warnings)")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)


if __name__ == "__main__":
    main()