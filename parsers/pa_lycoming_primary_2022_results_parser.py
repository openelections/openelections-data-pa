#!/usr/bin/env python3
"""
Lycoming County, PA 2022 General Primary parser.

Source: "Lycoming PA 2022 General Primary SOVC.pdf" — Electionware
"Official Results - Statement of Votes Cast" crosstab, the same vendor layout
family as the county's 2020 primary SOVC
(parsers/primary/pa_lycoming_primary_2020_results_parser.py). Every logical
page is one contest PANEL shown as a matrix: precincts as rows, candidates as
columns, plus a leading "Total Votes" column; the banner and contest title
repeat atop every logical page and the last page of each panel ends with a
countywide "Total" row:

    United States Senator (Rep) (Vote for 1)
    Precinct                           Total       Kathy      Mehmet ...
                                  Votes       Barnette         Oz ...
    Precinct Anthony                    241           55          93 ...
    ...
    Total                              19129          4461        5919 ...

Contests with more candidates than fit one panel width are SPLIT into
several panels that repeat the same contest title and the same 81 precinct
rows (e.g. Governor (Rep): panel 1 has Barletta/Mastriano/Zama/White/Hart/
McSwain/Gerow, panel 2 has Corman/Gale/Write-in; Member of Republican State
Committee has a named-candidates panel with two Write-in columns and a
Write-in-only panel). The row's "Total Votes" is the FULL contest total,
i.e. the sum over ALL panels' candidates, so per-panel sums being below it
is expected and used as a cross-panel check instead.

Candidate names wrap across 2-3 stacked header lines, so the parser assigns
each header token to its nearest header column by x-position and reads data
rows positionally (one number per column, left to right). Checks performed:
  - every data row's number count == column count;
  - per contest+panel+precinct the panel's row total is consistent across
    panels, and the sum of ALL panels' candidates == the printed
    "Total Votes" (warning only — some rows carry a small source-side
    discrepancy, e.g. the GOP U.S. Senate tabulation);
  - per contest, our countywide per-candidate sums equal the printed
    countywide "Total" row's candidate columns (hard failure otherwise);
    printed Write-in columns across all panels of a contest are merged.

Contests in the vendor export: U.S. Senator (Dem/Rep), Governor (Dem/Rep),
Lieutenant Governor (Dem/Rep), Representative in Congress (9th/15th District)
(Dem/Rep), Representative in the General Assembly (83rd/84th District)
(Dem/Rep), Member of Democratic State Committee (Vote for 1),
Member of Republican State Committee (Vote for 3). No State Senate or local
contests appear, and no per-precinct registered-voters/ballots-cast figures
are printed, so no Registered Voters / Ballots Cast rows are emitted.

The crosstab shows "All Boxes" — no per-precinct vote-type breakdown — so the
7-column output header is used. Every printed "Write-in" column (across all
panels) is merged into a single "Write Ins" row per precinct, carrying the
contest's party per the repo-wide primary convention.

The report is the county's 2022-05-24 official crosstab, tabulated before the
June Oz/McCormick U.S. Senate recount closed (no amended per-precinct
Lycoming source exists); the GOP U.S. Senate rows are kept as tabulated and
the residual vs the certified anchor is documented in the verification notes.

Usage: python pa_lycoming_primary_2022_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys
from collections import defaultdict

import pdfplumber

COUNTY = 'Lycoming'

CONTEST_RE = re.compile(r'^(.+?)\s+\((Dem|Rep)\)\s+\(Vote for (\d+)\)\s*$')

OFFICE_MAP = {
    'United States Senator': ('U.S. Senate', ''),
    'Governor': ('Governor', ''),
    'Lieutenant Governor': ('Lieutenant Governor', ''),
    'Member of Democratic State Committee': ('Member of Democratic State Committee', ''),
    'Member of Republican State Committee': ('Member of Republican State Committee', ''),
}

# banner lines repeated at the top of every logical page (the "Page: N of 356"
# suffix on line 1 is stripped before matching)
IGNORE_TEXTS = {
    'Official Results - Statement of Votes Cast',
    'May 17, 2022 General Primary',
    'Lycoming County',
    'All Precincts, All Districts, All ScanStations, All Contests, All Boxes',
}
TOTAL_BALLOTS_RE = re.compile(r'^Total Ballots Cast:\s*[\d,]+$')
NUMBER_RE = re.compile(r'^[\d,]+$')
WRITEIN_RE = re.compile(r'^write[-\s]?ins?$', re.I)

# name columns sit left of x=150; number columns start at ~169
NAME_X_MAX = 150
NUM_X_MIN = 160
HEADER_STACK_TOL = 30  # px: how far a wrapped name token may sit from its column
# (stacked names wrap with up to ~20px shift; the nearest of the ~40px-spaced
# header columns still wins)


class ParseError(Exception):
    pass


class Panel:
    def __init__(self):
        self.columns = None  # col 0 is Total Votes; each: {'text', 'header_x'}
        self.rows = 0
        self.row_totals = {}  # precinct -> printed Total Votes
        self.printed_total = None  # countywide row numbers (total + candidates)


class Contest:
    def __init__(self, title_text, party):
        self.title_text = title_text.strip()
        self.party = party.upper()
        self.office, self.district = self._normalize(self.title_text)
        self.panels = []
        self.current_panel = None

    @staticmethod
    def _normalize(raw):
        office = raw.strip()
        ush = re.match(
            r'^Representative in Congress\s\((\d+)(?:st|nd|rd|th)?\s+District\)$', office)
        if ush:
            return 'U.S. House', ush.group(1)
        sha = re.match(
            r'^Representative in the General Assembly\s'
            r'\((\d+)(?:st|nd|rd|th)?\s+District\)$', office)
        if sha:
            return 'State House', sha.group(1)
        return OFFICE_MAP.get(office, (office, ''))

    def new_panel(self):
        self.current_panel = Panel()
        self.panels.append(self.current_panel)
        return self.current_panel


def page_lines(page):
    """Group a physical page's words into visual lines (top-to-bottom)."""
    lines = defaultdict(list)
    for w in page.extract_words(x_tolerance=1.5, y_tolerance=3):
        lines[round(w['top'], 1)].append(w)
    return [sorted(ws, key=lambda w: w['x0']) for _, ws in sorted(lines.items())]


