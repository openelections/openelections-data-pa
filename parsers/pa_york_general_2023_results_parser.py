#!/usr/bin/env python3
"""
York County, PA 2023 General Election parser.

Source: "York County Official Results per Precinct Post Court Ruling Additions
2023 General.pdf" (43 pages; one table per contest: one row per precinct,
candidates as columns "<CANDIDATE> - <PARTY>", "Write-in", or "YES"/"NO" for
retention questions).

pdfplumber's extract_tables garbles the wide tables (many named write-in
columns with interleaved wrapped headers), so this parser works from word
positions (extract_words with x_tolerance=1.5):

- lines are clustered by word top (tolerance 1.5); words separated by a
  near-zero gap (invisible space characters in the letter-spaced sub-lines)
  are re-joined;
- contest segments start at title lines containing "(Vote for N)" and run to
  the next title;
- each contest ends with a "Total" row whose numeric words sit at the exact
  right edge of every value column; those x positions are the authoritative
  value columns (precinct-name digits never occur in a Total row);
- data rows are lines with >= 2 numeric words; numeric words are matched to
  value columns left-to-right by x1 (blank cells become 0); small digits left
  of the first value column are precinct suffixes and stay in the name;
- wrapped precinct names continue on lines just above ("pre") / below
  ("post") the value line, including across page breaks; gap lines before the
  first data row are headers;
- every header word is assigned to the value column whose span (from the
  previous column's right edge to this column's right edge) contains it --
  in the wide letter-spaced headers each name piece is right-aligned at its
  column edge; a "Write-in" word is instead mapped by nearest right edge;
  per column, words are joined in reading order;
- classification: contains 'write' -> aggregated 'Write-ins'; 'YES'/'NO' ->
  retention candidate; contains a party token ('DEM/REP' -> 'D/R') ->
  candidate with party; otherwise a named qualified write-in, aggregated
  into 'Write-ins'.

Both 2023 statewide retention questions were SUPERIOR Court retentions (Jack
Panella and Victor P. Stabile); 'Judge Retention(PLATTS)' is the local Court
of Common Pleas retention.

The source has no per-precinct Registered Voters / Ballots Cast figures, so
the output contains no metadata rows (same caveat as Lycoming), and no
election-day / mail / provisional breakdown (single vote count per column).

Usage: python pa_york_general_2023_results_parser.py <input_pdf> <output_csv>
"""

import csv
import re
import sys

import pdfplumber

COUNTY = 'York'

NUM_RE = re.compile(r'^[\d,]+$')
VOTE_FOR_RE = re.compile(r'^(.*?)\s*\(Vote for\s+(\d+)\)\s*$')
PARTIES = {'DEM', 'REP', 'LIB', 'GRN', 'IND', 'CON', 'LBR', 'WF', 'SSK',
           'FRE', 'UNI'}
COMBINED_PARTIES = {'DEM/REP': 'D/R', 'REP/DEM': 'D/R'}

RETENTIONS = {
    'PANELLA': 'Superior Court Retention - Jack Panella',
    'STABILE': 'Superior Court Retention - Victor P. Stabile',
    'PLATTS': 'Court of Common Pleas Retention - Platts',
}

# The source prints statewide candidates' surnames in caps; normalize the
# statewide candidates to the repo-standard spellings (as used by the other
# 2023 county files, e.g. Bucks).
STATEWIDE_NAME_MAP = {
    'Daniel McCAFFERY': 'Daniel McCaffery',
    'Carolyn CARLUCCIO': 'Carolyn Carluccio',
    'Jill BECK': 'Jill Beck',
    'Timika LANE': 'Timika Lane',
    'Maria BATTISTA': 'Maria Battista',
    'Harry F. SMAIL, Jr.': 'Harry F. Smail, Jr.',
    'Matt WOLF': 'Matt Wolf',
    'Megan MARTIN': 'Megan Martin',
}

