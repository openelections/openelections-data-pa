#!/usr/bin/env python3
"""
Text-extract (pdftotext -layout) engine for Electionware "Summary Results
Report" precinct PDFs, 2023 PA general election.

The 2023 county PDFs are the same Electionware family that
``electionware_precinct_np`` handles via natural_pdf, but the natural_pdf
rendering times out on large PDFs. pdftotext -layout already separates the
columns cleanly in these files, so this engine parses the text extract
instead. It reproduces the office-normalization conventions of the shared
engine (candidates keep their printed casing; YES/NO retention rows;
"Write-In Totals" -> "Write-ins"; Overvotes/Undervotes rows; split
aggregate merging) via a per-county ``normalize_office`` hook supplied by
each county wrapper.

Config (TxtConfig):
  county              -- county name written to the CSV
  normalize_office    -- callable(header) -> (office, district)
  prettify_precinct   -- callable(precinct name) -> str
  extra_junk          -- extra regexes treated as page-header/footer junk
  party_optional      -- emit non-party-prefixed candidate rows (Centre)
  skip_candidates     -- heads to skip silently (e.g. "NO CANDIDATE FILED")

Row grammar handled (per precinct segment between "Statistics" markers):
  Statistics block:
    Registered Voters - Total [N]           (number may be on the line or
    <nums>                                  on the preceding numbers-only
    Ballots Cast - Total                    line)
    Ballots Cast - Blank
    Voter Turnout - Total
    (Clinton: "Precincts ... Reporting" meta rows -- skipped)
  Contests:
    <office header>
    Vote For N
    [column-header junk]
    [PARTY] <NAME>  total [%] [ed mail prov]
    Write-In Totals / Write-In: ... / Not Assigned / Total Votes Cast /
    Overvotes / Undervotes / Contest Totals

Usage from a county wrapper::

    from electionware_txt import TxtConfig, run_cli
    CFG = TxtConfig(county="Adams", normalize_office=norm, ...)
    if __name__ == "__main__":
        run_cli(CFG)
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from electionware_precinct_np import (  # shared engine, do not edit
    PARTY_RE,
    VOTE_FOR_RE,
    _merge_split_aggregates,
)


# ---------------------------------------------------------------------------
# Line classification.
# ---------------------------------------------------------------------------

# Page title / footer / report-junk fragments. Lines containing any of these
# are never precinct names and never data.
JUNK_SUBSTR = (
    "Summary Results Report",
    "Summary Precinct Results Report",
    "Precinct Summary Results Report",
    "Precinct Results Report",
    "Election Summary Report",
    "Official Precinct Report",
    "RESULT BOOK",
    "OFFICIAL RESULTS",
    "Report generated with Electionware",
    "Precinct Summary - ",
    "Election Summary - ",
    "ALLISON TOWNSHIP - ",  # Clinton per-precinct footers are covered by the
    # generic footer regex below; kept for documentation.
)

# Footer lines: "Precinct Summary - <ts>   Page N of M" / "... 1 of 379" /
# "ALLISON TOWNSHIP - 11/27/2023 ... Page 8 of 8".
FOOTER_RE = re.compile(r"\bPage \d+ of \d+\b|\d+\s+of\s+\d+\s*$")

# Page-header junk: election + date + county lines.
HEADER_JUNK_RE = re.compile(
    r"(?i)(\bnovember\b|\bmunicipal\b|\bcounty\b|\bOFFICIAL RESULTS\b|"
    r"^\s*2023\s+(MUNICIPAL|Municipal|General|GENERAL)|^\s*General\s+2023\b|"
    r"^\s*Municipal\s+2023\s*$|^\s*7\s+November\s+2023)"
)

# Statistics-block labels (never candidate rows).
STATS_META_PREFIXES = (
    "Registered Voters - Total",
    "Registered Voters-Total",
    "Ballots Cast - Total",
    "Ballots Cast - Blank",
    "Voter Turnout",
    "Election Day Precincts Reporting",
    "Precincts Complete",
    "Precincts Partially Reported",
    "Absentee/",
)

NUM_TOKEN_RE = re.compile(r"^\d[\d,]*(?:\.\d+)?%?$")
STAT_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)


def trailing_numbers(line: str, limit: int = 8) -> list[str]:
    """Collect the trailing numeric (or percent) tokens of a line."""
    tokens = line.split()
    out: list[str] = []
    for tok in reversed(tokens):
        if NUM_TOKEN_RE.match(tok):
            out.append(tok)
        else:
            break
        if len(out) >= limit:
            break
    out.reverse()
    return out


def strip_percent(nums: list[str]) -> list[str]:
    """Drop percent (VOTE %) tokens wherever they appear in a numeric run."""
    return [t for t in nums if not t.endswith("%")]


def numbers_only(line: str) -> bool:
    tokens = line.split()
    return bool(tokens) and all(NUM_TOKEN_RE.match(t) for t in tokens)


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------


@dataclass
class TxtConfig:
    county: str
    normalize_office: Callable[[str], tuple[str, str]]
    prettify_precinct: Callable[[str], str] = lambda s: s
    extra_junk: tuple = ()
    party_optional: bool = False
    # Additional party codes printed by this county's report (checked
    # before the shared PARTY_RE); e.g. Adams prints "PFF" for an
    # independent candidate.
    extra_parties: tuple = ()
    skip_candidates: tuple = ("NO CANDIDATE FILED",)
    # Rows dropped wholesale (used by Clinton to drop the county-summary
    # section that precedes the per-precinct pages in the concatenated txt).
    drop_rows: Optional[Callable[[dict], bool]] = None
    # Optional hook to re-label offices that appear multiple times per
    # precinct in the same order (e.g. Clinton's two unnamed Superior Court
    # retention questions). Called as
    # disambiguate(office, district, occurrence_index_within_precinct) and
    # returns the final (office, district).
    disambiguate: Optional[Callable[[str, str, int], tuple[str, str]]] = None
    # Optional predicate on the raw precinct name; segments whose name
    # matches are skipped (e.g. Clinton's embedded cumulative group
    # reports, whose Statistics marker picks up a "Contest Totals ..."
    # pseudo-name from the preceding page).
    drop_precinct: Optional[Callable[[str], bool]] = None


# ---------------------------------------------------------------------------
# Segment extraction.
# ---------------------------------------------------------------------------


def is_junk(line: str, cfg: TxtConfig) -> bool:
    s = line.strip()
    if not s:
        return True
    if any(sub in s for sub in JUNK_SUBSTR):
        return True
    for pat in cfg.extra_junk:
        if re.search(pat, s):
            return True
    if FOOTER_RE.search(s):
        return True
    if HEADER_JUNK_RE.search(s):
        return True
    return False


def find_precinct_name(lines: list[str], marker_idx: int, cfg: TxtConfig) -> Optional[str]:
    """Scan upward from the Statistics marker for the precinct name."""
    for i in range(marker_idx - 1, -1, -1):
        s = lines[i].strip()
        if not s:
            continue
        if STAT_RE.match(s):
            continue
        if is_junk(s, cfg):
            continue
        return s
    return None


def precinct_segments(lines: list[str], cfg: TxtConfig):
    """Yield (precinct_name_raw, seg_lines) per Statistics marker."""
    markers = [i for i, ln in enumerate(lines) if STAT_RE.match(ln.strip())]
    if not markers:
        raise RuntimeError("No 'Statistics' markers found; wrong file format?")
    bounds = markers + [len(lines)]
    for k, start in enumerate(markers):
        name = find_precinct_name(lines, start, cfg)
        seg = lines[start : bounds[k + 1]]
        # Trim the next page's repeated header block (title/date/footer
        # lines and the next precinct's name) off the segment tail: the
        # segment ends at the last footer/junk line.
        cut = len(seg)
        for i in range(len(seg) - 1, -1, -1):
            s = seg[i].strip()
            if not s:
                continue
            if any(sub in s for sub in JUNK_SUBSTR) or FOOTER_RE.search(s):
                cut = i + 1
                break
        seg = seg[:cut]
        yield name, seg


# ---------------------------------------------------------------------------
# Row parsing.
# ---------------------------------------------------------------------------


def parse_segment(
    name: str,
    seg: list[str],
    cfg: TxtConfig,
    warnings: list,
    precinct_names: Optional[set] = None,
):
    rows: list[dict] = []
    current_office: Optional[str] = None
    current_district: str = ""
    last_numeric: Optional[list[str]] = None
    in_stats = False
    office_occurrence: dict[tuple[str, str], int] = {}
    current_occurrence = 0

    lines = [ln.strip() for ln in seg]
    n = len(lines)
    names = precinct_names or set()

    # Office headers: next non-empty line is "Vote For N".
    office_header_idx: set[int] = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, n):
            nxt = lines[j]
            if not nxt:
                continue
            if VOTE_FOR_RE.match(nxt):
                office_header_idx.add(i)
            break

    def emit(office, district, party, candidate, votes, ed, mail, prov):
        if cfg.drop_rows is not None and cfg.drop_rows(
            {
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
            }
        ):
            return
        rows.append(
            {
                "county": cfg.county,
                "precinct": name,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": votes,
                "election_day": ed,
                "mail": mail,
                "provisional": prov,
            }
        )

    def vals_from(nums: list[str]):
        nums = [t.replace(",", "") for t in nums]
        if len(nums) == 4:
            return nums
        if len(nums) == 1:
            return [nums[0], "", "", ""]
        return None

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        if STAT_RE.match(line):
            in_stats = True
            current_office = None
            current_district = ""
            last_numeric = None
            continue

        if idx in office_header_idx:
            office, district = cfg.normalize_office(line)
            key = (office, district)
            office_occurrence[key] = office_occurrence.get(key, 0) + 1
            if cfg.disambiguate is not None:
                office, district = cfg.disambiguate(
                    office, district, office_occurrence[key] - 1
                )
            current_office = office
            current_district = district
            current_occurrence = office_occurrence[key] - 1
            in_stats = False
            last_numeric = None
            continue

        if VOTE_FOR_RE.match(line):
            continue

        if is_junk(line, cfg):
            continue

        # Continuation pages repeat the precinct name at the top; never
        # treat a known precinct name as a data row.
        if line in names:
            continue

        # Statistics-block labels.
        if line.startswith(STATS_META_PREFIXES):
            if line.startswith("Registered Voters"):
                nums = [t.replace(",", "") for t in
                        strip_percent(trailing_numbers(line))]
                if len(nums) == 1:
                    emit(
                        "Registered Voters", "", "", "",
                        nums[0], "", "", "",
                    )
                else:
                    warnings.append(
                        f"{name}: Registered Voters unparsed: {line!r}"
                    )
            elif line.startswith("Ballots Cast - Total") or line.startswith(
                "Ballots Cast-Total"
            ):
                nums = [t.replace(",", "") for t in
                        strip_percent(trailing_numbers(line))]
                if not nums and last_numeric:
                    nums = [t.replace(",", "") for t in strip_percent(last_numeric)]
                if nums and len(nums) == 4:
                    emit("Ballots Cast", "", "", "", *nums)
                elif nums and len(nums) == 1:
                    # TOTAL-only reports (e.g. Centre) print just the total.
                    emit("Ballots Cast", "", "", "", nums[0], "", "", "")
                elif nums:
                    warnings.append(f"{name}: Ballots Cast tokens={nums}")
            elif line.startswith("Ballots Cast - Blank") or line.startswith(
                "Ballots Cast-Blank"
            ):
                nums = [t.replace(",", "") for t in
                        strip_percent(trailing_numbers(line))]
                if not nums and last_numeric:
                    nums = [t.replace(",", "") for t in strip_percent(last_numeric)]
                if nums and len(nums) == 4:
                    emit("Ballots Cast - Blank", "", "", "", *nums)
                elif nums and len(nums) == 1:
                    emit("Ballots Cast - Blank", "", "", "", nums[0], "", "", "")
            # everything else (turnout, precincts reporting) is skipped
            continue

        # Numbers-only line (Ballots Cast values printed above the label).
        if numbers_only(line):
            last_numeric = trailing_numbers(line)
            continue

        # Candidate / aggregate rows.
        raw_tail = trailing_numbers(line)
        nums = strip_percent(raw_tail)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line

        if not nums:
            # Non-numeric non-header lines: only meaningful ones are handled
            # above; anything else is column-header junk or wrapped text.
            continue

        # Drop thousands separators.
        nums = [t.replace(",", "") for t in nums]

        if current_office is None:
            warnings.append(f"{name}: candidate row with no office: {line!r}")
            continue

        # Write-in detail rows ("Write-In: Scattered 1  9" -- the name may
        # itself end in a digit) are skipped before token-count checks.
        if head.upper().startswith("WRITE-IN:"):
            continue

        if len(nums) == 4:
            total, ed, mail, prov = nums
        elif len(nums) == 1:
            total, ed, mail, prov = nums[0], "", "", ""
        else:
            warnings.append(f"{name}: odd token count {nums} for {head!r}")
            continue

        upper = head.upper()
        if upper == "YES":
            emit(current_office, current_district, "", "Yes", total, ed, mail, prov)
            continue
        if upper == "NO":
            emit(current_office, current_district, "", "No", total, ed, mail, prov)
            continue
        if upper == "WRITE-IN TOTALS":
            emit(current_office, current_district, "", "Write-ins", total, ed, mail, prov)
            continue
        if upper == "OVERVOTES":
            emit(current_office, current_district, "", "Overvotes", total, ed, mail, prov)
            continue
        if upper == "UNDERVOTES":
            emit(current_office, current_district, "", "Undervotes", total, ed, mail, prov)
            continue
        if upper.startswith("WRITE-IN:"):
            continue
        if upper == "NOT ASSIGNED" or upper == "TOTAL VOTES CAST" or upper == "CONTEST TOTALS":
            continue
        if upper in cfg.skip_candidates:
            continue

        pm = PARTY_RE.match(head)
        if pm:
            emit(
                current_office, current_district,
                pm.group(1).upper(), pm.group(2).strip(),
                total, ed, mail, prov,
            )
            continue

        extra_party = None
        for p in cfg.extra_parties:
            if head.upper().startswith(p.upper() + " ") and len(head) > len(p):
                extra_party = p.upper()
                emit(
                    current_office, current_district,
                    extra_party, head[len(p):].strip(),
                    total, ed, mail, prov,
                )
                break
        else:
            if cfg.party_optional:
                emit(current_office, current_district, "", head, total, ed, mail, prov)
                continue
            warnings.append(
                f"{name}: unmatched row under {current_office!r}: {line!r}"
            )
        continue

        if cfg.party_optional:
            emit(current_office, current_district, "", head, total, ed, mail, prov)
            continue

        warnings.append(f"{name}: unmatched row under {current_office!r}: {line!r}")

    # Merge split aggregates (multi-page write-in lists), keyed per
    # contiguous run of the same office -- same as the shared engine.
    return _merge_split_aggregates(rows)


# ---------------------------------------------------------------------------
# Top level.
# ---------------------------------------------------------------------------

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]


def txt_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.split("\n")


def parse_txt(path: Path, cfg: TxtConfig):
    lines = txt_lines(path)
    rows: list[dict] = []
    warnings: list[str] = []
    precinct_count = 0
    unnamed_segments = 0
    segs = list(precinct_segments(lines, cfg))
    all_names = {re.sub(r"\s{2,}", " ", cfg.prettify_precinct(nm)).strip()
                 for nm, _ in segs if nm}
    all_names |= {nm.strip() for nm, _ in segs if nm}
    for name, seg in segs:
        if name is None:
            # County-level summary section (e.g. Clinton's first 10 pages)
            # has no precinct name above its Statistics marker; its rows are
            # not precinct data.
            unnamed_segments += 1
            continue
        if cfg.drop_precinct is not None and cfg.drop_precinct(name):
            unnamed_segments += 1
            continue
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", cfg.prettify_precinct(name)).strip()
        rows.extend(parse_segment(pretty, seg, cfg, warnings, all_names))
    return rows, precinct_count, warnings, unnamed_segments


def parse_input(input_path: str, cfg: TxtConfig):
    """Parse a layout-preserving .txt extract, or a PDF via pdftotext."""
    path = Path(input_path)
    if path.suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
        subprocess.run(
            ["pdftotext", "-layout", str(path), str(tmp)], check=True
        )
        return parse_txt(tmp, cfg)
    return parse_txt(path, cfg)


def run_cli(cfg: TxtConfig, argv: Optional[list[str]] = None) -> None:
    argv = list(argv) if argv is not None else sys.argv
    if len(argv) != 3:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} <input.txt|input.pdf> <output.csv>")
    rows, precinct_count, warnings, unnamed = parse_input(argv[1], cfg)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w in warnings[:40]:
            print("  " + w)
        if len(warnings) > 40:
            print("  ...")