def is_data_row(line):
    return (line[0]['text'] == 'Precinct'
            and any(NUMBER_RE.match(w['text']) and w['x0'] >= NUM_X_MIN for w in line))


def is_total_row(line):
    return (line[0]['text'] == 'Total'
            and any(NUMBER_RE.match(w['text']) and w['x0'] >= NUM_X_MIN for w in line))


def read_columns(page_no, line, following):
    """Column header line -> [{'text','header_x'}] with col 0 = Total Votes;
    stacked wrapped-name lines (the lines between this header and the first
    data row / next title) are attributed to their nearest column.
    Returns (cols, lines_consumed)."""
    cols = [{'text': '__total__', 'header_x': line[1]['x0']}]
    for w in line[1:]:
        if w['text'] in ('Total', 'Votes'):
            continue
        cols.append({'text': w['text'], 'header_x': w['x0']})
    if len(cols) < 2:
        raise ParseError(f'page {page_no}: no candidate columns in header')
    j = 0
    while j < len(following):
        nline = following[j]
        ntext = ' '.join(w['text'] for w in nline)
        nt = re.sub(r'\s*Page:\s*\d+\s+of\s+\d+\s*$', '', ntext).strip()
        if (CONTEST_RE.match(ntext) or nt in IGNORE_TEXTS
                or TOTAL_BALLOTS_RE.match(nt) or is_data_row(nline)
                or is_total_row(nline)):
            break
        for w in nline:
            if w['text'] == 'Votes':
                continue
            best = min(cols[1:], key=lambda c: abs(c['header_x'] - w['x0']))
            if abs(best['header_x'] - w['x0']) > HEADER_STACK_TOL:
                raise ParseError(
                    f'page {page_no}: wrapped name token {w["text"]!r} does not '
                    f'sit under a header column (x={w["x0"]})')
            best['text'] = (best['text'] + ' ' + w['text']).strip()
        j += 1
    return cols, j