FIELDNAMES = ['county', 'precinct', 'office', 'district', 'party',
              'candidate', 'votes', 'election_day', 'mail', 'provisional']


# ------------------------------------------------------------------ lines

class Line:
    __slots__ = ('page', 'top', 'words')

    def __init__(self, page, top, words):
        self.page = page
        self.top = top
        self.words = words

    def text(self):
        return ' '.join(w['text'] for w in self.words)

    def nums(self):
        return [w for w in self.words if NUM_RE.match(w['text'])]


def _merge_touching(words):
    """Join words split by zero-width space chars (letter-spaced sub-lines
    extract as one word per letter at gap 0); keep real spaces as joins."""
    out = []
    for w in words:
        if out and w['x0'] - out[-1]['x1'] <= 0.6:
            prev = out[-1]
            out[-1] = {'text': prev['text'] + w['text'],
                       'x0': prev['x0'], 'x1': w['x1'], 'top': prev['top']}
        else:
            out.append(dict(w))
    return out


def page_lines(page, page_idx):
    words = page.extract_words(x_tolerance=1.5)
    words.sort(key=lambda w: (w['top'], w['x0']))
    lines = []
    for w in words:
        if lines and abs(w['top'] - lines[-1][-1]['top']) <= 1.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [Line(page_idx, ln[0]['top'], _merge_touching(ln)) for ln in lines]


# ------------------------------------------------------------------ offices

def normalize_office(title):
    t = re.sub(r'\s+', ' ', title).strip()
    m = VOTE_FOR_RE.match(t)
    if not m:
        return t, ''
    t = m.group(1).strip()

    m = re.match(r'^Judge Retention\s*\((\w+)\)$', t)
    if m and m.group(1).upper() in RETENTIONS:
        return RETENTIONS[m.group(1).upper()], ''

    m = re.match(r'^Magisterial District Judge\s+([\d-]+)$', t)
    if m:
        return 'Magisterial District Judge', m.group(1)

    m = re.match(r'^Judge of the Court of Common Pleas\s*-\s*'
                 r'(\d+)(?:st|nd|rd|th)?\s*District$', t)
    if m:
        return 'Judge of the Court of Common Pleas', ''

    m = re.match(r'^School Director\s+-\s+(.+?)\s*(\(Unexpired\s+'
                 r'[A-Za-z]+ Year Term\))?$', t)
    if m:
        district = m.group(1).strip()
        term = (m.group(2) or '').strip()
        if term:
            term = term.replace('(Two Year Unexpired Term)',
                                'Unexpired 2 Year Term')
            term = term.replace('(Unexpired Two Year Term)',
                                'Unexpired 2 Year Term')
            term = term.replace('(Unexpired Four Year Term)',
                                'Unexpired 4 Year Term')
        office = 'School Director' + ((' ' + term) if term else '')
        return office, district

    # "<office> for <jurisdiction>"
    m = re.match(r'^(.+?)\s+for\s+(.+)$', t)
    if m:
        head = m.group(1).strip()
        juris = m.group(2).strip()
        head = re.sub(r'\(Two Year Unexpired Term\)', 'Unexpired 2 Year Term',
                      head)
        head = re.sub(r'\(Unexpired Two Year Term\)', 'Unexpired 2 Year Term',
                      head)
        head = re.sub(r'\(Unexpired Four Year Term\)', 'Unexpired 4 Year Term',
                      head)
        head = re.sub(r'Tax Collector (\d)yr', r'Tax Collector \1 Year Term',
                      head)
        head = re.sub(r'Auditor (\d)yr', r'Auditor \1 Year Term', head)
        head = re.sub(r'Borough Council (\d)yr',
                      r'Borough Council \1 Year Term', head)
        head = re.sub(r'\s+', ' ', head)
        return head, juris

    # "Hanover Borough Council 5 2yr"
    m = re.match(r'^(.+?)\s+Borough Council\s+(\d+)\s+(\d)yr$', t)
    if m:
        return (f'Borough Council {m.group(3)} Year Term',
                f'{m.group(1).strip()} Borough {m.group(2)}')

    # "<Muni> Commissioner"
    m = re.match(r'^(.+?)\s+Commissioner$', t)
    if m:
        return 'Township Commissioner', m.group(1).strip()

    # York City offices
    m = re.match(r'^(City Council|City Treasurer|City Controller)$', t)
    if m:
        return m.group(1), 'York City'

    return t, ''


