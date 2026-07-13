"""Shared natural_pdf engine for "Statement of Votes Cast by Geography" /
"Official Results by Precinct" style reports (Wayne, Lycoming).

Both counties share: "Precinct <Name>" boundary lines, candidate data lines
shaped "Name TOTAL PCT% ED MI PR", multi-line write-in accumulation, and
Yes/No retention rows. They differ only in how the contest header spells out
registered voters/turnout:

  - Wayne: two physical lines --
        "OFFICE (Vote for N)"
        "NNN ballots (...), NNN registered voters, turnout NN.NN%"
    Ballots Cast is read directly from the second line.

  - Lycoming: one physical line --
        "Office (Vote for N), NNN registered voters, turnout NN.NN%"
    Ballots Cast is derived: round(registered_voters * turnout_pct / 100).

Fulton uses a structurally different report (keyword-based office detection,
party-coded candidate lines with continuation lines, no vote_for/write-in
accumulation) and is intentionally NOT part of this shared engine -- see
pa_fulton_general_2025_results_parser.py.

The line-processing state machine (``process_lines``) takes a plain list of
text lines and is independent of natural_pdf, so it can be unit tested
against small text fixtures without a real PDF (see tests/test_sovc_geo.py).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_PRECINCT_RE = re.compile(r'^Precinct\s+(.+)$')
DEFAULT_DATA_LINE_RE = re.compile(
    r'^(.+?)\s+(\d[\d,]*)\s+[\d.]+%\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)$'
)


@dataclass(frozen=True)
class SovcGeoConfig:
    county: str
    skip_prefixes: tuple
    contest_re: re.Pattern
    ballots_re: Optional[re.Pattern] = None  # set for two-line headers (Wayne); None = one-line (Lycoming)
    precinct_re: re.Pattern = DEFAULT_PRECINCT_RE
    data_line_re: re.Pattern = DEFAULT_DATA_LINE_RE
    skip_over_undervotes: bool = False
    extra_skip: Optional[Callable] = None  # optional predicate(line) -> bool for county-specific skip lines
    # Exact-match (not prefix) line that marks the start of a countywide
    # roll-up section appearing AFTER all real "Precinct <name>" sections --
    # every contest repeats with countywide totals, with no new precinct
    # marker to distinguish it. Without this, those rows get silently
    # misattributed to whichever precinct was last seen (see Wayne's "All
    # Precincts" section on the last ~26 pages of its 2025 general PDF,
    # which starts with a standalone "All Precincts" line -- easy to miss
    # because it's also a *prefix* of the unrelated per-page report-header
    # line "All Precincts, All Districts, ..."). When this exact line is
    # seen, current_precinct is cleared so the roll-up rows that follow are
    # dropped (no precinct means "if not current_precinct: continue") rather
    # than attached to the wrong precinct.
    countywide_marker: Optional[str] = None


def clean_votes(val):
    if not val:
        return '0'
    return val.replace(',', '').strip()


class _ParseState:
    def __init__(self):
        self.results = []
        self.current_precinct = None
        self.current_office = None
        self.current_vote_for = '1'
        self.seen_precincts = set()
        self.pending_office = None
        self.writein_accum = None
        # (precinct, office) -> {'votes', 'election_day', 'mail', 'provisional'}
        # as printed on the report's own "Total" line for that contest --
        # side-channel only, never written to the output CSV.
        self.printed_totals = {}


def _flush_writein(state: _ParseState, county: str):
    accum, precinct, office = state.writein_accum, state.current_precinct, state.current_office
    if accum is None or precinct is None or office is None:
        return
    state.results.append({
        'county': county,
        'precinct': precinct,
        'office': office,
        'district': '',
        'party': '',
        'candidate': 'Write-in',
        'vote_for': state.current_vote_for,
        'votes': str(accum['total']),
        'election_day': str(accum['ed']),
        'mail': str(accum['mi']),
        'provisional': str(accum['pr']),
    })


def _emit_stats(state: _ParseState, county: str, reg_voters: str, ballots_cast: str):
    if state.current_precinct in state.seen_precincts:
        return
    state.seen_precincts.add(state.current_precinct)
    for office, votes in (('Registered Voters', reg_voters), ('Ballots Cast', ballots_cast)):
        state.results.append({
            'county': county, 'precinct': state.current_precinct, 'office': office,
            'district': '', 'party': '', 'candidate': '',
            'vote_for': '', 'votes': votes, 'election_day': '', 'mail': '', 'provisional': '',
        })


def process_lines(lines, config: SovcGeoConfig, state: Optional[_ParseState] = None) -> _ParseState:
    """Feed a flat list of already-extracted text lines through the state
    machine. Pass the same ``state`` across successive calls (e.g. one per
    PDF page) to carry precinct/office/write-in context across page breaks,
    matching the original per-county parsers' behavior."""
    if state is None:
        state = _ParseState()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Must be checked before skip_prefixes: the countywide marker is
        # often also a *prefix* of an unrelated per-page header line (e.g.
        # Wayne's "All Precincts" section marker vs. the page header "All
        # Precincts, All Districts, ..."), so an exact match has to win.
        if config.countywide_marker and line == config.countywide_marker:
            _flush_writein(state, config.county)
            state.writein_accum = None
            state.pending_office = None
            state.current_precinct = None
            state.current_office = None
            continue

        if any(line.startswith(p) for p in config.skip_prefixes):
            continue

        if config.skip_over_undervotes and (line.startswith('Overvotes') or line.startswith('Undervotes')):
            continue

        if config.extra_skip and config.extra_skip(line):
            continue

        prec_match = config.precinct_re.match(line)
        if prec_match:
            _flush_writein(state, config.county)
            state.writein_accum = None
            state.pending_office = None

            state.current_precinct = prec_match.group(1).strip()
            state.current_office = None
            continue

        if not state.current_precinct:
            continue

        contest_match = config.contest_re.match(line)
        if contest_match:
            _flush_writein(state, config.county)
            state.writein_accum = None

            if config.ballots_re is not None:
                # Two-line variant: this line only names the office; the
                # ballots/registered-voters line follows separately.
                state.pending_office = contest_match.group(1).strip()
                state.current_vote_for = contest_match.group(2)
            else:
                # One-line variant: office + vote_for + reg_voters + turnout together.
                state.current_office = contest_match.group(1).strip()
                state.current_vote_for = contest_match.group(2)
                reg_voters = clean_votes(contest_match.group(3))
                turnout_pct = float(contest_match.group(4))
                ballots_cast = str(round(int(reg_voters) * turnout_pct / 100))
                _emit_stats(state, config.county, reg_voters, ballots_cast)
            continue

        if config.ballots_re is not None:
            ballots_match = config.ballots_re.match(line)
            if ballots_match and state.pending_office:
                state.current_office = state.pending_office
                state.pending_office = None

                ballots_cast = clean_votes(ballots_match.group(1))
                reg_voters = clean_votes(ballots_match.group(2))
                _emit_stats(state, config.county, reg_voters, ballots_cast)
                continue

        if not state.current_office:
            continue

        data_match = config.data_line_re.match(line)
        if not data_match:
            continue

        name = data_match.group(1).strip()
        total = clean_votes(data_match.group(2))
        ed = clean_votes(data_match.group(3))
        mi = clean_votes(data_match.group(4))
        pr = clean_votes(data_match.group(5))

        if name == 'Total':
            if state.current_precinct is not None and state.current_office is not None:
                state.printed_totals[(state.current_precinct, state.current_office)] = {
                    'votes': total, 'election_day': ed, 'mail': mi, 'provisional': pr,
                }
            _flush_writein(state, config.county)
            state.writein_accum = None
            continue

        if name == 'Write-in':
            if state.writein_accum is None:
                state.writein_accum = {'total': 0, 'ed': 0, 'mi': 0, 'pr': 0}
            state.writein_accum['total'] += int(total)
            state.writein_accum['ed'] += int(ed)
            state.writein_accum['mi'] += int(mi)
            state.writein_accum['pr'] += int(pr)
            continue

        state.results.append({
            'county': config.county,
            'precinct': state.current_precinct,
            'office': state.current_office,
            'district': '',
            'party': '',
            'candidate': name,
            'vote_for': state.current_vote_for,
            'votes': total,
            'election_day': ed,
            'mail': mi,
            'provisional': pr,
        })

    return state