def emit_precinct_rows(county, contest, panel, precinct, nums, out, warnings):
    total, cand = nums[0], nums[1:]
    if sum(cand) > total:
        # sum above the printed Total Votes would mean misassigned columns
        raise ParseError(f'{contest.title_text} ({contest.party}) {precinct!r}: '
                         f'candidates sum {sum(cand)} > Total Votes {total}')
    writein = 0
    for votes, col in zip(cand, panel.columns[1:]):
        if WRITEIN_RE.match(col['text']):
            writein += votes
        else:
            out.append({'county': county, 'precinct': precinct,
                        'office': contest.office, 'district': contest.district,
                        'party': contest.party, 'candidate': col['text'],
                        'votes': votes})
    # emit the merged Write Ins row even when zero (Lycoming's own 2022
    # general output keeps "Write Ins,0" rows, as do sibling 2022 primary files)
    out.append({'county': county, 'precinct': precinct,
                'office': contest.office, 'district': contest.district,
                'party': contest.party, 'candidate': 'Write Ins',
                'votes': writein})


def merge_writeins(results):
    """Sum the Write Ins rows of one (office, district, party, precinct) that
    came from several panels/columns of the same contest."""
    seen = {}
    merged = []
    for r in results:
        if r['candidate'] == 'Write Ins':
            key = (r['precinct'], r['office'], r['district'], r['party'])
            if key in seen:
                seen[key]['votes'] += r['votes']
            else:
                seen[key] = r
                merged.append(r)
        else:
            merged.append(r)
    return merged


def parse_pdf(path, county=COUNTY):
    results = []
    contests = {}
    order = []
    current = None
    warnings = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            lines = page_lines(page)
            i = 0
            while i < len(lines):
                line = lines[i]
                text = ' '.join(w['text'] for w in line)
                core = re.sub(r'\s*Page:\s*\d+\s+of\s+\d+\s*$', '', text).strip()

                if core in IGNORE_TEXTS or TOTAL_BALLOTS_RE.match(core):
                    i += 1
                    continue

                m = CONTEST_RE.match(text)
                if m:
                    key = (m.group(1), m.group(2).upper())
                    if key not in contests:
                        contests[key] = Contest(m.group(1), m.group(2))
                        order.append(contests[key])
                    current = contests[key]
                    current.new_panel()
                    i += 1
                    continue

                if current is None:
                    i += 1
                    continue

                # column header: 'Precinct ... Total ...' with no numbers
                if (line[0]['text'] == 'Precinct' and 'Total' in text
                        and not is_data_row(line)):
                    cols, j = read_columns(pno, line, lines[i + 1:])
                    current.current_panel.columns = cols
                    i += j + 1
                    continue

                if is_total_row(line):
                    panel = current.current_panel
                    if panel.columns is None:
                        raise ParseError(f'page {pno}: Total row before column header')
                    nums = [int(w['text'].replace(',', '')) for w in line
                            if NUMBER_RE.match(w['text']) and w['x0'] >= NUM_X_MIN]
                    if len(nums) != len(panel.columns):
                        raise ParseError(
                            f'page {pno}: countywide Total row has {len(nums)} '
                            f'numbers, expected {len(panel.columns)}')
                    if panel.printed_total is not None and panel.printed_total != nums:
                        raise ParseError(
                            f'page {pno}: conflicting countywide Total rows '
                            f'{panel.printed_total} vs {nums}')
                    panel.printed_total = nums
                    i += 1
                    continue

                if is_data_row(line):
                    panel = current.current_panel
                    if panel.columns is None:
                        raise ParseError(f'page {pno}: data row before column header')
                    precinct = ' '.join(w['text'] for w in line
                                        if w['x0'] < NAME_X_MAX and w['text'] != 'Precinct'
                                        ).strip()
                    nums = [int(w['text'].replace(',', '')) for w in line
                            if NUMBER_RE.match(w['text']) and w['x0'] >= NUM_X_MIN]
                    if len(nums) != len(panel.columns):
                        raise ParseError(
                            f'page {pno} precinct {precinct!r}: {len(nums)} numbers '
                            f'vs {len(panel.columns)} columns')
                    emit_precinct_rows(county, current, panel, precinct, nums,
                                       results, warnings)
                    panel.row_totals[precinct] = nums[0]
                    panel.rows += 1
                    i += 1
                    continue

                i += 1

    results = merge_writeins(results)
    results.sort(key=lambda r: (r['precinct'], r['office'], r['district'],
                                r['party'], r['candidate']))
    return results, order, warnings