def classify_header(text):
    """(kind, candidate, party) for a reconstructed column header.

    kind: 'candidate' | 'writeins' | 'yes' | 'no'
    """
    s = re.sub(r'\s+', ' ', text).strip()
    low = s.lower()
    if 'write' in low:
        return 'writeins', 'Write-ins', ''
    if low == 'yes':
        return 'yes', 'Yes', ''
    if low == 'no':
        return 'no', 'No', ''
    # the party token can sit anywhere in the fragment order (suffixes such
    # as 'III' may be reconstructed after it), so scan the tokens
    toks = [t for t in s.split() if t != '-']
    for i in range(len(toks) - 1, -1, -1):
        tok = toks[i].upper()
        if not PARTY_TOKEN_RE.match(tok):
            continue
        party = COMBINED_PARTIES.get(tok, tok)
        if party not in PARTIES and party != 'D/R':
            continue
        name = ' '.join(toks[:i] + toks[i + 1:]).strip()
        if name:
            return 'candidate', name, party
    # no recognizable party token: named qualified write-in
    return 'writeins', 'Write-ins', ''


# ------------------------------------------------------------------ parsing

def assign_gap_lines(anchor_seq, gap_seq):
    """Assign each gap (non-value) line to (anchor_idx, position) where
    position is 'pre', 'post' or 'header' (None anchor)."""
    out = {}
    n = len(anchor_seq)
    for ln, prev_idx in gap_seq:
        if prev_idx < 0:
            a0 = anchor_seq[0]
            if ln.page == a0.page and 0 <= a0.top - ln.top <= 6.5:
                out[id(ln)] = (0, 'pre')
            else:
                out[id(ln)] = (None, 'header')
            continue
        a_prev = anchor_seq[prev_idx]
        a_next = anchor_seq[prev_idx + 1] if prev_idx + 1 < n else None
        if a_next is None:
            out[id(ln)] = (prev_idx, 'post')
        elif ln.page == a_prev.page and ln.page == a_next.page:
            # same page: nearest anchor by top
            if (a_next.top - ln.top) < (ln.top - a_prev.top):
                out[id(ln)] = (prev_idx + 1, 'pre')
            else:
                out[id(ln)] = (prev_idx, 'post')
        elif ln.page > a_prev.page:
            # top of a new page: a wrapped name spilling across the page
            # break. Lines well above the next anchor finish the previous
            # row; lines just above it start its name.
            if a_next.page == ln.page and ln.top >= a_next.top - 6.0:
                out[id(ln)] = (prev_idx + 1, 'pre')
            else:
                out[id(ln)] = (prev_idx, 'post')
        else:
            # gap line on the previous row's page, next anchor on a later
            # page: page break right after it
            out[id(ln)] = (prev_idx, 'post')
    return out


def resolve_values(nums, cols, diagnostics, label):
    """Match a row's numeric words to Total-row columns left-to-right.

    Returns (values, name_digits): values maps col_index -> int; name_digits
    are small left-of-table numbers that belong to the precinct name.
    """
    values = {}
    name_digits = []
    ptr = 0
    for x1, text in sorted(nums):
        j = None
        for k in range(ptr, len(cols)):
            if abs(x1 - cols[k]) <= 2.5:
                j = k
                break
        if j is not None:
            for b in range(ptr, j):
                values[b] = 0
            values[j] = int(text.replace(',', ''))
            ptr = j + 1
            continue
        if x1 < cols[ptr] - 2.5:
            name_digits.append((x1, text))
            continue
        # blank cells before this value
        k = ptr
        while k < len(cols) and cols[k] < x1 - 2.5:
            k += 1
        if k < len(cols) and abs(x1 - cols[k]) <= 2.5:
            for b in range(ptr, k):
                values[b] = 0
            values[k] = int(text.replace(',', ''))
            ptr = k + 1
        else:
            diagnostics.append(f'{label}: unmatched numeric {text} @ {x1}')
    return values, name_digits


