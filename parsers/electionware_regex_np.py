"""Shared natural_pdf engine for hand-rolled regex parsers over Electionware
precinct summary PDFs (Indiana, Lackawanna).

Textually this is the same Electionware precinct-summary shape that
electionware_precinct_np.py's config-driven engine also handles (see that
module's docstring) -- these two counties simply predate that engine and use
standalone line regexes instead. The two counties differ in several places
that must be preserved exactly since there's no source PDF checked into the
repo to golden-test against:

  - Party code list (Lackawanna adds DNR, AAI)
  - Whether candidate/Yes-No/Write-In-Totals lines carry a "PCT%" token
    (Lackawanna does, Indiana doesn't)
  - The blanket '%'-line skip: Indiana skips ANY line containing '%' (safe,
    since its data lines never contain one); Lackawanna only skips '%' lines
    that also mention 'Turnout' or 'VOTE' (its data lines always contain a
    percentage, so a blanket skip would eat real data)
  - Extra column-header skip lines Lackawanna needs that Indiana's blunter
    SKIP_PREFIXES already covers
  - Write-In Totals dedup: Indiana dedups per (precinct, office, vote_for);
    Lackawanna does not
  - Yes/No candidate casing: Lackawanna always uppercases to YES/NO;
    Indiana preserves whatever the (case-sensitive) regex matched
  - The word used to exclude header noise before reading a precinct name
    off the page header ('OFFICIAL' vs 'CERTIFIED')
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ElectionwareRegexConfig:
    county: str
    county_marker: str  # e.g. "Indiana County" -- locates the precinct name in the page header
    precinct_header_exclude_prefixes: tuple  # lines to skip while scanning for the precinct name
    skip_prefixes: tuple
    office_keywords: tuple
    party_codes: tuple  # e.g. ('DEM', 'REP', 'LBR', ...)
    has_pct_column: bool  # candidate/yesno/write-in lines carry a "PCT%" token
    percent_skip: Callable  # predicate(line) -> bool; county-specific handling of stray '%' lines
    extra_skip: Optional[Callable] = None  # additional county-specific skip predicate
    dedup_write_in_totals: bool = False
    yesno_uppercase_candidate: bool = False
    yesno_pattern: str = r'(Yes|No)'  # Lackawanna matches YES/Yes/NO/No case variants


def _build_regexes(config: ElectionwareRegexConfig):
    party_alt = '|'.join(re.escape(p) for p in config.party_codes)
    pct = r'\s+[\d.]+%' if config.has_pct_column else ''
    candidate_re = re.compile(
        rf'^({party_alt})\s+(.+?)\s+(\d[\d,]*){pct}\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
    )
    yesno_re = re.compile(
        rf'^{config.yesno_pattern}\s+(\d[\d,]*){pct}\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
    )
    write_in_totals_re = re.compile(
        rf'^Write-In Totals\s+(\d[\d,]*){pct}\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
    )
    stats_line_re = re.compile(
        r'^(Registered Voters - Total|Ballots Cast - Total|Ballots Cast - Blank)\s+([\d,]+)\s*([\d,]*)\s*([\d,]*)\s*([\d,]*)$'
    )
    vote_for_re = re.compile(r'^Vote For\s+(\d+)$')
    return candidate_re, yesno_re, write_in_totals_re, stats_line_re, vote_for_re


def clean_votes(val):
    if not val:
        return '0'
    return val.replace(',', '').strip()


def extract_precinct_from_page(lines, config: ElectionwareRegexConfig):
    for i, line in enumerate(lines):
        line = line.strip()
        if config.county_marker in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                candidate = lines[j].strip()
                if candidate and not any(candidate.startswith(p) for p in config.precinct_header_exclude_prefixes):
                    return candidate
    return None


def process_pages(pages, config: ElectionwareRegexConfig):
    """Process a list of pages (each a list of already-split text lines).
    Independent of natural_pdf so it can be unit tested against small text
    fixtures without a real PDF (see tests/test_electionware_regex.py)."""
    candidate_re, yesno_re, write_in_totals_re, stats_line_re, vote_for_re = _build_regexes(config)

    results = []
    current_precinct = None
    current_office = None
    current_vote_for = '1'
    seen_stats = set()
    seen_write_in = set()

    for lines in pages:
        page_precinct = extract_precinct_from_page(lines, config)
        if page_precinct and page_precinct != current_precinct:
            current_precinct = page_precinct
            current_office = None

        if not current_precinct:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if any(line.startswith(p) for p in config.skip_prefixes):
                continue
            if config.percent_skip(line):
                continue
            if config.extra_skip and config.extra_skip(line):
                continue

            if line == current_precinct:
                continue

            stats_match = stats_line_re.match(line)
            if stats_match:
                stat_type = stats_match.group(1)
                key = (current_precinct, stat_type)
                if key not in seen_stats:
                    seen_stats.add(key)
                    candidate_name = {
                        'Registered Voters - Total': 'Registered Voters',
                        'Ballots Cast - Total': 'Ballots Cast',
                        'Ballots Cast - Blank': 'Ballots Cast - Blank',
                    }[stat_type]

                    row = {
                        'county': config.county, 'precinct': current_precinct, 'office': candidate_name,
                        'district': '', 'party': '', 'candidate': '',
                        'vote_for': '', 'votes': clean_votes(stats_match.group(2)),
                        'election_day': '', 'mail': '', 'provisional': '',
                    }
                    if stat_type != 'Registered Voters - Total':
                        row['election_day'] = clean_votes(stats_match.group(3))
                        row['mail'] = clean_votes(stats_match.group(4))
                        row['provisional'] = clean_votes(stats_match.group(5))
                    results.append(row)
                continue

            vf_match = vote_for_re.match(line)
            if vf_match:
                current_vote_for = vf_match.group(1)
                continue

            upper_line = line.upper()
            if any(kw in upper_line for kw in config.office_keywords):
                if not candidate_re.match(line) and not yesno_re.match(line) \
                   and not write_in_totals_re.match(line) and not stats_line_re.match(line) \
                   and not re.match(r'^\d', line):
                    current_office = line
                    continue

            if current_office:
                cand_match = candidate_re.match(line)
                if cand_match:
                    results.append({
                        'county': config.county, 'precinct': current_precinct, 'office': current_office,
                        'district': '', 'party': cand_match.group(1), 'candidate': cand_match.group(2).strip(),
                        'vote_for': current_vote_for,
                        'votes': clean_votes(cand_match.group(3)),
                        'election_day': clean_votes(cand_match.group(4)),
                        'mail': clean_votes(cand_match.group(5)),
                        'provisional': clean_votes(cand_match.group(6)),
                    })
                    continue

                yn_match = yesno_re.match(line)
                if yn_match:
                    candidate_name = yn_match.group(1).upper() if config.yesno_uppercase_candidate else yn_match.group(1)
                    results.append({
                        'county': config.county, 'precinct': current_precinct, 'office': current_office,
                        'district': '', 'party': '', 'candidate': candidate_name,
                        'vote_for': current_vote_for,
                        'votes': clean_votes(yn_match.group(2)),
                        'election_day': clean_votes(yn_match.group(3)),
                        'mail': clean_votes(yn_match.group(4)),
                        'provisional': clean_votes(yn_match.group(5)),
                    })
                    continue

                wi_match = write_in_totals_re.match(line)
                if wi_match:
                    wi_key = (current_precinct, current_office, current_vote_for)
                    if not config.dedup_write_in_totals or wi_key not in seen_write_in:
                        seen_write_in.add(wi_key)
                        results.append({
                            'county': config.county, 'precinct': current_precinct, 'office': current_office,
                            'district': '', 'party': '', 'candidate': 'Write-In Totals',
                            'vote_for': current_vote_for,
                            'votes': clean_votes(wi_match.group(1)),
                            'election_day': clean_votes(wi_match.group(2)),
                            'mail': clean_votes(wi_match.group(3)),
                            'provisional': clean_votes(wi_match.group(4)),
                        })
                    continue

    return results


def parse_electionware_regex_results(pdf_path, config: ElectionwareRegexConfig):
    from natural_pdf import PDF

    pdf = PDF(pdf_path)
    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    pages = []
    for page_idx, page in enumerate(pdf.pages):
        pages.append(page.extract_text().split('\n'))
        if (page_idx + 1) % 200 == 0:
            print(f"  Processed {page_idx + 1} of {total_pages} pages...")

    return process_pages(pages, config)


def write_csv(results, output_path):
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def run_cli(config: ElectionwareRegexConfig, argv=None):
    import sys
    from pathlib import Path

    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(f"Usage: uv run python {sys.argv[0]} <input_pdf> <output_csv>")
        sys.exit(1)

    pdf_path, output_path = argv

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results = parse_electionware_regex_results(pdf_path, config)
    write_csv(results, output_path)