def run_checks(contests, results):
    """Verification 1: printed countywide Total rows vs our sums."""
    bad = 0
    for c in contests:
        key = (c.office, c.district, c.party)
        ours = defaultdict(int)
        precincts = set()
        for r in results:
            if (r['office'], r['district'], r['party']) == key:
                name = 'Write-in' if r['candidate'] == 'Write Ins' else r['candidate']
                ours[name] += r['votes']
                precincts.add(r['precinct'])
        if not precincts:
            print(f'CHECK {c.title_text} ({c.party}): NO ROWS')
            bad += 1
            continue
        # panel consistency of the printed countywide totals
        totals = {p.printed_total[0] for p in c.panels if p.printed_total}
        if len(totals) > 1:
            print(f'CHECK {c.title_text} ({c.party}): panel countywide totals differ: '
                  f'{sorted(totals)}')
            bad += 1
        printed = c.panels[0].printed_total or c.panels[-1].printed_total
        if printed is None:
            print(f'CHECK {c.title_text} ({c.party}): no printed countywide Total row')
            bad += 1
            continue
        # sum printed write-in columns across all panels of the contest
        printed_writein = sum(
            v for p in c.panels if p.printed_total
            for col, v in zip(p.columns[1:], p.printed_total[1:])
            if WRITEIN_RE.match(col['text']))
        # named candidates: compare against whichever panel lists them
        for p in c.panels:
            if not p.printed_total:
                continue
            for col, pval in zip(p.columns[1:], p.printed_total[1:]):
                if WRITEIN_RE.match(col['text']):
                    continue
                o = ours.get(col['text'], 0)
                if o != pval:
                    bad += 1
                    print(f'MISMATCH {c.title_text} ({c.party}) {col["text"]}: '
                          f'ours {o} vs printed {pval}')
        o = ours.get('Write-in', 0)
        if o != printed_writein:
            bad += 1
            print(f'MISMATCH {c.title_text} ({c.party}) write-ins: ours {o} '
                  f'vs printed {printed_writein}')
        # cross-panel per-precinct check
        panel_by_precinct = defaultdict(list)
        for p in c.panels:
            for precinct, t in p.row_totals.items():
                panel_by_precinct[precinct].append(t)
        diffs = {prec: ts for prec, ts in panel_by_precinct.items()
                 if len(set(ts)) > 1}
        if diffs:
            bad += 1
            print(f'CHECK {c.title_text} ({c.party}): inconsistent row totals '
                  f'across panels: {dict(list(diffs.items())[:5])}')
        sum_all = sum(ours.values())
        print(f'{c.title_text} ({c.party}) [{c.office}{"/" + c.district if c.district else ""}]: '
              f'{len(c.panels)} panel(s), {len(precincts)} precincts, countywide total '
              f'{sorted(totals) if totals else "?"}, ours(named+WI sum) {sum_all}')
    return bad


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_lycoming_primary_2022_results_parser.py '
              '<input_pdf> <output_csv>')
        sys.exit(1)
    results, contests, warnings = parse_pdf(sys.argv[1])
    if warnings:
        print(f'{len(warnings)} row(s) with per-panel candidates sum > Total Votes:')
        for w in warnings[:10]:
            print('  ' + w)
    bad = run_checks(contests, results)
    if bad:
        sys.exit(2)
    write_csv(results, sys.argv[2])


FIELDNAMES = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']


def write_csv(results, output_path):
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


if __name__ == '__main__':
    main()