def _split_groups(words, max_gap=4.0):
    """Split a line's words into per-column groups at wide gaps."""
    groups = []
    cur = []
    prev = None
    for w in words:
        if cur and w['x0'] - prev['x1'] > max_gap:
            groups.append(cur)
            cur = []
        cur.append(w)
        prev = w
    if cur:
        groups.append(cur)
    return groups


PARTY_TOKEN_RE = re.compile(
    r'^(DEM/REP|REP/DEM|DEM|REP|LIB|GRN|IND|CON|LBR|WF|SSK|FRE|UNI)$')


def _span_left(cols, k):
    if k > 0:
        return cols[k - 1]
    if len(cols) > 1:
        return cols[0] - (cols[1] - cols[0]) / 2.0
    return cols[0] - 40.0


def _col_for_word(word, cols):
    """Column index for a non-'Write-in' header word.

    In the wide letter-spaced headers every name piece is right-aligned at
    (or contained in) its column's span, and the spans run from the previous
    column's right edge. Words sitting at a column's right edge (within the
    same 2.5pt tolerance as the value matching) go to that column first --
    this also catches floating-point overshoot and pieces a fraction of a
    point past their edge; span containment decides the rest; words outside
    the table (e.g. a trailing 'Ann' past the last edge) go to the nearest
    edge."""
    x1 = word['x1']
    best, best_d = None, None
    for k, cx in enumerate(cols):
        d = abs(x1 - cx)
        if best_d is None or d < best_d:
            best, best_d = k, d
    if best_d <= 2.5:
        return best
    if x1 <= _span_left(cols, 0):
        return 0
    if x1 > cols[-1]:
        return len(cols) - 1
    for k in range(len(cols)):
        if _span_left(cols, k) < x1 <= cols[k]:
            return k
    return best


def _col_for_write_word(word, cols):
    """'Write-in' tokens are right-aligned near their column's value edge."""
    best, best_d = 0, None
    for k, cx in enumerate(cols):
        d = abs(word['x1'] - cx)
        if best_d is None or d < best_d:
            best, best_d = k, d
    return best


def _build_col_headers(header_lines, cols, diagnostics, label):
    """Reconstruct each column's header text from the header lines.

    Every header word is assigned to a value column individually (spans run
    from the previous column's right edge) and the words of a column are
    joined in reading order (top, then x)."""
    frag_lists = [[] for _ in cols]
    for ln in header_lines:
        for w in ln.words:
            if w['text'].lower() == 'precinct':
                continue
            if 'write' in w['text'].lower():
                # 'Write-in' tokens are right-aligned near their column's
                # value edge, so map by nearest right edge
                k = _col_for_write_word(w, cols)
            else:
                k = _col_for_word(w, cols)
            frag_lists[k].append((ln.top, w['x0'], w['text']))

    col_defs = []
    seen_names = {}
    for j in range(len(cols)):
        frags = sorted(frag_lists[j])
        text = ' '.join(f[2] for f in frags)
        kind, cand, party = classify_header(text)
        if kind == 'candidate':
            key = (cand, party)
            if key in seen_names:
                diagnostics.append(
                    f'{label}: duplicate candidate {cand!r} cols '
                    f'{seen_names[key]} and {j} (raw {text!r})')
            else:
                seen_names[key] = j
        col_defs.append({'kind': kind, 'candidate': cand, 'party': party,
                         'raw': text})
    return col_defs


