#!/usr/bin/env python3
"""
Shared pdftotext-layout parser core for Electionware-style 2023 General
Election county text extracts (Tioga, Indiana, Northumberland, Montour).

These counties' 2023 source PDFs are Electionware "Summary Results Report" /
"Precinct Report" prints whose pdftotext -layout output separates columns
cleanly, so a deterministic text parser is both faster and more reliable than
the shared natural-pdf engines.  The per-county scripts in
``pa_<county>_general_2023_results_parser.py`` supply:

  * ``map_header(header_line)`` — map a contest header line to
    ``(office, district)``; return None for unrecognized headers (they are
    reported as warnings, never guessed).  Called exactly once per new
    contest section, so per-county counters for positionally-disambiguated
    repeated headers (e.g. Northumberland's two identically-titled
    "SUPERIOR COURT RETENTION" sections) are safe here.
  * ``is_header(line)`` — cheap, side-effect-free test used only to walk
    back through buffered lines when a page break separates a contest
    header from its "Vote For N" line.  Defaults to ``map_header``.
  * ``page_header_key`` — text identifying the page-header line that carries
    the jurisdiction name; the next non-``header_skip`` line is the
    precinct/municipality for the page.  None means every section is
    countywide and ``countywide_precinct`` is used.

The driver implements the Electionware quirks common to these counties:

  * wrapped statistics rows ("Ballots Cast - Total" with the numbers printed
    on the preceding line),
  * repeated contest headers on page continuations of a single section
    (multi-page write-in lists) — detected as continuations, not new
    contests; sections close at "Contest Totals",
  * the optional ``<VOTE %>`` column (stripped before parsing),
  * candidate rows, Yes/No rows, Write-In Totals ("Write-ins"),
    Overvotes/Undervotes rows; named ``Write-In:`` lines and "Not Assigned"
    are skipped (the write-in totals row already includes them),
  * same-key rows within a precinct (multi-seat contests reported as
    separate sections with identical keys) are summed.

Usage (from a county parser):

    rows, warnings = electionware_txt_2023.parse_report(
        load_text(input_path), "Tioga", tioga_map_header, ...)
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import tempfile

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

# Candidate / aggregate rows end with 4 integer tokens:
#   total, election_day, mail/absentee, provisional
TAIL4_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")

# Optional VOTE % column between the total and the election-day column
# (Montour 2023): strip before matching the 4-integer tail.
PCT_RE = re.compile(r"\s+\d+\.\d+%")

PARTY_CODES = [
    "DEM/REP/IND",
    "DEM/REP",
    "REP/IND",
    "DEM/IND",
    "D/R",
    "DEM",
    "REP",
    "LBR",
    "LIB",
    "GRE",
    "GRN",
    "CST",
    "FWD",
    "ASP",
    "DAR",
    "IND",
    "NA",
    "NP",
    "CON",
]
PARTY_RE = re.compile(
    r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$",
    re.IGNORECASE,
)

VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.IGNORECASE)

SINGLE_INT_RE = re.compile(r"^(\d[\d,]*)$")

# A line consisting of exactly four integer tokens (wrapped statistics
# payload or same-line metadata values).
BARE4_RE = re.compile(
    r"^(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$")

# Metadata rows emitted as office-valued rows with empty candidate.
META_OFFICES = {
    "Registered Voters - Total": "Registered Voters",
    "Ballots Cast - Total": "Ballots Cast",
    "Ballots Cast - Blank": "Ballots Cast - Blank",
}

# Lines never treated as contest headers or candidate rows.
PAGE_JUNK_RE = re.compile(
    r"^(?:"
    r"Summary Results Report|Precinct Results Report|Precinct Report"
    r"|OFFICIAL RESULTS|UNOFFICIAL RESULTS"
    r"|Municipal Election|MUNICIPAL GENERAL ELECTION"
    r"|2023 Municipal Election|2023 General Election|2023 Municipal General"
    r"|November \d{1,2},? \d{4}"
    r"|Election Summary -|Precinct Summary -|Precinct Summary November"
    r"|Report generated|Copyright"
    r"|TOTAL\b|Day\b|Absentee/|Ballots$|Statistics$|STATISTICS$"
    r"|Election Day Precincts Reporting|Precincts Complete"
    r"|Precincts Partially Reported|Absentee/ ?Early Precincts Reporting"
    r"|Voter Turnout|Precincts Reporting\b"
    r")"
)


def load_text(input_path: str) -> str:
    """Return the pdftotext -layout text of ``input_path``.

    Accepts either the source PDF (converted via pdftotext, which must be on
    PATH) or a pre-extracted .txt file.
    """
    if input_path.lower().endswith(".pdf"):
        tf = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        tmp = tf.name
        tf.close()
        try:
            subprocess.run(["pdftotext", "-layout", input_path, tmp], check=True)
            with open(tmp, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    with open(input_path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


class _Aggregator:
    """Rows keyed on (precinct, office, district, party, candidate);
    repeated keys (multi-seat contests reported as separate sections with
    identical keys) are summed."""

    def __init__(self):
        self.rows: dict[tuple, dict] = {}
        self.order: list[tuple] = []

    def add(self, precinct, office, district, party, candidate, vals):
        key = (precinct, office, district, party, candidate)
        row = self.rows.get(key)
        if row is None:
            row = self.rows[key] = dict.fromkeys(FIELDNAMES, "")
            row.update(precinct=precinct, office=office, district=district,
                       party=party, candidate=candidate)
            self.order.append(key)
        for field, v in zip(("votes", "election_day", "mail", "provisional"), vals):
            if v is None:
                continue  # e.g. Registered Voters rows carry no breakdown
            row[field] = str(int(row[field] or 0) + v)

    def ordered(self):
        return [self.rows[k] for k in self.order]


def parse_report(
    text: str,
    county: str,
    map_header,
    *,
    page_header_key=None,
    header_skip: tuple = (),
    strip_pct: bool = False,
    countywide_precinct: str | None = None,
    is_header=None,
    page_filter=None,
    prettify_precinct=None,
    fill_district=None,
    log=None,
):
    """Parse Electionware text into OpenElections rows.

    map_header(header) -> (office, district) | None
    page_header_key    -> text on the page-header line carrying the
                          jurisdiction; the next non-``header_skip`` line is
                          the precinct value.  None => every section is
                          countywide and ``countywide_precinct`` is used.
    page_filter(chunk) -> False to skip a whole page (used by Tioga, whose
                          file concatenates a countywide Summary Results
                          Report and the per-municipality Precinct Results
                          Report).
    fill_district(office, precinct) -> (office, district) | None, consulted
                          when a mapped contest carries an empty district
                          (e.g. Tioga's bare "MOC 2/4YR" header inside the
                          Wellsboro Borough Ward One section).

    Returns (rows, warnings).  ``rows`` is a list of dicts with FIELDNAMES
    keys minus ``county`` (the caller assigns it when writing).
    """
    if is_header is None:
        is_header = map_header
    warnings: list[str] = []

    def warn(msg):
        warnings.append(msg)
        if log:
            log("WARN: " + msg)

    agg = _Aggregator()

    if page_filter is not None:
        chunks = re.split(r"Report generated with Electionware", text)
        pages = [p for p in chunks if page_filter(p)]
    else:
        pages = [text]

    precinct = countywide_precinct or ""
    section = None            # (office, district, raw_header)
    pending: list[str] = []   # recent unconsumed, non-junk lines (headers)
    prev_ints = None          # ints-only line (wrapped statistics payload)
    expect_precinct = False

    for page in pages:
        for raw in page.split("\n"):
            s = raw.strip()
            if not s:
                continue

            # Jurisdiction header line: the next non-junk line names the
            # precinct/municipality for the following content.
            if page_header_key is not None and page_header_key in raw:
                expect_precinct = True
                continue
            if expect_precinct:
                if not PAGE_JUNK_RE.match(s) and s not in header_skip \
                        and not VOTE_FOR_RE.match(s) \
                        and not s.startswith(("Write-In", "Not Assigned")) \
                        and not TAIL4_RE.match(s):
                    if prettify_precinct is not None:
                        s = prettify_precinct(s)
                    if s != precinct:
                        precinct = s
                        section = None
                        pending = []
                        prev_ints = None
                    expect_precinct = False
                    continue

            if strip_pct:
                s = PCT_RE.sub("", s)

            if PAGE_JUNK_RE.match(s):
                continue

            # Metadata (Registered Voters / Ballots Cast / Ballots Cast - Blank)
            meta = None
            for label, office in META_OFFICES.items():
                if s.startswith(label):
                    meta = (office, s[len(label):].strip())
                    break
            if meta is not None:
                office, rest = meta
                t4 = BARE4_RE.match(rest)
                if t4:
                    vals = [int(t.replace(",", "")) for t in t4.groups()]
                elif SINGLE_INT_RE.match(rest):
                    vals = [int(rest.replace(",", "")), 0, 0, 0]
                elif prev_ints is not None:
                    vals = list(prev_ints)
                else:
                    warn(f"{county} {precinct}: no numbers for '{office}'")
                    vals = None
                if vals is not None:
                    if office == "Registered Voters":
                        vals = [vals[0], None, None, None]
                    agg.add(precinct, office, "", "", "", vals)
                pending = []
                prev_ints = None
                continue

            # A line consisting solely of numbers: the wrapped statistics
            # payload or a candidate-name fragment; never a header.
            m = BARE4_RE.match(s)
            if m:
                prev_ints = [int(t.replace(",", "")) for t in m.groups()]
                continue

            if VOTE_FOR_RE.match(s):
                if pending:
                    # Page-repeat of the open section (the repeated header
                    # may be separated from "Vote For" by page-break junk).
                    if section is not None and section[2] in pending[-5:]:
                        pending = []
                        continue
                header = None
                for cand in reversed(pending[-5:]):
                    if is_header(cand):
                        header = cand
                        break
                if header is None:
                    if section is None:
                        warn(f"{county} {precinct}: unmapped contest header "
                             f"before '{s}': {pending[-1] if pending else ''!r}")
                    pending = []
                    continue
                mapped = map_header(header)
                if mapped is None:
                    warn(f"{county} {precinct}: unmapped contest header "
                         f"{header!r}")
                    pending = []
                    continue
                office, district = mapped
                if fill_district is not None and not district:
                    rep = fill_district(office, precinct)
                    if rep:
                        office, district = rep
                section = (office, district, header)
                pending = []
                continue

            if s.startswith("Contest Totals"):
                section = None
                pending = []
                continue
            if s.startswith("Total Votes Cast"):
                pending = []
                continue

            if section is not None:
                m = TAIL4_RE.match(s)
                if m:
                    head = m.group(1).strip()
                    vals = [int(t.replace(",", "")) for t in m.groups()[1:]]
                    if head == "":
                        continue
                    if head.upper() in ("YES", "NO"):
                        agg.add(precinct, section[0], section[1], "",
                                head.capitalize(), vals)
                    elif pm := PARTY_RE.match(head):
                        agg.add(precinct, section[0], section[1],
                                pm.group(1).upper(), pm.group(2).strip(), vals)
                    elif head == "Write-In Totals":
                        agg.add(precinct, section[0], section[1], "",
                                "Write-ins", vals)
                    elif head in ("Overvotes", "Undervotes"):
                        agg.add(precinct, section[0], section[1], "",
                                head, vals)
                    elif head == "Not Assigned" or head.startswith("Write-In:"):
                        pass
                    else:
                        warn(f"{county} {precinct}: unrecognized data row in "
                             f"{section[0]!r}: {s[:80]!r}")
                    pending = []
                    continue
                if s.startswith("Write-In:"):
                    pending = []
                    continue

            pending.append(s)
            if len(pending) > 8:
                pending = pending[-8:]

    return agg.ordered(), warnings


def write_csv(rows, output_path: str, county: str):
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        for r in rows:
            w.writerow([county] + [r[c] for c in FIELDNAMES[1:]])


def run(county, map_header, *, page_header_key, header_skip=(),
        strip_pct=False, countywide_precinct=None, is_header=None,
        page_filter=None, prettify_precinct=None, fill_district=None):
    """argv contract: (input_path, output_path), optional --county flag."""
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input_path> <output_path> [--county NAME]")
    rows, warnings = parse_report(
        load_text(args[0]), county, map_header,
        page_header_key=page_header_key, header_skip=header_skip,
        strip_pct=strip_pct, countywide_precinct=countywide_precinct,
        is_header=is_header, page_filter=page_filter,
        prettify_precinct=prettify_precinct, fill_district=fill_district,
        log=lambda m: print(m, file=sys.stderr),
    )
    write_csv(rows, args[1], county)
    print(f"wrote {len(rows)} rows -> {args[1]} ({len(warnings)} warnings)")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)