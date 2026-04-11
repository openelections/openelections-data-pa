#!/usr/bin/env python3
"""
Shared natural-pdf based parser for Electionware precinct PDFs.

Individual county parsers construct an ``ElectionwareConfig`` describing
their PDF's quirks and call ``run_cli(config)``. This module handles all
of the shared machinery: precinct boundary detection via the "Statistics"
marker, office-header look-ahead, statistics row parsing, party-prefixed
candidate rows, YES/NO retention rows, and Write-In / Overvotes /
Undervotes.

Currently used by the Huntingdon, Cameron, and Snyder parsers.
See ``NATURAL_PDF_EVALUATION.md`` for the evaluation that led to this
module and which pieces of natural-pdf are used.
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import natural_pdf as npdf


# ---------------------------------------------------------------------------
# Regex building blocks shared across Electionware PDFs.
# ---------------------------------------------------------------------------

# Party codes observed in PA Electionware PDFs. Order matters: "DEM/REP"
# must come before "DEM" so cross-filed candidates match first.
PARTY_CODES = ["DEM/REP", "DEM", "REP", "LBR", "LIB", "GRN", "CST", "FWD", "ASP", "DAR"]
PARTY_RE = re.compile(r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$")

# Candidate / aggregate rows end with 4 integer tokens:
#   total, election_day, mail, provisional
VOTE_TAIL_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$"
)
# Registered Voters has a single total.
SINGLE_TAIL_RE = re.compile(r"^(.*?)\s+(\d[\d,]*)$")

# Default term token: "2YR", "4YR", "6YR" (case-insensitive — Mifflin uses "2yr").
TERM_TOKEN_RE = re.compile(r"^(\d+)YR$", re.IGNORECASE)
# Huntingdon-style term token with a count suffix: "4YR/1", "6YR/2".
TERM_TOKEN_SLASH_RE = re.compile(r"^(\d+)YR/\d+$", re.IGNORECASE)

SMALL_WORDS = {"of", "the", "and", "for", "in", "to", "a", "at", "on"}
# Narrower set matching the original Huntingdon fallback (no "at"/"on").
SMALL_WORDS_NARROW = {"of", "the", "and", "for", "in", "to", "a"}
ROMAN_RE = re.compile(r"^[IVX]+$")


# ---------------------------------------------------------------------------
# String helpers.
# ---------------------------------------------------------------------------


def _title_case_with(s: str, small_words: set[str]) -> str:
    out = []
    for i, w in enumerate(s.split()):
        if ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif i > 0 and w.lower() in small_words:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def title_case(s: str) -> str:
    """Title-case a string, preserving Roman numerals and keeping
    small words ("of", "the", ...) lowercase except in the first position."""
    return _title_case_with(s, SMALL_WORDS)


def title_case_narrow(s: str) -> str:
    """Same as title_case but with a narrower small-words set (no "at"/"on").
    Matches the original Huntingdon fallback behavior."""
    return _title_case_with(s, SMALL_WORDS_NARROW)


def simple_capitalize(s: str) -> str:
    """Capitalize every word, no small-word handling (Huntingdon-style)."""
    return " ".join(w.capitalize() for w in s.split())


def expand_muni_abbrev(raw: str) -> str:
    """TWP -> Township, BORO -> Borough; title-case with Mc fix.
    Used by counties (Snyder) where local office headers use abbreviations."""
    out = []
    for t in raw.split():
        up = t.upper()
        if up == "TWP":
            out.append("Township")
        elif up == "BORO":
            out.append("Borough")
        elif t.startswith("#"):
            out.append(t)
        elif up.startswith("MC") and len(t) >= 3:
            out.append("Mc" + t[2:].capitalize())
        else:
            out.append(t.capitalize())
    return " ".join(out)


def _cap_preserving_mc(word: str) -> str:
    """Capitalize ``word`` while preserving Mc prefix (McVeytown, McClure)."""
    if len(word) >= 3 and word[:2].lower() == "mc":
        return "Mc" + word[2:].capitalize()
    return word.capitalize()


def expand_muni_flexible(raw: str) -> str:
    """Expand Twp/TWP -> Township, Boro/BORO -> Borough, then title-case
    every letter run (handles ALL-CAPS, mixed-case, hyphens, underscores,
    and Mc-prefixed names).

    Examples:
      "Armagh Twp"                        -> "Armagh Township"
      "Burnham Boro"                      -> "Burnham Borough"
      "McVeytown Boro"                    -> "McVeytown Borough"
      "ARMAGH TOWNSHIP-EAST"              -> "Armagh Township-East"
      "BROWN TOWNSHIP-BIG VALLEY_REEDSVILLE"
                                          -> "Brown Township-Big Valley_Reedsville"
    """
    s = re.sub(r"\bTwp\b", "Township", raw, flags=re.IGNORECASE)
    s = re.sub(r"\bBoro\b", "Borough", s, flags=re.IGNORECASE)
    return re.sub(r"[A-Za-z]+", lambda m: _cap_preserving_mc(m.group(0)), s)


def prettify_all_caps_precinct(name: str) -> str:
    """ADAMS TOWNSHIP -> Adams Township; MCCLURE BOROUGH -> McClure Borough.
    Used by counties whose precinct names are ALL-CAPS in the PDF."""
    out = []
    for word in name.split():
        if word.startswith("#"):
            out.append(word)
        elif len(word) >= 3 and word.upper().startswith("MC"):
            out.append("Mc" + word[2:].capitalize())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def prettify_huntingdon_precinct(name: str) -> str:
    """Title-case each ALL-CAPS run, preserving punctuation and digits.
    Preserves Mc prefix (McClure, McVeytown).

    Examples:
      HOPEWELL/PUTTSTOWN -> Hopewell/Puttstown
      HUNTINGDON 1       -> Huntingdon 1
      MCVEYTOWN BOROUGH  -> McVeytown Borough
      ARMAGH TOWNSHIP-EAST -> Armagh Township-East
    """
    return re.sub(r"[A-Z]+", lambda m: _cap_preserving_mc(m.group(0)), name)


def identity(s: str) -> str:
    return s


# ---------------------------------------------------------------------------
# Retention regex builder.
# ---------------------------------------------------------------------------


def make_retention_re(style: str) -> re.Pattern[str]:
    """
    Build the retention regex for a county.

    Styles:
      "retention"       - "SUPREME COURT RETENTION - CHRISTINE DONOHUE"
      "retain"          - "SUPREME COURT - RETAIN CHRISTINE DONOHUE"
      "retention-loose" - Huntingdon-style, dash optional, tail may be
                          empty or contain initials like "D.W."
    """
    if style == "retention":
        return re.compile(
            r"^(SUPREME|SUPERIOR|COMMONWEALTH) COURT RETENTION\s*-\s*(.+)$",
            re.IGNORECASE,
        )
    if style == "retain":
        return re.compile(
            r"^(SUPREME|SUPERIOR|COMMONWEALTH) COURT\s*-\s*RETAIN\s+(.+)$",
            re.IGNORECASE,
        )
    if style == "retention-loose":
        return re.compile(
            r"^(SUPREME|SUPERIOR|COMMONWEALTH) COURT RETENTION\s*-?\s*(.*)$",
            re.IGNORECASE,
        )
    raise ValueError(f"Unknown retention style: {style!r}")


# ---------------------------------------------------------------------------
# Config dataclass.
# ---------------------------------------------------------------------------


OfficeHandler = Callable[[str], Optional[tuple[str, str]]]


@dataclass
class ElectionwareConfig:
    """Per-county knobs for the shared Electionware parser."""

    county: str
    skip_prefixes: tuple[str, ...]
    county_header_suffix: str  # e.g. "Huntingdon County", "CAMERON COUNTY"

    # Office-header normalization.
    exact_offices: dict[str, tuple[str, str]] = field(default_factory=dict)
    local_offices: list[tuple[str, str]] = field(default_factory=list)
    local_office_orientation: str = "prefix"  # "prefix" or "suffix"
    retention_style: str = "retention"
    title_case_retention_tail: bool = True

    # Term-token handling in prefix-style local office headers.
    term_token_re: re.Pattern[str] = TERM_TOKEN_RE
    drop_term_token: bool = False  # True = silently drop; False = "(N Year)"

    include_common_pleas: bool = True
    include_magisterial: bool = True

    # Municipality normalizer for local office headers (suffix side).
    municipality_normalizer: Callable[[str], str] = title_case

    # County-specific hook points. Each handler returns (office, district)
    # or None to fall through.
    school_director_handler: Optional[OfficeHandler] = None
    extra_office_handlers: list[OfficeHandler] = field(default_factory=list)

    # Precinct-name prettifier (applied once before parse_precinct_rows).
    prettify_precinct: Callable[[str], str] = identity

    # Fallback used when no other rule matches an office header.
    fallback_title_case: Callable[[str], str] = title_case


# ---------------------------------------------------------------------------
# Office normalization.
# ---------------------------------------------------------------------------


def normalize_office(raw: str, config: ElectionwareConfig) -> tuple[str, str]:
    """Return (office, district) for a raw office header line."""
    line = raw.strip()

    if line in config.exact_offices:
        return config.exact_offices[line]

    # County-specific handlers first (highest priority after exact).
    for handler in config.extra_office_handlers:
        result = handler(line)
        if result is not None:
            return result

    # Retention.
    m = make_retention_re(config.retention_style).match(line)
    if m:
        court = m.group(1).capitalize()
        tail = m.group(2).strip()
        if config.title_case_retention_tail and tail:
            tail = title_case(tail)
        if tail:
            return (f"{court} Court Retention - {tail}", "")
        return (f"{court} Court Retention", "")

    # Court of Common Pleas (strip judicial district suffix). Case-insensitive
    # so both "JUDGE OF THE COURT OF COMMON PLEAS 20th Judicial District" and
    # the mixed-case "Judge of the Court of Common Pleas" variants match.
    if config.include_common_pleas and line.lower().startswith(
        "judge of the court of common pleas"
    ):
        return ("Judge of the Court of Common Pleas", "")

    # Magisterial District Judge. Accepts both "MAGISTERIAL DISTRICT JUDGE
    # DISTRICT 20-3-01" (Huntingdon) and "Magisterial District Judge 58-3-2"
    # (Mifflin) — the "DISTRICT" keyword is optional and matching is
    # case-insensitive.
    if config.include_magisterial:
        m = re.match(
            r"Magisterial District Judge(?:\s+District)?\s+(.+)$",
            line,
            re.IGNORECASE,
        )
        if m:
            return ("Magisterial District Judge", m.group(1).strip())

    # County-specific school director handler.
    if config.school_director_handler is not None:
        result = config.school_director_handler(line)
        if result is not None:
            return result

    # Local offices.
    if config.local_office_orientation == "prefix":
        for prefix, norm in config.local_offices:
            if line == prefix:
                return (norm, "")
            if line.startswith(prefix + " "):
                remainder = line[len(prefix):].strip().split()
                years: Optional[str] = None
                if remainder:
                    tm = config.term_token_re.match(remainder[0])
                    if tm:
                        years = tm.group(1)
                        remainder = remainder[1:]
                if years is not None and not config.drop_term_token:
                    office = f"{norm} ({years} Year)"
                else:
                    office = norm
                district = (
                    config.municipality_normalizer(" ".join(remainder))
                    if remainder
                    else ""
                )
                return (office, district)
    elif config.local_office_orientation == "suffix":
        # Optionally strip a trailing term token ("4YR", "2yr", etc.).
        work = line
        trailing_years: Optional[str] = None
        tokens = work.split()
        if tokens:
            tm = config.term_token_re.match(tokens[-1])
            if tm:
                trailing_years = tm.group(1)
                work = " ".join(tokens[:-1])
        for suffix, norm in config.local_offices:
            prefix_text: Optional[str] = None
            if work == suffix:
                prefix_text = ""
            elif work.endswith(" " + suffix):
                prefix_text = work[: -len(suffix)].strip()
            if prefix_text is None:
                continue
            if trailing_years is not None and not config.drop_term_token:
                office = f"{norm} ({trailing_years} Year)"
            else:
                office = norm
            district = (
                config.municipality_normalizer(prefix_text) if prefix_text else ""
            )
            return (office, district)
    else:
        raise ValueError(
            f"Unknown local_office_orientation: {config.local_office_orientation!r}"
        )

    # Fallback: title-case the whole line, no district.
    return (config.fallback_title_case(line), "")


# ---------------------------------------------------------------------------
# Precinct block extraction.
# ---------------------------------------------------------------------------


def extract_precinct_blocks(
    pdf, config: ElectionwareConfig
) -> Iterable[tuple[str, str]]:
    """Yield (precinct_name, text) tuples, one per precinct."""
    # Accept both "Statistics" (title case; most counties) and "STATISTICS"
    # (Juniata). natural-pdf's :contains is case-sensitive, so we query twice
    # and merge.
    stat_hits = [
        el
        for el in pdf.find_all('text:contains("Statistics")')
        if el.text.strip() == "Statistics"
    ] + [
        el
        for el in pdf.find_all('text:contains("STATISTICS")')
        if el.text.strip() == "STATISTICS"
    ]
    if not stat_hits:
        raise RuntimeError("No 'Statistics' markers found; wrong PDF format?")
    # Re-sort by (page number, top) since we merged two result lists.
    stat_hits.sort(key=lambda el: (el.page.number, el.top))

    start_pages = [el.page.number for el in stat_hits]
    precinct_names: list[str] = []
    for el in stat_hits:
        above_region = el.page.region(top=0, bottom=el.top)
        above_text = (above_region.extract_text() or "").strip().split("\n")
        name: Optional[str] = None
        for line in reversed(above_text):
            line = line.strip()
            if not line:
                continue
            # Skip the "Statistics" marker even when column-header text
            # runs into the same visual row (Mifflin: "Statistics TOTAL
            # ElectionMail VotesProvisional").
            if line.startswith("Statistics") or line.startswith("STATISTICS"):
                continue
            if line.startswith(config.skip_prefixes):
                continue
            if line.endswith(config.county_header_suffix):
                continue
            name = line
            break
        if name is None:
            raise RuntimeError(
                f"Could not find precinct name above Statistics on page {el.page.number}"
            )
        precinct_names.append(name)

    total_pages = len(pdf.pages)
    for i, (start, name) in enumerate(zip(start_pages, precinct_names)):
        end = (start_pages[i + 1] - 1) if i + 1 < len(start_pages) else total_pages
        chunks = [pdf.pages[p - 1].extract_text() or "" for p in range(start, end + 1)]
        yield name, "\n".join(chunks)


# ---------------------------------------------------------------------------
# Row parsing.
# ---------------------------------------------------------------------------


def parse_votes(tokens: list[str]) -> tuple[int, int, int, int]:
    return tuple(int(t.replace(",", "")) for t in tokens)  # type: ignore[return-value]


def parse_precinct_rows(
    precinct: str, text: str, config: ElectionwareConfig
) -> list[dict]:
    rows: list[dict] = []
    current_office: Optional[str] = None
    current_district: str = ""

    lines = [ln.strip() for ln in text.split("\n")]

    # Merge wrapped Write-In continuation lines: a line with no digits
    # following a "Write-In:" line is treated as a continuation.
    merged: list[str] = []
    for ln in lines:
        if (
            merged
            and merged[-1].startswith("Write-In:")
            and ln
            and not re.search(r"\d", ln)
        ):
            merged[-1] = merged[-1] + " " + ln
        else:
            merged.append(ln)
    lines = merged

    # Office headers = lines whose next non-empty line starts with "Vote For".
    office_header_idx: set[int] = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            if nxt.startswith("Vote For"):
                office_header_idx.add(i)
            break

    def add(office, district, party, candidate, vals):
        total, ed, mail, prov = vals
        rows.append(
            {
                "county": config.county,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "mail": mail,
                "provisional": prov,
            }
        )

    for idx, line in enumerate(lines):
        if not line:
            continue
        if line.startswith(config.skip_prefixes):
            continue
        if line.startswith("Statistics") or line.startswith("STATISTICS"):
            continue

        if idx in office_header_idx:
            office, district = normalize_office(line, config)
            current_office = office
            current_district = district
            continue

        # Statistics rows.
        if line.startswith("Registered Voters - Total"):
            m = SINGLE_TAIL_RE.match(line)
            if m:
                rows.append(
                    {
                        "county": config.county,
                        "precinct": precinct,
                        "office": "Registered Voters",
                        "district": "",
                        "party": "",
                        "candidate": "",
                        "votes": int(m.group(2).replace(",", "")),
                        "election_day": "",
                        "mail": "",
                        "provisional": "",
                    }
                )
            continue
        if line.startswith("Ballots Cast - Total"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                vals = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                add("Ballots Cast", "", "", "", vals)
            continue
        if line.startswith("Ballots Cast - Blank"):
            m = VOTE_TAIL_RE.match(line)
            if m:
                vals = parse_votes([m.group(i) for i in (2, 3, 4, 5)])
                add("Ballots Cast Blank", "", "", "", vals)
            continue

        vote_m = VOTE_TAIL_RE.match(line)
        if vote_m is None:
            continue

        head = vote_m.group(1).strip()
        vals = parse_votes([vote_m.group(i) for i in (2, 3, 4, 5)])

        if current_office is None:
            continue

        if head in ("YES", "NO"):
            add(current_office, current_district, "", head.capitalize(), vals)
            continue

        pm = PARTY_RE.match(head)
        if pm:
            add(current_office, current_district, pm.group(1), pm.group(2).strip(), vals)
            continue

        if head == "Write-In Totals":
            add(current_office, current_district, "", "Write-ins", vals)
            continue
        if head.startswith("Write-In:"):
            continue
        if head == "Not Assigned":
            continue
        if head == "Overvotes":
            add(current_office, current_district, "", "Overvotes", vals)
            continue
        if head == "Undervotes":
            add(current_office, current_district, "", "Undervotes", vals)
            continue

    return rows


# ---------------------------------------------------------------------------
# Top-level orchestration.
# ---------------------------------------------------------------------------


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


def parse_pdf(pdf_path: Path, config: ElectionwareConfig) -> tuple[list[dict], int]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    precinct_count = 0
    for precinct_name, text in extract_precinct_blocks(pdf, config):
        precinct_count += 1
        pretty = config.prettify_precinct(precinct_name)
        rows.extend(parse_precinct_rows(pretty, text, config))
    return rows, precinct_count


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def run_cli(config: ElectionwareConfig, argv: Optional[list[str]] = None) -> None:
    """Standard two-argument CLI for county parsers."""
    argv = list(argv) if argv is not None else sys.argv
    if len(argv) != 3:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count = parse_pdf(pdf_path, config)
    write_csv(rows, out_path)
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )
