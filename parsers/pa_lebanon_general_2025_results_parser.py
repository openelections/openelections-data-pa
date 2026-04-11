#!/usr/bin/env python3
"""
Parse Lebanon County PA 2025 General (Municipal) Election precinct results.

Source: Lebanon PA Election-Precinct-Summary-V8.pdf (Electionware format
with several Lebanon-specific quirks).

Usage:
    python parsers/pa_lebanon_general_2025_results_parser.py \\
        "<input.pdf>" "<output.csv>"

Uses the shared natural-pdf Electionware parser in
``electionware_precinct_np`` and supplies Lebanon-specific config plus a
custom precinct-block extractor.

Lebanon-specific quirks:
  - **No "Statistics" markers at all.** The PDF has no Registered Voters,
    Ballots Cast, Statistics, Total Votes Cast, Overvotes, Undervotes, or
    Contest Totals rows — just office headers, candidate rows, and
    Write-In Totals. Precinct boundaries are detected by watching the
    line immediately following the "November 4, 2025 Lebanon County"
    header on each page and grouping consecutive pages with the same
    precinct label. Handled via ``precinct_block_extractor``.
  - **Cross-filed candidates use "D/R"** instead of "DEM/REP"; added as
    a shared party code.
  - **Retention headers drop the court name**: "RETENTION QUESTION
    DOUGHERTY". Handled by ``extra_office_handlers`` that maps the
    five expected last names to the canonical
    "<Court> Court Retention - <Full Name>" form.
  - **Mixed-case Yes/No rows**: the shared parser now matches these
    case-insensitively.
  - **Magisterial district number is duplicated**: "MAGISTERIAL
    DISTRICT JUDGE 52-1-01 52-1-01". The shared MDJ regex picks up the
    first token; we post-process to dedupe.
  - **Two duplicated-token offices**: "LEBANON CITY MAYOR LEBANON CITY
    MAYOR" and "MEMBER CITY COUNCIL CITY" (where the final "CITY" is
    Lebanon City). Handled as exact overrides.
  - **INSPECTOR OF ELECTION / JUDGE OF ELECTION** headers include the
    precinct label ("JUDGE OF ELECTION Annville East 18E"). The precinct
    is already on the row, so we strip the trailing precinct name.
  - **Local office municipalities omit Twp/Boro**: "SUPERVISOR BETHEL",
    "MAYOR CLEONA", "TAX COLLECTOR ANNVILLE". The ``municipality
    _normalizer`` just title-cases the remainder.
  - **Hyphenated term tokens**: "AUDITOR-2 YEAR MILLCREEK",
    "SUPERVISOR-4 YEAR NORTH LONDONDERRY". Handled as distinct prefix
    entries.
"""

import re
from typing import Iterable, Optional

import natural_pdf as npdf

from electionware_precinct_np import (
    ElectionwareConfig,
    run_cli,
    title_case,
)


SKIP_PREFIXES = (
    "Summary Results Report Official Reports",
    "Summary Results Report UNOFFICIAL RESULTS",
    "Summary Results Report OFFICIAL RESULTS",
    "2025 General Municipal Election",
    "November 4, 2025 Lebanon County",
    "Precinct Summary - ",
    "Report generated with Electionware",
    "TOTAL ELE",  # mangled vertical-text column header
    "Vote For ",
    "Total Votes Cast",
    "Contest Totals",
)


# --------------------------------------------------------------------------
# Custom precinct-block extractor.
# --------------------------------------------------------------------------

COUNTY_HEADER = "November 4, 2025 Lebanon County"


def lebanon_extract_precinct_blocks(
    pdf: "npdf.PDF", config: ElectionwareConfig
) -> Iterable[tuple[str, str]]:
    """Yield (precinct_name, full_text) for each Lebanon precinct.

    Lebanon's PDF has the precinct label on the line immediately after
    the "November 4, 2025 Lebanon County" header on every page; pages
    with the same label belong to the same precinct. Consecutive pages
    are concatenated into one text block per precinct.
    """
    current: Optional[str] = None
    buf: list[str] = []
    for page in pdf.pages:
        text = page.extract_text() or ""
        lines = text.split("\n")
        label: Optional[str] = None
        for i, line in enumerate(lines):
            if COUNTY_HEADER in line:
                for j in range(i + 1, len(lines)):
                    cand = lines[j].strip()
                    if cand:
                        label = cand
                        break
                break
        if label is None:
            continue
        if label != current:
            if current is not None:
                yield current, "\n".join(buf)
            current = label
            buf = []
        buf.append(text)
    if current is not None and buf:
        yield current, "\n".join(buf)


# --------------------------------------------------------------------------
# Office normalization: retention, exact overrides, local prefix offices.
# --------------------------------------------------------------------------


