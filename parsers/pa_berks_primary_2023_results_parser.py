#!/usr/bin/env python3
"""Parse Berks County PA 2023 Municipal Primary (May 16, 2023) results.

Sources (Electionware "Summary Results Report", pdftotext -layout text):

  * ``Berks_County_Official_Summary_Results_2023_Primary.txt`` — countywide
    summary (480 contests).  Produces the county-level 9-column CSV.
  * ``Berks_County_Official_Precinct_Results_2023_Primary.txt`` — per-precinct
    report (2,029 pages, 202 precincts).  Produces the 10-column precinct CSV.

Both files share the same section layout: a party-prefixed contest header
(``DEM Justice of the Supreme Court`` / ``REP Auditor Albany Twp``) directly
above a ``Vote For N`` line, candidate rows, ``Write-In Totals`` +
named ``Write-In:`` detail rows + ``Not Assigned``.  The party lives on the
**contest header** (closed primary); every candidate row under it inherits
that party, and the aggregate ``Write-ins`` row carries that contest party
(see the precinct-engine note below for why it is not empty).

Office/district conventions mirror ``2023/counties/20231107__pa__general__berks__precinct.csv``
(the county's general-2023 file):
  * ``Member Of Council Ku Ward 1``     -> Borough Council / Kutztown Boro Ward 1
  * ``Member Of Council <rest>``        -> Borough Council / <rest>
  * ``Auditor <rest>``                  -> Township Auditor / <rest> (boros too)
  * ``Supervisor / Commissioner <rest>``-> Township Supervisor / Township Commissioner
  * ``Mayor City of Reading``           -> Mayor / City of Reading
  * ``Council President City of Reading``-> Council President / Reading
  * ``City Council District N``         -> City Council District N / Reading
  * ``School Director X School District[ Region N]``
      -> School Director[ Region N] / <X minus "School District">
  * ``Magisterial District Judge District 23-1-02`` -> Magisterial District Judge / 23-1-02

The county-summary loop is county-specific because the shared
``pa_2023_summary_common.parse_esr`` STATISTICS block expects an office word
at the start of the first contest header; Berks primary headers start with
``DEM``/``REP``.  The precinct engine is the shared ``electionware_txt_2023`` text driver with
two Berks-primary adaptations applied in a pre-pass (the shared driver's
general-election layout expects a party code on every candidate row, which
closed-primary reports do not carry):

  * candidate rows are annotated with the open section's party token
    (``Daniel McCaffery  52 44 8 0`` -> ``DEM Daniel McCaffery  52 44 8 0``)
    so the driver's ``PARTY_RE`` branch picks the party up per row;
  * the DEM/REP party is also embedded in the mapped office as a tab marker
    so the driver's aggregator keeps the two parties' sections (and their
    aggregate ``Write-ins`` rows) separate; the marker is split back out
    before writing.  Write-in aggregate rows carry the contest party (DEM/REP),
    following Berks's own 2024 primary file — a closed primary has two
    write-in contests per office, and party-empty rows would collide.

Usage:
    python parsers/pa_berks_primary_2023_results_parser.py <input> <output>
Input is auto-detected: a path containing "precinct" produces the
precinct CSV (10 columns), anything else the county summary CSV (9 columns).
"""

import os
import re
import sys

try:
    from pa_2023_summary_common import (
        Rows, nums_from, split_candidate, vals_from,
        is_junk, write_csv, text_from_pdf,
    )
    from electionware_txt_2023 import (
        parse_report, load_text, write_csv as write_precinct_csv, TAIL4_RE,
    )
except ImportError:  # allow running from repo root
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from pa_2023_summary_common import (
        Rows, nums_from, split_candidate, vals_from,
        is_junk, write_csv, text_from_pdf,
    )
    from electionware_txt_2023 import (
        parse_report, load_text, write_csv as write_precinct_csv, TAIL4_RE,
    )


COUNTY = "Berks"

EXACT_OFFICES = {
    "Justice of the Supreme Court": ("Justice of the Supreme Court", ""),
    "Judge of the Superior Court": ("Judge of the Superior Court", ""),
    "Judge of the Commonwealth Court": ("Judge of the Commonwealth Court", ""),
    "Judge of the Court of Common Pleas": ("Judge of the Court of Common Pleas", ""),
    "District Attorney": ("District Attorney", ""),
    "County Commissioner": ("County Commissioner", ""),
    "County Controller": ("County Controller", ""),
    "County Treasurer": ("County Treasurer", ""),
    "Clerk of Courts": ("Clerk of Courts", ""),
    "Recorder of Deeds": ("Recorder of Deeds", ""),
    "Register of Wills": ("Register of Wills", ""),
    "Sheriff": ("Sheriff", ""),
}

MDJ_RE = re.compile(r"^Magisterial District Judge District (\d{2}-\d-\d{2})$", re.I)
CITY_COUNCIL_RE = re.compile(r"^City Council (District \d+)$", re.I)
KU_WARD_RE = re.compile(r"^Ku Ward (\d+)$", re.I)
SCHOOL_RE = re.compile(
    r"^(.+?) School District(?:\s+Region\s+(\d+))?$", re.I)
PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$")


def map_contest(header):
    """Map a party-prefixed contest header to (office, district, party).

    Returns None for unrecognized headers (reported as a problem, never
    guessed).
    """
    h = re.sub(r"\s+", " ", header.strip())
    m = PARTY_PREFIX_RE.match(h)
    if not m:
        return None
    party = m.group(1)
    rest = m.group(2).strip()

    if rest in EXACT_OFFICES:
        office, district = EXACT_OFFICES[rest]
        return office, district, party

    m = MDJ_RE.match(rest)
    if m:
        return "Magisterial District Judge", m.group(1), party

    m = CITY_COUNCIL_RE.match(rest)
    if m:
        return f"City Council {m.group(1)}", "Reading", party

    if rest == "Council President City of Reading":
        return "Council President", "Reading", party

    m = re.match(r"^Mayor\s+(.+)$", rest, re.I)
    if m:
        return "Mayor", m.group(1).strip(), party

    m = re.match(r"^Member Of Council\s+(.+)$", rest, re.I)
    if m:
        tail = m.group(1).strip()
        ku = KU_WARD_RE.match(tail)
        if ku:
            tail = f"Kutztown Boro Ward {ku.group(1)}"
        return "Borough Council", tail, party

    m = re.match(r"^Commissioner\s+(.+)$", rest, re.I)
    if m:
        return "Township Commissioner", m.group(1).strip(), party

    m = re.match(r"^Supervisor\s+(.+)$", rest, re.I)
    if m:
        return "Township Supervisor", m.group(1).strip(), party

    m = re.match(r"^Auditor\s+(.+)$", rest, re.I)
    if m:
        return "Township Auditor", m.group(1).strip(), party

    m = re.match(r"^Tax Collector\s+(.+)$", rest, re.I)
    if m:
        return "Tax Collector", m.group(1).strip(), party

    m = SCHOOL_RE.match(rest)
    if m:
        district = m.group(1).strip()
        region = m.group(2)
        office = "School Director"
        if region:
            office += f" Region {region}"
        return office, district, party

    return None


# --------------------------------------------------------------------------
# county summary engine
# --------------------------------------------------------------------------

MUNICIPAL_PRIMARY_JUNK = re.compile(r"municipal primary", re.IGNORECASE)


def is_junk_berks(stripped):
    if MUNICIPAL_PRIMARY_JUNK.search(stripped):
        return True
    return bool(is_junk(stripped))


def parse_county_summary(text_path, county, map_contest):
    """Parse the Berks Electionware county summary into a Rows object.

    Same section layout as pa_2023_summary_common.parse_esr, but contest
    headers carry the DEM/REP party prefix and the STATISTICS block must
    stop at the first party-prefixed header.
    """
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    current = None
    i = 0
    rv = None
    bc = None
    seen_stats = False

    header_lines = set()
    for k, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                header_lines.add(j)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        # -- STATISTICS block ------------------------------------------------
        if "STATISTICS" in stripped.upper() and not seen_stats:
            seen_stats = True
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if re.match(r"^registered voters - total", s, re.I):
                    n = nums_from(s.split())
                    rv = int(n[0]) if n else 0
                elif re.match(r"^ballots cast - total", s, re.I):
                    bc = nums_from(s.split())
                elif re.match(r"^(DEM|REP)\s", s, re.I):
                    break
                i += 1
            continue

        # -- contest header: line just above "Vote For N" ---------------------
        if re.match(r"^vote for \d+$", stripped, re.I):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                current = map_contest(lines[j].strip())
                R.contests.append(current)
            i += 1
            continue

        if i in header_lines:
            i += 1
            continue

        if is_junk_berks(stripped):
            i += 1
            continue

        if current is None or not any(c.isdigit() for c in stripped):
            i += 1
            continue

        tokens = stripped.split()
        nums = nums_from(tokens[1:]) if tokens else []
        lead = tokens[0].upper().rstrip(":") if tokens else ""

        if stripped.upper().startswith("WRITE-IN TOTALS"):
            total, ed, mi, pr = vals_from(nums)
            # party = contest party (DEM/REP).  The brief's "party empty"
            # write-in rule is for general files, where each office has a
            # single contest; in a closed primary both parties' write-in
            # rows would collide on (office, district, '', 'Write-ins') and
            # trip the duplicate_entries data test, and party attribution
            # would be lost.  Berks's own 2024 primary file carries the
            # contest party on its write-in rows.
            R.add(current[0], current[1], current[2], "Write-ins",
                  total, ed, mi, pr)
        elif stripped.upper().startswith("WRITE-IN:"):
            pass  # detail row, already included in Write-In Totals
        elif re.match(r"^not assigned", stripped, re.I):
            pass
        elif lead in ("YES", "NO"):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], current[2], lead.capitalize(),
                  total, ed, mi, pr)
        else:
            head, cand_party, allnums = split_candidate(tokens)
            # keep names exactly as printed (the county's general-2023 file
            # convention, e.g. "Harry F. Smail, Jr."); no CANON rewriting
            name = " ".join(head).strip()
            if name and allnums:
                total, ed, mi, pr = vals_from(allnums)
                R.add(current[0], current[1], current[2], name,
                      total, ed, mi, pr)
        i += 1

    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc:
        R.add_meta("Ballots Cast", bc[0],
                   bc[1] if len(bc) > 1 else "",
                   bc[2] if len(bc) > 2 else "",
                   bc[3] if len(bc) > 3 else "")
    return R


