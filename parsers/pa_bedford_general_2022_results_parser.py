#!/usr/bin/env python3
"""Bedford County, PA 2022 General Election precinct-level parser.

Source: "Bedford PA Official Results Precinct 11.8.2022.pdf"

Format: a crosstab "Results per Precinct" report (precincts as rows,
candidates as columns) where every cell's text is drawn unclipped -- full
candidate names and numbers overflow their column and overlap the next
column's text, so pdfplumber's extract_text/extract_tables interleave
columns and find no ruling-line tables.  The reliable signal is the
content-stream order: each cell's text is one Tj run, so grouping chars by
row (top) and segmenting runs in stream order (break on x-overlap or a
>8pt forward jump) recovers full precinct names, full candidate headers
("John Fetterman - DEM") and each numeric cell.  Numbers are right-aligned
per column, so each numeric run is assigned to a candidate column by its
right edge; header runs are assigned by their left edge (uniform ~51.6pt
column pitch), splitting header runs that silently span two columns at
char positions sitting exactly on a column edge.

The PDF contains only five contest tables (US Senate, Governor, U.S. House
13, State Senate 32, State House 78) followed by ~137 pages of per-
write-in-candidate detail tables (no precinct labels, no contest titles)
that are ignored.  The main tables' "Write-in" column plus any named
write-in candidate columns ("Donald Trump", "Beth Farnham", "Mickey
Mouse", ...) are aggregated into a single "Write Ins" row per precinct
(only when nonzero, matching the repo's 2022 convention); "Blank" columns
are contest blank/undervote columns and are skipped.  No Registered
Voters / Ballots Cast table and no vote-method breakdown exist in this
report, so the output uses the 7-column header.

Usage: python pa_bedford_general_2022_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys

import pdfplumber

COUNTY = 'Bedford'

# (office pattern, standard office name); district extracted separately
OFFICE_RULES = [
    (re.compile(r'^united states senator', re.I), 'U.S. Senate'),
    (re.compile(r'^governor', re.I), 'Governor'),
    (re.compile(r'^representative in congress', re.I), 'U.S. House'),
    (re.compile(r'^senator in the general assembly', re.I), 'State Senate'),
    (re.compile(r'^representative in the general assembly', re.I),
     'State House'),
]

VOTE_FOR_RE = re.compile(r'\(Vote for\s+(\d+)\)')
DISTRICT_RE = re.compile(r'(\d+)\s*(?:st|nd|rd|th)?\s+(?:Senatorial\s+)?District')
PARTY_RE = re.compile(r'^(.*?)\s*-\s*([A-Z/]{2,})$')
DATA_ROW_RE = re.compile(r'^\d{4}\b')


def normalize_office(title):
    """(office, district) from a raw contest title."""
    for pat, office in OFFICE_RULES:
        if pat.search(title):
            district = ''
            m = DISTRICT_RE.search(title)
            if m:
                district = m.group(1)
            return office, district
    return title.strip(), ''


def build_rows(page):
    """Group a page's chars into visual rows (clustered by top), preserving
    content-stream order within each row.  Returns [(top, [chars])]."""
    tops = []
    for c in page.chars:
        if all(abs(c['top'] - t) > 3 for t in tops):
            tops.append(c['top'])
    tops.sort()
    rows = {}
    for c in page.chars:
        best = min(tops, key=lambda t: abs(c['top'] - t))
        if abs(c['top'] - best) <= 3:
            rows.setdefault(best, []).append(c)
    return [(t, rows[t]) for t in sorted(rows)]


def segment_runs(chars):
    """Split a row's chars into text runs using content-stream order: a new
    run starts when a char overlaps the previous char's x-range (next
    column's text drawn over the previous run's clipped overflow) or jumps
    forward more than 8pt (adjacent cell, no overlap)."""
    runs = []
    cur, cur_chars, prev = '', [], None
    for c in chars:
        if prev is not None and (c['x0'] < prev['x1'] - 0.5
                                 or c['x0'] - prev['x1'] > 8):
            runs.append((cur, cur_chars))
            cur, cur_chars = '', []
        cur += c['text']
        cur_chars.append(c)
        prev = c
    if cur:
        runs.append((cur, cur_chars))
    return runs


def split_run_at(run_chars, edge, tol=0.7):
    """Split a header run at the char that starts exactly on a column left
    edge (a merged two-column header).  Returns (before, after) or None."""
    for i, c in enumerate(run_chars):
        if i > 0 and abs(c['x0'] - edge) <= tol:
            return run_chars[:i], run_chars[i:]
    return None


class ContestTable:
    """Buffers one contest's header and data rows; finalized on its Total row."""

    def __init__(self, office, district, vote_for):
        self.office = office
        self.district = district
        self.vote_for = vote_for
        self.header_runs = []      # [(text, start_x)]
        self.data_rows = []        # [(precinct, [(num_text, right_x)])]
        self.total_nums = []       # [(num_text, right_x)]

    def finalize(self):
        """Map header runs and data numbers to candidate columns.

        Returns (results rows, {precinct: write-in total},
                 {candidate: source Total row value})."""
        if not self.header_runs or not self.total_nums:
            raise ValueError(f'Incomplete table for {self.office}')

        rights = sorted(r for _, r in self.total_nums)
        gaps = [b - a for a, b in zip(rights, rights[1:])]
        pitch = sorted(gaps)[len(gaps) // 2]
        left_0 = self.header_runs[0][1][0]['x0']
        left_edges = [left_0 + pitch * i for i in range(len(self.total_nums))]
        ncols = len(self.total_nums)

        # ---- header runs -> columns by start edge; only split a run when
        # some column ended up with no header (a merged two-column header)
        cols = [None] * ncols   # per column: (text, run_chars)
        for text, run_chars in self.header_runs:
            start_x = run_chars[0]['x0']
            idx = min(range(ncols), key=lambda i: abs(start_x - left_edges[i]))
            if abs(start_x - left_edges[idx]) > 5:
                raise ValueError(
                    f'{self.office}: header run {text!r} not on a column edge')
            if cols[idx] is not None:
                raise ValueError(
                    f'{self.office}: two header runs map to column {idx}')
            cols[idx] = (text, run_chars)

        progress = True
        while progress:
            progress = False
            for m in range(ncols):
                if cols[m] is not None:
                    continue
                for ci in range(m):
                    if cols[ci] is None:
                        continue
                    _text, chars = cols[ci]
                    parts = split_run_at(chars, left_edges[m])
                    if parts:
                        cols[ci] = (''.join(c['text'] for c in parts[0]),
                                    parts[0])
                        cols[m] = (''.join(c['text'] for c in parts[1]),
                                   parts[1])
                        progress = True
                        break
                if cols[m] is not None:
                    break

        missing = [i for i, c in enumerate(cols) if c is None]
        if missing:
            raise ValueError(f'{self.office}: no header for columns {missing}')

        # ---- data numbers -> columns by right edge
        def assign_numbers(nums):
            assigned = {}
            for num, right_x in nums:
                idx = min(range(ncols), key=lambda i: abs(right_x - rights[i]))
                if abs(right_x - rights[idx]) > 4 or idx in assigned:
                    raise ValueError(
                        f'{self.office}: cannot place number {num!r} '
                        f'(right edge {right_x})')
                assigned[idx] = num
            if len(assigned) != ncols:
                raise ValueError(
                    f'{self.office}: row has {len(assigned)} of '
                    f'{ncols} expected numbers')
            return assigned

        total_assigned = assign_numbers(self.total_nums)
        results = []
        writeins = {}
        for precinct, nums in self.data_rows:
            assigned = assign_numbers(nums)
            wi_total = 0
            for i, header in enumerate(cols):
                votes = int(assigned[i])
                header = header[0]
                low = header.lower()
                if 'blank' in low:
                    continue  # contest blank/undervote column, not a candidate
                if 'write' in low or '-' not in header:
                    # generic 'Write-in' column or named write-in candidate
                    wi_total += votes
                    continue
                m = PARTY_RE.match(header)
                party = m.group(2) if m else ''
                name = m.group(1).strip() if m else header
                # governor/lt-governor pairs print as 'A / B'
                name = re.sub(r'\s+/\s+', '/', name)
                name = re.sub(r'\s+', ' ', name)
                results.append({
                    'county': COUNTY, 'precinct': precinct,
                    'office': self.office, 'district': self.district,
                    'party': party, 'candidate': name, 'votes': votes,
                })
            if wi_total:
                writeins[precinct] = wi_total

        totals = {}
        for i, header in enumerate(cols):
            header = header[0]
            low = header.lower()
            if 'blank' in low:
                continue
            if 'write' in low or '-' not in header:
                totals['Write Ins'] = totals.get('Write Ins', 0) + \
                    int(total_assigned[i])
                continue
            m = PARTY_RE.match(header)
            name = re.sub(r'\s+/\s+', '/', m.group(1).strip()) if m else header
            totals[name] = int(total_assigned[i])
        return results, writeins, totals


def parse(pdf_path):
    results = []
    contest_totals = {}   # (office, district) -> {candidate: source Total}
    table = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for _top, chars in build_rows(page):
                runs = segment_runs(chars)
                texts = [t for t, _ in runs]

                title = next((t for t in texts if VOTE_FOR_RE.search(t)), None)
                if title:
                    vote_for = VOTE_FOR_RE.search(title).group(1)
                    raw = VOTE_FOR_RE.sub('', title).strip()
                    office, district = normalize_office(raw)
                    table = ContestTable(office, district, vote_for)
                    continue

                if not table:
                    continue

                if texts[0].strip() == 'Precinct':
                    table.header_runs = [(t, cs) for t, cs in runs[1:]]
                elif texts[0].strip() == 'Total':
                    table.total_nums = [(t, cs[-1]['x1']) for t, cs in runs[1:]]
                    t_results, t_writeins, totals = table.finalize()
                    results.extend(t_results)
                    for precinct, wi in t_writeins.items():
                        results.append({
                            'county': COUNTY, 'precinct': precinct,
                            'office': table.office,
                            'district': table.district, 'party': '',
                            'candidate': 'Write Ins', 'votes': wi,
                        })
                    contest_totals[(table.office, table.district)] = totals
                    table = None
                elif DATA_ROW_RE.match(texts[0]):
                    nums = [(t, cs[-1]['x1']) for t, cs in runs[1:]]
                    table.data_rows.append((re.sub(r'\s+', ' ', texts[0]),
                                            nums))

    # internal check: per contest, precinct sums vs the source's Total row
    sums = {}
    for r in results:
        key = (r['office'], r['district'], r['candidate'])
        sums[key] = sums.get(key, 0) + r['votes']
    mismatches = []
    for key, totals in contest_totals.items():
        office, district = key
        for candidate, total in totals.items():
            got = sums.get((office, district, candidate), 0)
            if got != total:
                mismatches.append(
                    f'{office} {district} {candidate}: precincts {got} '
                    f'!= source Total {total}')
    return results, contest_totals, mismatches


FIELDNAMES = ['county', 'precinct', 'office', 'district', 'party',
              'candidate', 'votes']


def write_csv(results, output_path):
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(results)
    print(f'Wrote {len(results)} rows to {output_path}')


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_bedford_general_2022_results_parser.py '
              '<input_pdf> <output_csv>')
        sys.exit(1)
    results, contest_totals, mismatches = parse(sys.argv[1])
    write_csv(results, sys.argv[2])
    for key in sorted(contest_totals):
        print(key, contest_totals[key])
    if mismatches:
        print('TOTAL MISMATCHES:')
        for m in mismatches:
            print(' ', m)
    else:
        print('All precinct sums match source Total rows.')


if __name__ == '__main__':
    main()