LEBANON_RETENTION = {
    "RETENTION QUESTION DONOHUE": (
        "Supreme Court Retention - Christine Donohue",
        "",
    ),
    "RETENTION QUESTION DOUGHERTY": (
        "Supreme Court Retention - Kevin M Dougherty",
        "",
    ),
    "RETENTION QUESTION WECHT": (
        "Supreme Court Retention - David Wecht",
        "",
    ),
    "RETENTION QUESTION DUBOW": (
        "Superior Court Retention - Alice Beck Dubow",
        "",
    ),
    "RETENTION QUESTION WOJCIK": (
        "Commonwealth Court Retention - Michael H Wojcik",
        "",
    ),
}


def lebanon_retention(line: str):
    return LEBANON_RETENTION.get(line)


def lebanon_exact_overrides(line: str):
    """Handle Lebanon's one-off header oddities."""
    # Duplicated office token on same line.
    if line == "LEBANON CITY MAYOR LEBANON CITY MAYOR":
        return ("Mayor", "Lebanon City")
    # "MEMBER CITY COUNCIL CITY" — trailing "CITY" is Lebanon City.
    if line == "MEMBER CITY COUNCIL CITY":
        return ("City Council", "Lebanon City")
    # Magisterial District Judge with duplicated district number.
    m = re.match(
        r"^MAGISTERIAL DISTRICT JUDGE\s+(\S+)(?:\s+\1)?$", line
    )
    if m:
        return ("Magisterial District Judge", m.group(1))
    # Inspector/Judge of Election headers embed the precinct name; strip it.
    for prefix, norm in (
        ("INSPECTOR OF ELECTION", "Inspector of Elections"),
        ("JUDGE OF ELECTION", "Judge of Elections"),
    ):
        if line == prefix or line.startswith(prefix + " "):
            return (norm, "")
    return None


EXACT_OFFICES = {
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "SHERIFF": ("Sheriff", ""),
    "CORONER": ("Coroner", ""),
    "TREASURER": ("Treasurer", ""),
    "COUNTY TREASURER": ("Treasurer", ""),
    "CONTROLLER": ("Controller", ""),
    "REGISTER OF WILLS & CLERK OF ORPHANS COURT": (
        "Register of Wills and Clerk of Orphans Court",
        "",
    ),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
}


# Prefix-style local offices. The municipality remainder is just a plain
# name ("BETHEL", "CLEONA", "NORTH LONDONDERRY") — no TWP/BORO token — so
# we title-case it directly. Order: longest prefix wins. Hyphenated
# term-token variants ("AUDITOR-2 YEAR") are enumerated explicitly rather
# than trying to generalize the term regex.
LOCAL_OFFICES = [
    ("SCHOOL DIRECTOR AT LARGE", "School Director At Large"),
    ("COUNCIL MEMBER-2 YEAR", "Borough Council (2 Year)"),
    ("COUNCIL MEMBER-4 YEAR", "Borough Council (4 Year)"),
    ("COUNCIL MEMBER", "Borough Council"),
    ("TOWNSHIP COMMISSIONER", "Township Commissioner"),
    ("AUDITOR-2 YEAR", "Township Auditor (2 Year)"),
    ("AUDITOR-4 YEAR", "Township Auditor (4 Year)"),
    ("AUDITOR-6 YEAR", "Township Auditor (6 Year)"),
    ("SUPERVISOR-2 YEAR", "Township Supervisor (2 Year)"),
    ("SUPERVISOR-4 YEAR", "Township Supervisor (4 Year)"),
    ("SUPERVISOR-6 YEAR", "Township Supervisor (6 Year)"),
    ("TAX COLLECTOR", "Tax Collector"),
    ("SUPERVISOR", "Township Supervisor"),
    ("AUDITOR", "Township Auditor"),
    ("CONSTABLE", "Constable"),
    ("MAYOR", "Mayor"),
]


def lebanon_muni(raw: str) -> str:
    """Title-case the remainder of a prefix-style local office header."""
    return title_case(raw)


CONFIG = ElectionwareConfig(
    county="Lebanon",
    skip_prefixes=SKIP_PREFIXES,
    county_header_suffix="Lebanon County",
    exact_offices=EXACT_OFFICES,
    local_offices=LOCAL_OFFICES,
    local_office_orientation="prefix",
    retention_style="retention",  # unused; handled by extra handler
    extra_office_handlers=[lebanon_retention, lebanon_exact_overrides],
    municipality_normalizer=lebanon_muni,
    include_magisterial=False,  # handled by exact_overrides
    precinct_block_extractor=lebanon_extract_precinct_blocks,
)


if __name__ == "__main__":
    run_cli(CONFIG)