def parse_text(text: str, config: SovcGeoConfig):
    """Parse a single blob of already-extracted text (test/debug helper).
    Returns (results, printed_totals)."""
    state = process_lines(text.split('\n'), config)
    _flush_writein(state, config.county)
    return state.results, state.printed_totals


def parse_sovc_geo_results(pdf_path, config: SovcGeoConfig):
    """Parse a Statement-of-Votes-Cast-by-geography PDF using ``config``.
    Returns (results, printed_totals)."""
    from natural_pdf import PDF

    pdf = PDF(pdf_path)
    state = _ParseState()

    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    for page_idx, page in enumerate(pdf.pages):
        text = page.extract_text()
        process_lines(text.split('\n'), config, state)

        if (page_idx + 1) % 50 == 0:
            print(f"  Processed {page_idx + 1} of {total_pages} pages...")

    _flush_writein(state, config.county)

    return state.results, state.printed_totals


def check_printed_totals(results, printed_totals):
    """Compare summed candidate (+ write-in) votes per (precinct, office)
    against the report's own printed "Total" line for that contest.
    Returns a list of mismatch dicts; empty means everything reconciled.
    Contests with no printed Total line (e.g. cut off by page extraction)
    are silently skipped, not counted as mismatches.
    """
    summed = {}
    for row in results:
        if row['office'] in ('Registered Voters', 'Ballots Cast'):
            continue
        key = (row['precinct'], row['office'])
        acc = summed.setdefault(key, {'votes': 0, 'election_day': 0, 'mail': 0, 'provisional': 0})
        for field in ('votes', 'election_day', 'mail', 'provisional'):
            val = row.get(field) or '0'
            try:
                acc[field] += int(val)
            except ValueError:
                pass

    mismatches = []
    for key, printed in printed_totals.items():
        actual = summed.get(key)
        if actual is None:
            continue
        for field in ('votes', 'election_day', 'mail', 'provisional'):
            try:
                printed_val = int(printed[field])
            except (ValueError, TypeError):
                continue
            if actual[field] != printed_val:
                mismatches.append({
                    'precinct': key[0], 'office': key[1], 'field': field,
                    'printed': printed_val, 'summed': actual[field],
                })
    return mismatches