def parse_contest(title, lines, diagnostics):
    office, district = normalize_office(title)
    label = title[:55]

    # split anchors (>= 2 numeric words) from gap lines
    anchor_seq = []
    gap_seq = []
    for ln in lines:
        if len(ln.nums()) >= 2:
            anchor_seq.append(ln)
        else:
            gap_seq.append((ln, len(anchor_seq) - 1))

    if not anchor_seq:
        diagnostics.append(f'{label}: no data rows')
        return []

    assign = assign_gap_lines(anchor_seq, gap_seq)
    line_by_id = {id(l): l for l in lines}

    # build rows; collect header lines
    rows = []
    total_row = None
    header_lines = []
    for ln in lines:
        if len(ln.nums()) >= 2:
            name_words = [w for w in ln.words if not NUM_RE.match(w['text'])]
            row = {
                'name_parts': [],
                'anchor_name': ' '.join(w['text'] for w in name_words),
                'nums': [(w['x1'], w['text']) for w in ln.nums()],
                'is_total': (len(name_words) == 1 and
                             name_words[0]['text'].lower() == 'total'),
            }
            if row['is_total']:
                total_row = row
            rows.append(row)
        elif assign[id(ln)][0] is None:
            header_lines.append(ln)

    if total_row is None:
        diagnostics.append(f'{label}: no Total row')
        return []
    cols = sorted(x1 for x1, _ in total_row['nums'])

    # attach wrapped-name fragments
    for lid, (idx, pos) in assign.items():
        if idx is None:
            continue
        txt = ' '.join(w['text'] for w in line_by_id[lid].words)
        if not txt:
            continue
        if pos == 'pre':
            rows[idx]['name_parts'].insert(0, txt)
        else:
            rows[idx]['name_parts'].append(txt)

    col_defs = _build_col_headers(header_lines, cols, diagnostics, label)

    out_rows = []
    for r_idx, row in enumerate(rows):
        if row['is_total']:
            continue
        values, name_digits = resolve_values(row['nums'], cols, diagnostics,
                                             f'{label} row {r_idx}')
        digits = ' '.join(t for _, t in sorted(name_digits))
        name = re.sub(r'\s+', ' ', ' '.join(
            [p for p in row['name_parts'] if p] +
            [row['anchor_name'], digits])).strip()
        agg_wi = 0
        for j, cd in enumerate(col_defs):
            v = values.get(j, 0)
            if cd['kind'] == 'writeins':
                agg_wi += v
            else:
                out_rows.append({
                    'county': COUNTY, 'precinct': name, 'office': office,
                    'district': district, 'party': cd['party'],
                    'candidate': STATEWIDE_NAME_MAP.get(cd['candidate'],
                                                        cd['candidate']),
                    'votes': str(v),
                    'election_day': '', 'mail': '', 'provisional': '',
                })
        out_rows.append({
            'county': COUNTY, 'precinct': name, 'office': office,
            'district': district, 'party': '', 'candidate': 'Write-ins',
            'votes': str(agg_wi), 'election_day': '', 'mail': '',
            'provisional': '',
        })
    return out_rows


def parse(pdf_path):
    all_lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            all_lines.extend(page_lines(page, page_idx))

    # segment by contest title lines
    segments = []   # [title, [lines]]
    for ln in all_lines:
        if VOTE_FOR_RE.match(ln.text()):
            segments.append([ln.text(), []])
        elif segments:
            segments[-1][1].append(ln)
        # lines before the first title (page-1 banner) are dropped

    rows_out = []
    diagnostics = []
    for title, lines in segments:
        rows_out.extend(parse_contest(title, lines, diagnostics))
    for d in diagnostics:
        print('WARN:', d, file=sys.stderr)
    return rows_out


def main():
    if len(sys.argv) < 3:
        print('Usage: python pa_york_general_2023_results_parser.py '
              '<input_pdf> <output_csv>')
        sys.exit(1)
    rows = parse(sys.argv[1])
    with open(sys.argv[2], 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f'Wrote {len(rows)} rows to {sys.argv[2]}')


if __name__ == '__main__':
    main()