# --------------------------------------------------------------------------
# precinct engine (shared electionware_txt_2023 driver)
# --------------------------------------------------------------------------

MUNICIPAL_PRIMARY_LINE = re.compile(r"^2023 Municipal Primary$", re.IGNORECASE)

# rows that must NOT receive a party annotation
SPECIAL_HEADS = ("Write-In Totals", "Not Assigned", "Overvotes", "Undervotes")


def _is_special_head(head):
    return (head in SPECIAL_HEADS or head.startswith("Write-In:")
            or head.upper() in ("YES", "NO"))


def berks_precinct_preprocess(text):
    """Drop page-furniture lines the shared driver's junk list misses and
    annotate candidate rows with the open section's party token (the driver
    only accepts candidate rows carrying a party code)."""
    out = []
    party = None
    for raw in text.split("\n"):
        s = raw.strip()
        if MUNICIPAL_PRIMARY_LINE.match(s):
            continue
        m = PARTY_PREFIX_RE.match(s)
        if m:
            party = m.group(1)
            out.append(raw)
            continue
        if s and party:
            m4 = TAIL4_RE.match(s)
            if m4:
                head = m4.group(1).strip()
                if head and not _is_special_head(head):
                    out.append(party + " " + s)
                    continue
        out.append(raw)
    return "\n".join(out)


def berks_map_header(header):
    """(office, district) for the precinct driver, party embedded as a tab
    marker on the office token (split back out before writing)."""
    mapped = map_contest(header)
    if mapped is None:
        return None
    office, district, party = mapped
    return (office + "\t" + party, district)


def run_precinct(src, dst):
    text = berks_precinct_preprocess(load_text(src))
    rows, warnings = parse_report(
        text, COUNTY, berks_map_header,
        page_header_key="Berks County",
        header_skip=("STATISTICS",),
        page_filter=lambda chunk: "Precinct Summary" in chunk,
    )
    # split the party marker back out of the office values
    mismatches = 0
    for r in rows:
        if "\t" in r["office"]:
            office, marker_party = r["office"].split("\t", 1)
            r["office"] = office
            if r["candidate"] == "Write-ins":
                # write-in rows carry the contest party (see run_county note)
                r["party"] = marker_party
            elif r["party"] and r["party"] != marker_party:
                mismatches += 1
                print(f"WARN: party mismatch {r['precinct']} {office}: "
                      f"row {r['party']} vs header {marker_party}",
                      file=sys.stderr)
            else:
                r["party"] = marker_party
    write_precinct_csv(rows, dst, COUNTY)
    print(f"wrote {len(rows)} rows -> {dst}")
    unmapped = [w for w in warnings if "unmapped" in w]
    print(f"warnings: {len(warnings)} ({len(unmapped)} unmapped headers, "
          f"{mismatches} party mismatches)")
    for w in warnings[:30]:
        print("WARN:", w, file=sys.stderr)
    return rows, warnings


def run_county(src, dst):
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    R = parse_county_summary(src, COUNTY, map_contest)
    # Berks repeats identical contest headers for a municipality's separate
    # seats (e.g. three "REP Auditor Bern Twp" Vote-For-1 sections); merge
    # same-key rows so the county file carries one row per
    # (office, district, party, candidate), matching the precinct engine's
    # aggregator convention.  Totals are unchanged by the merge.
    merged = {}
    order = []
    for r in R.rows:
        key = tuple(r[1:5])
        if key in merged:
            m = merged[key]
            for k in range(5, 9):
                a = m[k] or "0"
                b = r[k] or "0"
                m[k] = str(int(a) + int(b)) if (m[k] or r[k]) else ""
        else:
            merged[key] = list(r)
            order.append(key)
    R.rows = [merged[k] for k in order]
    n = write_csv(dst, R)
    unmapped = [c for c in R.contests if c is None]
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len([c for c in R.contests if c is not None])}"
          f" ({len(unmapped)} unmapped)")
    return R


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt-or-.pdf> <output.csv>")
    src, dst = args
    if "precinct" in os.path.basename(src).lower():
        run_precinct(src, dst)
    else:
        R = run_county(src, dst)
        bad = [c for c in R.contests if c is None]
        if bad:
            sys.exit(f"ERROR: unmapped contest headers present: {bad[:5]}")