def write_csv(results, output_path):
    """Write results to OpenElections CSV format."""
    fieldnames = ['county', 'precinct', 'office', 'district', 'party',
                  'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional']

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Wrote {len(results)} results to {output_path}")


def run_cli(config: SovcGeoConfig, argv=None):
    import sys
    from pathlib import Path

    argv = argv if argv is not None else sys.argv[1:]
    strict = '--strict' in argv
    argv = [a for a in argv if a != '--strict']

    if len(argv) != 2:
        print(f"Usage: uv run python {sys.argv[0]} <input_pdf> <output_csv> [--strict]")
        sys.exit(1)

    pdf_path, output_path = argv

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Parsing {pdf_path}...")
    results, printed_totals = parse_sovc_geo_results(pdf_path, config)
    write_csv(results, output_path)

    mismatches = check_printed_totals(results, printed_totals)
    checked = len(printed_totals)
    print(f"verification: {checked} contests with a printed total checked, "
          f"{len(mismatches)} mismatches")
    if mismatches:
        for m in mismatches[:20]:
            print(f"  MISMATCH {m['precinct']} / {m['office']} [{m['field']}]: "
                  f"printed={m['printed']} summed={m['summed']}", file=sys.stderr)
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more", file=sys.stderr)
        if strict:
            sys.exit(1)
