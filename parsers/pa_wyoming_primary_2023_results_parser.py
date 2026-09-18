#!/usr/bin/env python3
"""
Wyoming County, Pennsylvania — May 16, 2023 Municipal Primary per-precinct tally.

Source: "Wyoming County Precinct Tally 2023 Primary.pdf" (94 pages), which has a
native text layer (no OCR needed).  Layout:

  * Pages 1-37: contest-summary crosstabs.  One contest per block (some pages
    hold two blocks): vertical candidate-name headers, one column per
    candidate, one row per precinct, a totals row closing each block.
    Write-in candidate columns are printed in RED (non_stroking_color (1,0,0));
    ballot candidates in black.  "Blank" columns/rows are blank ballots /
    blank write-in entries and are folded into the Write-ins aggregate per
    the 2023 primary rules.
  * Pages 38-94: per-precinct tallies ("PRECINCT {party} {date}" header,
    OFFICE / VOTES rows) covering township/borough offices and school
    directors.  Red name rows are write-in candidates, black rows are ballot
    candidates.  An office line (or a following bare number) may carry the
    contest total.  Page 68 is an empty duplicate "NICHOLSON BOROUGH
    REPUBLICAN" header with no table (that precinct/party's data is on
    page 67); page 16 carries only the spilled grand total "128" of the DEM
    Sheriff block on page 15.

Usage:
    python parsers/pa_wyoming_primary_2023_results_parser.py <input> <output>

    <input> may be the source PDF (preferred — the red/black distinction that
    identifies write-in columns only exists there) or the pdftotext -layout
    extract.  The .txt path is DEGRADED: without the PDF's color information
    only rows/columns literally named "Blank"/"Scattered"/etc. can be
    recognised as write-ins; the parser prints a loud warning.  Use the PDF
    for the real data.

School-director and local township/borough offices are taken from the
per-precinct pages (pages 38-94, the source of truth for those contests);
the section-1 crosstabs for school directors are parsed only as a cross-check.
Judicial, county and Magisterial District Judge contests exist only in the
section-1 crosstabs and come from there.

Known source-data errors handled here (logged, not silently fixed):
  * Section-1 page 22 (Lackawanna Trail Region 1, 4-Year, DEM): the
    "Nicholson Towhship" row (Heather Clark 145 / Jaclyn Litwin 78 /
    Candace Haft 68) wrongly duplicates the Republican Nicholson Township
    numbers; page 24's REP Nicholson Township row is empty and its totals
    exclude the precinct.  The true values are on the per-precinct pages
    (DEM p69: Clark 51 / Litwin 38 / one unnamed write-in; REP p70:
    Clark 145 / Haft 78 / Litwin 68).  School-director values always come
    from the per-precinct pages, so this error never reaches the output.
  * "JACLYN LLITWIN" (per-precinct page 66) is Jaclyn Litwin.
  * "DANIEL NCCAFFERY" (crosstab header) is Daniel McCaffery.
"""

import csv
import re
import sys
from collections import Counter, defaultdict

COUNTY = "Wyoming"

# ----------------------------------------------------------------------------
# Normalisation tables
# ----------------------------------------------------------------------------

CANONICAL_PRECINCTS = [
    "Braintrim Township", "Clinton Township", "Eaton Township", "Exeter Township",
    "Factoryville Borough Ward 1", "Factoryville Borough Ward 2", "Falls Township",
    "Forkston Township", "Laceyville Borough", "Lemon Township",
    "Mehoopany Township", "Meshoppen Borough", "Meshoppen Township",
    "Monroe Township", "Nicholson Borough", "Nicholson Township",
    "North Branch Township", "Northmoreland Township", "Noxen Township",
    "Overfield Township", "Tunkhannock Borough Ward 1",
    "Tunkhannock Borough Ward 2", "Tunkhannock Borough Ward 3",
    "Tunkhannock Borough Ward 4", "Tunkhannock Township #1",
    "Tunkhannock Township #2", "Washington Township", "Windham Township",
]
PRECINCT_SET = set(CANONICAL_PRECINCTS)

PRECINCT_ALIASES = {n.lower(): n for n in CANONICAL_PRECINCTS}
PRECINCT_ALIASES.update({
    "nicholson towhship": "Nicholson Township",      # county-report typo
    "nicholson boro": "Nicholson Borough",
    "factoryville ward 1": "Factoryville Borough Ward 1",
    "factoryville ward 2": "Factoryville Borough Ward 2",
})
for _n in (1, 2, 3, 4):
    PRECINCT_ALIASES[f"tunkhannock boro ward {_n}"] = f"Tunkhannock Borough Ward {_n}"
    PRECINCT_ALIASES[f"tunkhannock township #{_n}"] = f"Tunkhannock Township #{_n}"

# candidate name (lower, collapsed whitespace) -> canonical spelling
CANONICAL_CANDIDATES = {
    "daniel nccaffery": "Daniel McCaffery",   # source typo
    "daniel mccaffery": "Daniel McCaffery",
    "debbie kunselman": "Debbie Kunselman",
    "pat dugan": "Pat Dugan",
    "timika lane": "Timika Lane",
    "jill beck": "Jill Beck",
    "matt wolf": "Matt Wolf",
    "bryan neft": "Bryan Neft",
    "maria battista": "Maria Battista",
    "harry f. smail jr.": "Harry F. Smail Jr.",
    "harry f smail jr": "Harry F. Smail Jr.",
    "megan martin": "Megan Martin",
    "carolyn carluccio": "Carolyn Carluccio",
    "bob roberts": "Bob Roberts",
    "rick wilbur": "Rick Wilbur",
    "tom henry": "Tom Henry",
    "ernie king": "Ernie King",
    "laura dickson": "Laura Dickson",
    "ashley darby": "Ashley Darby",
    "judy shupp": "Judy Shupp",
    "cindy zika adams": "Cindy Zika Adams",
    "dennis l. montross": "Dennis L. Montross",
    "david plummer": "David Plummer",
    "harold g. bender": "Harold G. Bender",
    "harold g bender": "Harold G. Bender",
    "harold bender": "Harold G. Bender",
    "jaclyn llitwin": "Jaclyn Litwin",        # per-precinct page 66 typo
    "jaclyn litwin": "Jaclyn Litwin",
    "heather clark": "Heather Clark",
    "candace haft": "Candace Haft",
    "lori a bennett": "Lori Bennett",
    "lori bennett": "Lori Bennett",
    "loriabennett": "Lori Bennett",
    "kari hilbert oshirak": "Kari Hilbert Oshirak",
    "kari hilbert-oshirak": "Kari Hilbert Oshirak",
    "josh prince": "Josh Prince",
    "joshprince": "Josh Prince",
    "patricia a. mccullough": "Patricia A. McCullough",
    "patriciaa. mccullough": "Patricia A. McCullough",   # crosstab band loses a space
    "sara saylor kashatus": "Sara Saylor Kashatus",
    "sarah saylor kashatus": "Sara Saylor Kashatus",     # REP page spelling
    "william prebola": "William Prebola",
    "john burke": "John Burke",
    "tiffani l warner": "Tiffani L Warner",
    "daryl travis knapp": "Daryl Travis Knapp",
    "mara a pagnotti-valenti": "Mara A Pagnotti-Valenti",
    "mara a. pagnotti-valenti": "Mara A Pagnotti-Valenti",
    "michael a kachmarsky": "Michael A Kachmarsky",
    "peter j butera": "Peter J Butera",
    "rebecca rutkoski": "Rebecca Rutkoski",
    "kirby kunkle": "Kirby Kunkle",
    "len pribula": "Len Pribula",
    "eric johnson": "Eric Johnson",
}

WRITEIN_NAME_RE = re.compile(
    r"^(write[- ]?ins?|scattered( write[- ]?ins?)?|scatter|blank|not assigned|"
    r"no candidate|void)$", re.I)

WATERMARK_RE = re.compile(
    r"(municipal|primary|election|democratic|republican|^may|"
    r"^16,?$|^2023$|county$)", re.I)

OFFICE_LINE_RE = re.compile(
    r"^(school\s*director|auditor|supervisor|constable|borough\s*council|"
    r"council(men|man| member)?|mayor|tax\s*collector)", re.I)

TITLE_KEYWORD_RE = re.compile(
    r"(court|commissioner|auditor|prothon|register|sheriff|magisterial|"
    r"school|region|vote for|years?|justice)", re.I)


def norm_name_key(name):
    return re.sub(r"[^a-z]", "", name.lower())


def normalize_precinct(raw):
    s = re.sub(r"\s+", " ", raw.strip())
    s = re.sub(r"\s+at\s+large$", "", s, flags=re.I)
    if s.lower() in PRECINCT_ALIASES:
        return PRECINCT_ALIASES[s.lower()]
    s = s.replace("TOWHSHIP", "TOWNSHIP")
    words = []
    for w in s.split():
        if w.isupper() and len(w) > 1:
            w = w.capitalize()
        words.append(w)
    s = " ".join(words)
    s = re.sub(r"\bBoro\b", "Borough", s)
    s = re.sub(r"\bTwp\b", "Township", s)
    if s in PRECINCT_SET:
        return s
    if s.lower() in PRECINCT_ALIASES:
        return PRECINCT_ALIASES[s.lower()]
    return None


def collapse_spaced(text):
    return re.sub(r"\s+", "", text)


def detect_party(text):
    t = collapse_spaced(text)
    if re.search(r"democratic", t, re.I):
        return "DEM"
    if re.search(r"republican", t, re.I):
        return "REP"
    return None


def map_school_director_text(low):
    """School-director header (lowercase) -> (office, district) or None."""
    term_m = re.search(r"(\d)\s*-?\s*years?\b", low)
    term = term_m.group(1) if term_m else "4"
    district = None
    if "wyoming area" in low:
        district = "Wyoming Area School District At Large"
    elif "elk lake" in low:
        district = "Elk Lake School District Region 4"
    else:
        bases = (
            ("lackawanna trail", "Lackawanna Trail School District"),
            ("tunkhannock area", "Tunkhannock Area School District"),
            ("wyalusing area", "Wyalusing Area School District"),
            ("lake lehman", "Lake Lehman School District"),
        )
        for key, base in bases:
            if key in low:
                reg = re.search(r"region\s*(\d)", low)
                district = base + (f" Region {reg.group(1)}" if reg else "")
                break
    if district is None:
        return None
    return (f"School Director ({term} Year)", district)


def map_section1_title(title, problems, where):
    low = " ".join(title.lower().split())
    checks = [
        (r"justice of the supreme", ("Justice of the Supreme Court", "")),
        (r"superior court", ("Judge of the Superior Court", "")),
        (r"commonwealth court", ("Judge of the Commonwealth Court", "")),
        (r"county commissioner", ("County Commissioner", "")),
        (r"county auditor", ("County Auditor", "")),
        (r"prothon", ("County Prothonotary & Clerk of Courts", "")),
        (r"register of wills", ("County Register of Wills & Recorder", "")),
        (r"sheriff", ("Sheriff", "")),
        (r"magisterial", ("Magisterial District Judge", "44-3-01")),
    ]
    for pat, office in checks:
        if re.search(pat, low):
            return office
    school = map_school_director_text(low)
    if school:
        return school
    problems.append(f"{where}: unrecognized contest title {title!r}")
    return None


def map_office_line(text, precinct, problems, where):
    tl = " ".join(text.lower().split())
    if re.search(r"school\s*director", tl):
        mapped = map_school_director_text(tl)
        if mapped:
            return mapped
        problems.append(f"{where}: unrecognized school director line {text!r}")
        return None
    term = None
    m = re.search(r"(\d)\s*yea", tl)
    if m:
        term = m.group(1)
    elif re.search(r"\dyer\b", tl):      # 'Constable -- 6 Yer' (p48 typo)
        term = "6"
    term_suf = f" ({term} Year)" if term else ""
    if tl.startswith("auditor"):
        return (f"Township Auditor{term_suf}", precinct)
    if tl.startswith("supervisor"):
        return (f"Township Supervisor{term_suf}", precinct)
    if tl.startswith("constable"):
        return (f"Constable{term_suf}", precinct)
    if "council" in tl:
        term = "2" if re.search(r"2\s*year", tl) else "4"
        return (f"Borough Council ({term} Year)", precinct)
    if tl.startswith("mayor"):
        return (f"Mayor{term_suf}", precinct)
    if "tax collector" in tl:
        return ("Tax Collector (2 Year)", precinct)
    problems.append(f"{where}: unrecognized office line {text!r}")
    return None


_COLLAPSED_CANDIDATES = {re.sub(r"[^a-z]", "", k): v
                         for k, v in CANONICAL_CANDIDATES.items()}


def fix_name(text):
    t = re.sub(r"\s+", " ", text).strip()
    if t.lower() in CANONICAL_CANDIDATES:
        return CANONICAL_CANDIDATES[t.lower()]
    # crosstab headers often lose their spaces ("ERNIEKING")
    collapsed = re.sub(r"[^a-z]", "", t.lower())
    if collapsed in _COLLAPSED_CANDIDATES:
        return _COLLAPSED_CANDIDATES[collapsed]
    return t


# ----------------------------------------------------------------------------
# Low-level line/word helpers
# ----------------------------------------------------------------------------

def char_is_red(c):
    col = c.get("non_stroking_color")
    if isinstance(col, (list, tuple)) and len(col) >= 1:
        if col[0] == 1.0 and (len(col) == 1 or (len(col) > 1 and col[1] == 0.0)):
            return True
    return False


def group_chars_into_lines(chars, top_gap=3.0):
    if not chars:
        return []
    ordered = sorted(chars, key=lambda c: (c["top"], c["x0"]))
    lines = []
    cur = [ordered[0]]
    cur_top = ordered[0]["top"]
    for c in ordered[1:]:
        if c["top"] - cur_top <= top_gap:
            cur.append(c)
            cur_top = max(cur_top, c["top"])
        else:
            lines.append(cur)
            cur = [c]
            cur_top = c["top"]
    lines.append(cur)
    out = []
    for ln in lines:
        ln.sort(key=lambda c: c["x0"])
        out.append((min(c["top"] for c in ln), ln))
    out.sort(key=lambda t: t[0])
    return out


def line_to_words(chars, x_gap=3.5):
    words = []
    cur = [chars[0]]
    for c in chars[1:]:
        if c["x0"] - cur[-1]["x1"] > x_gap:
            words.append(cur)
            cur = [c]
        else:
            cur.append(c)
    words.append(cur)
    out = []
    for w in words:
        text = "".join(c["text"] for c in w).strip()
        reds = sum(1 for c in w if char_is_red(c))
        out.append((text, w[0]["x0"], w[-1]["x1"], reds / len(w)))
    return out


def insert_spaces_by_gaps(run):
    gaps = []
    for a, b in zip(run, run[1:]):
        gaps.append(a["top"] - b["top"])
    real = [g for g in gaps if g > 0.2]
    if len(real) < 2:
        return "".join(ch["text"] for ch in run)
    med = sorted(real)[len(real) // 2]
    out = []
    for i, ch in enumerate(run):
        out.append(ch["text"])
        if i < len(gaps) and gaps[i] > med * 1.45:
            out.append(" ")
    return "".join(out)


def vertical_strings(vchars):
    """Group vertical chars into strings."""
    bands = defaultdict(list)
    for c in vchars:
        bands[round(c["x0"] / 4)].append(c)
    strings = []
    for _, cs in bands.items():
        cs.sort(key=lambda c: -c["top"])          # first letter = largest top
        run = [cs[0]]
        runs = [run]
        for prev, c in zip(cs, cs[1:]):
            if prev["top"] - c["top"] > 30:
                run = [c]
                runs.append(run)
            else:
                run.append(c)
        for run in runs:
            text = "".join(ch["text"] for ch in run)
            if not text.strip():
                continue
            if " " not in text:
                text = insert_spaces_by_gaps(run)
            reds = sum(1 for ch in run if char_is_red(ch))
            strings.append({
                "text": text.strip(),
                "x_center": sum((ch["x0"] + ch["x1"]) / 2 for ch in run) / len(run),
                "red_frac": reds / len(run),
                "top": max(ch["top"] for ch in run),
                "bottom": min(ch["top"] for ch in run),
            })
    return strings


_PA_COUNTY_WATERMARKS = (
    "ADAMS", "ALLEGHENY", "ARMSTRONG", "BEAVER", "BEDFORD", "BERKS", "BLAIR",
    "BRADFORD", "BUCKS", "BUTLER", "CAMBRIA", "CAMERON", "CARBON", "CENTRE",
    "CHESTER", "CLARION", "CLEARFIELD", "CLINTON", "COLUMBIA", "CRAWFORD",
    "CUMBERLAND", "DAUPHIN", "DELAWARE", "ELK", "ERIE", "FAYETTE", "FOREST",
    "FRANKLIN", "FULTON", "GREENE", "HUNTINGDON", "INDIANA", "JEFFERSON",
    "JUNIATA", "LACKAWANNA", "LANCASTER", "LAWRENCE", "LEBANON", "LEHIGH",
    "LUZERNE", "LYCOMING", "MCKEAN", "MERCER", "MIFFLIN", "MONROE",
    "MONTGOMERY", "MONTOUR", "NORTHAMPTON", "NORTHUMBERLAND", "PERRY",
    "PHILADELPHIA", "PIKE", "POTTER", "SCHUYLKILL", "SNYDER", "SOMERSET",
    "SULLIVAN", "SUSQUEHANNA", "TIOGA", "UNION", "VENANGO", "WARREN",
    "WASHINGTON", "WAYNE", "WESTMORELAND", "WYOMING", "YORK",
)

_PRECINCT_WATERMARKS = {re.sub(r"[^A-Za-z]", "", p).upper()
                        for p in CANONICAL_PRECINCTS}


# the MDJ crosstabs carry the court seat ("Factoryville Borough") as a page
# watermark, sometimes split across two bands
_EXTRA_WATERMARK_TOKENS = {"FACTORYVILLEBOROUGH", "FACTORYVILLE", "BOROUGH"}


def is_watermark(text):
    t = re.sub(r"[^A-Za-z]", "", text).upper()
    if not t:
        return True
    if t in _EXTRA_WATERMARK_TOKENS:
        return True
    if WATERMARK_RE.search(text.strip()):
        return True
    for c in _PA_COUNTY_WATERMARKS:
        if c in t:
            return True
    for p in _PRECINCT_WATERMARKS:
        if len(p) > 6 and p in t:
            return True
    return False


# ----------------------------------------------------------------------------
# Section 1: contest-summary crosstab pages (1-37)
# ----------------------------------------------------------------------------

class Block:
    def __init__(self, page_no):
        self.page_no = page_no
        self.title_words = []          # [(top, text)]
        self.party = None
        self.precinct_rows = []        # (raw, norm, {x_center: (val, red)}, top)
        self.totals_cells = {}         # x_center -> value
        self.columns = []
        self.first_row_top = None
        self.name_fragments = []       # degraded .txt mode: header word fragments

    @property
    def title(self):
        return " ".join(t for _, t in sorted(self.title_words))


def build_blocks_from_lines(parsed_lines, page_no, vchars, problems, notes):
    """parsed_lines: [(top, [(text, x0, x1, red_frac), ...])]; vchars: vertical
    chars (PDF) or [] (txt mode).  Returns a list of finished Blocks."""
    blocks = []
    cur = None

    def close_block():
        nonlocal cur
        if cur is not None:
            if cur.precinct_rows:
                blocks.append(cur)
            else:
                notes.append(f"p{page_no}: header {cur.title!r} has no precinct "
                             f"rows; dropped")
        cur = None

    for top, words in parsed_lines:
        words = [w for w in words if w[0].strip()]
        if not words:
            continue
        alpha_words = [w for w in words if not re.fullmatch(r"\d{1,4}", w[0])]
        numeric_words = [w for w in words if re.fullmatch(r"\d{1,4}", w[0])]
        first_alpha = alpha_words[0][0] if alpha_words else ""
        label_key = re.sub(r"\s+", " ", first_alpha).strip().lower()

        if label_key in ("office", "votes", "candidates name", "candidates",
                         "candidate name") and not numeric_words:
            continue

        if cur is not None and cur.precinct_rows:
            numeric_only = (not alpha_words) and bool(numeric_words)
            is_totals_label = label_key.rstrip(":") == "totals"
            if numeric_only or (is_totals_label and numeric_words):
                for text, x0, x1, redf in numeric_words:
                    cur.totals_cells[round((x0 + x1) / 2)] = int(text)
                close_block()
                continue
            if is_totals_label:
                continue    # bare totals label; numbers on another line

        norm = normalize_precinct(first_alpha) if first_alpha else None
        if norm is not None:
            if cur is None:
                cur = Block(page_no)
            if cur.first_row_top is None:
                cur.first_row_top = top
            cells = {}
            for text, x0, x1, redf in numeric_words:
                cells[round((x0 + x1) / 2)] = (int(text), redf)
            cur.precinct_rows.append((first_alpha, norm, cells, top))
            continue

        # header / title line
        collapsed = collapse_spaced(" ".join(w[0] for w in words))
        if re.search(r"municipal|election|^may|16,?2023", collapsed, re.I):
            continue
        party = detect_party(collapsed)
        if cur is None:
            cur = Block(page_no)
        if party and cur.party is None:
            cur.party = party
        # long space runs inside a merged word separate label fields
        # (e.g. "DEMOCRATIC        MAGISTERIAL DISTRICT JUSTICE")
        parts = []
        for w in words:
            for part in re.split(r"\s{2,}", w[0].strip()):
                if part and not detect_party(part):
                    parts.append(part)
        line_text = " ".join(parts)
        if line_text and TITLE_KEYWORD_RE.search(line_text) \
                and not re.fullmatch(r"[\d,\-\s]+", line_text):
            cur.title_words.append((top, line_text))
        elif line_text:
            for w in words:
                if detect_party(w[0]):
                    continue
                cur.name_fragments.append((top, w[0], (w[1] + w[2]) / 2))
    close_block()
    # finish blocks, giving each block only the vertical strings from its own
    # header zone (multi-block pages share the same x bands); a block's
    # headers sit above its first precinct row and below the previous
    # block's first row
    for i, b in enumerate(blocks):
        upper = b.first_row_top
        lower = blocks[i - 1].first_row_top if i else 0.0
        if vchars:
            zone = [c for c in vchars if lower < c["top"] <= upper + 3]
        else:
            zone = []
        finish_block(b, zone, problems, notes)
    return blocks


def finish_block(block, vchars, problems, notes):
    where = f"p{block.page_no}"
    title = block.title
    if not block.party:
        problems.append(f"{where}: no party found for title {title!r}")
    mapped = map_section1_title(title, problems, where) if title else None
    block.office = mapped[0] if mapped else None
    block.district = mapped[1] if mapped else ""

    # cluster numeric column x positions from totals row and precinct rows
    # (totals cover columns that are zero in every precinct)
    xs = sorted({x for _, _, cells, _ in block.precinct_rows for x in cells}
                | set(block.totals_cells))
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= 8:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    col_centers = [sum(c) / len(c) for c in clusters]
    if col_centers and block.totals_cells and \
            len(col_centers) != len(block.totals_cells):
        notes.append(f"{where} {title!r}: {len(col_centers)} data columns vs "
                     f"{len(block.totals_cells)} totals cells")

    block.columns = [{"x": cx, "name": None, "name_red": None,
                      "writein": None, "cells": defaultdict(int),
                      "cell_reds": []} for cx in col_centers]

    def nearest_col(x):
        if not col_centers:
            return None
        cx = min(col_centers, key=lambda c: abs(c - x))
        return cx if abs(cx - x) <= 6 else None

    # candidate names: vertical strings (PDF) or header fragments (txt)
    if vchars:
        name_sources = []
        for s in vertical_strings(vchars):
            name_sources.append((s["bottom"], s["x_center"], [s["text"]],
                                 s["red_frac"]))
        for _, xc, parts, redf in name_sources:
            if not col_centers:
                break
            if len(parts[0].strip()) < 2 or is_watermark(parts[0]):
                continue
            cx = min(col_centers, key=lambda c: abs(c - xc))
            if abs(cx - xc) > 14:
                notes.append(f"{where}: header text {parts[0]!r} matches no "
                             f"column")
                continue
            col = next(c for c in block.columns if c["x"] == cx)
            fname = fix_name(parts[0])
            if col["name"] is None:
                col["name"] = fname
                col["name_red"] = redf >= 0.6
            elif fname != col["name"]:
                # wrapped vertical names print as two adjacent strings
                col["name"] = fix_name(col["name"] + " " + fname)
                notes.append(f"{where}: merged header text on column "
                             f"{col['name']!r}")
    else:
        frag = defaultdict(list)     # col x -> [(top, xmid, word)]
        for top, word, xmid in block.name_fragments:
            if len(word) < 2 or is_watermark(word):
                continue
            cx = nearest_col(xmid)
            if cx is None:
                notes.append(f"{where}: header word {word!r} matches no column")
                continue
            frag[cx].append((top, xmid, word))
        for col in block.columns:
            words = frag.get(col["x"], [])
            if not words:
                continue
            # group words of the same visual line, join in x order
            lines_out = []
            for top in sorted({w[0] for w in words}):
                group = sorted([w for w in words if abs(w[0] - top) <= 2],
                               key=lambda t: t[1])
                lines_out.append((len(group), " ".join(g[2] for g in group)))
            lines_out.sort(key=lambda t: -t[0])
            col["name"] = fix_name(lines_out[0][1])
            col["name_red"] = False
            if len(lines_out) > 1:
                notes.append(f"{where}: column {col['name']!r} also matched "
                             f"{lines_out[1][1]!r}")
    # assign cells
    for raw, norm, cells, _ in block.precinct_rows:
        for x, (val, redf) in cells.items():
            cx = nearest_col(x)
            if cx is None:
                notes.append(f"{where}: value {val} at x={x} matches no column")
                continue
            col = next(c for c in block.columns if c["x"] == cx)
            col["cells"][norm] = val
            col["cell_reds"].append(redf >= 0.6)
    # write-in classification
    for col in block.columns:
        name = col["name"] or ""
        if col["name"] is None:
            # unnamed numeric columns in the crosstabs are write-in columns
            # whose header was not printed/read
            col["writein"] = True
            problems.append(f"{where} {block.office} ({block.party}): unnamed "
                            f"data column at x={col['x']:.0f} treated as "
                            f"write-in")
        elif (col["name_red"] or WRITEIN_NAME_RE.match(name)) and vchars:
            col["writein"] = True
        elif re.sub(r"[^A-Za-z]", "", name).upper() in \
                {"CANDIDATE", "NOCANDIDATE"}:
            # the crosstabs label the ballot's write-in slot (or an empty
            # race's single line) with the generic word "CANDIDATE"
            col["writein"] = True
            notes.append(f"{where}: column labelled {name!r} treated as the "
                         f"write-in slot")
        elif WRITEIN_NAME_RE.match(name):
            col["writein"] = True
        elif col["cell_reds"] and \
                sum(col["cell_reds"]) / len(col["cell_reds"]) >= 0.6:
            col["writein"] = True
            notes.append(f"{where}: column {name!r} classified write-in by "
                         f"red cells")
        else:
            col["writein"] = False
    # totals validation
    for col in block.columns:
        cell_sum = sum(col["cells"].values())
        tot = None
        for tx, tv in block.totals_cells.items():
            if abs(tx - col["x"]) <= 8:
                tot = tv
                break
        if tot is not None and tot != cell_sum:
            problems.append(
                f"{where} {block.office} ({block.party}): column "
                f"{col['name']!r} precinct sum {cell_sum} != totals row {tot}")


def emit_section1_block(b, rows_out, problems, notes, force_zero_writeins=False):
    """Emit rows for a section-1 crosstab block (authoritative contests)."""
    if b.party is None:
        problems.append(f"p{b.page_no} {b.office}: no party; block skipped")
        return
    if b.office is None:
        return          # unrecognized title already logged by map_section1_title
    writeins = defaultdict(int)
    for col in b.columns:
        if col["writein"]:
            for p, v in col["cells"].items():
                writeins[p] += v
    precincts = [pnorm for _, pnorm, _, _ in b.precinct_rows]
    for col in b.columns:
        if col["writein"] or col["name"] is None:
            continue
        for p in precincts:
            val = col["cells"].get(p, 0)
            rows_out.append([COUNTY, p, b.office, b.district, b.party,
                             col["name"], val, "", "", ""])
    for p in precincts:
        if writeins.get(p, 0) > 0 or (force_zero_writeins and p not in writeins):
            rows_out.append([COUNTY, p, b.office, b.district, b.party,
                             "Write-ins", writeins.get(p, 0), "", "", ""])
    if not precincts:
        rows_out.append([COUNTY, "", b.office, b.district, b.party,
                         "Write-ins", 0, "", "", ""])
        problems.append(f"p{b.page_no} {b.office}: crosstab has no precinct "
                        f"rows; emitted countywide Write-ins 0")


# ----------------------------------------------------------------------------
# Section 2: per-precinct tally pages (38-94)
# ----------------------------------------------------------------------------

def parse_section2_page(page_no, parsed_lines, problems, notes, txt=False):
    precinct = party = None
    start_idx = 0
    found_office = False
    for i, (top, words) in enumerate(parsed_lines):
        text = " ".join(w[0] for w in words).strip()
        if re.match(r"^office\b", text, re.I):
            start_idx = i + 1
            found_office = True
            break
        if re.match(r"^votes\b", text, re.I) and len(words) <= 2:
            continue
        p = detect_party(text)
        if p and party is None:
            party = p
        cand_text = " ".join(w[0] for w in words if not detect_party(w[0]))
        cand_text = re.sub(r"\b0?5/\d{2}/\d{4}\b", "", cand_text).strip()
        if cand_text and precinct is None:
            norm = normalize_precinct(cand_text)
            if norm:
                precinct = norm
    if precinct is None or party is None:
        problems.append(f"p{page_no}: missing header (precinct={precinct!r}, "
                        f"party={party!r})")
        return []
    if not found_office:
        # header-only page with no table (e.g. p68, a duplicate header)
        notes.append(f"p{page_no} {precinct} {party}: header with no office "
                     f"table; page skipped")
        return []

    where = f"p{page_no} {precinct} {party}"
    contests = []
    cur = None

    for top, words in parsed_lines[start_idx:]:
        words = [w for w in words if w[0].strip()]
        if not words:
            continue
        alpha_words = [w for w in words if not re.fullmatch(r"\d{1,4}", w[0])]
        numeric_words = [w for w in words if re.fullmatch(r"\d{1,4}", w[0])]
        name_text = " ".join(w[0] for w in alpha_words).strip()
        if txt:
            # character-offset coordinates: the votes count is the last
            # numeric token on the line
            votes_cells = numeric_words[-1:]
            other_nums = []
        else:
            votes_cells = [w for w in numeric_words if w[1] > 430]
            other_nums = [w for w in numeric_words if w not in votes_cells]

        if alpha_words and OFFICE_LINE_RE.match(alpha_words[0][0]):
            total = int(votes_cells[-1][0]) if votes_cells else None
            od = map_office_line(name_text, precinct, problems, where)
            if od is None:
                cur = None
                continue
            cur = {"office": od[0], "district": od[1], "party": party,
                   "precinct": precinct, "rows": [], "total": total,
                   "where": where}
            contests.append(cur)
            continue
        if not alpha_words and votes_cells and cur is not None:
            val = int(votes_cells[-1][0])
            rows_sum = sum(r["votes"] for r in cur["rows"])
            if cur["rows"] and val == rows_sum:
                if cur["total"] is not None and cur["total"] != val:
                    notes.append(f"{where}: duplicate total for "
                                 f"{cur['office']}")
                cur["total"] = val
            else:
                # a write-in row whose name text did not survive extraction
                cur["rows"].append({"name": "", "votes": val,
                                    "writein": True})
                notes.append(f"{where} {cur['office']}: write-in row with no "
                             f"name text, {val} vote(s)")
            continue
        if alpha_words and cur is not None:
            if not name_text.strip():
                continue
            red_name = any(w[3] >= 0.5 for w in alpha_words)
            votes = None
            if votes_cells:
                votes = int(votes_cells[-1][0])
            elif other_nums:
                votes = int(other_nums[-1][0])
                notes.append(f"{where}: votes for {name_text!r} not in votes "
                             f"column")
            if votes is None:
                # the source omits the vote figure for this row entirely
                problems.append(f"{where} {cur['office']}: no vote figure "
                                f"printed for {name_text!r}; row dropped")
                continue
            cur["rows"].append({"name": name_text, "votes": votes,
                                "writein": red_name})
            continue
        if alpha_words and cur is None:
            problems.append(f"{where}: name row before any office line: "
                            f"{name_text!r}")
            continue
        notes.append(f"{where}: skipped line {name_text!r} "
                     f"nums={numeric_words}")
    return contests


def txt_mode_votes(x):
    return x


# ----------------------------------------------------------------------------
# Emission
# ----------------------------------------------------------------------------

def emit_contest(c, rows_out, problems, notes):
    cand_rows = [r for r in c["rows"]
                 if not r["writein"] and not WRITEIN_NAME_RE.match(r["name"])]
    win_sum = sum(r["votes"] for r in c["rows"]
                  if r["writein"] or WRITEIN_NAME_RE.match(r["name"]))
    for r in cand_rows:
        rows_out.append([COUNTY, c["precinct"], c["office"], c["district"],
                         c["party"], fix_name(r["name"]), r["votes"],
                         "", "", ""])
    if not c["rows"]:
        total = c["total"] or 0
        if not total:
            # no candidate figures and no total printed anywhere: nothing to
            # record without fabricating a zero
            problems.append(f"{c['where']} {c['office']}: contest has no vote "
                            f"figures printed; contest omitted")
            return
        notes.append(f"{c['where']} {c['office']}: contest total {total} "
                     f"with no candidate rows; emitted as Write-ins")
        rows_out.append([COUNTY, c["precinct"], c["office"], c["district"],
                         c["party"], "Write-ins", total, "", "", ""])
    elif win_sum > 0:
        rows_out.append([COUNTY, c["precinct"], c["office"], c["district"],
                         c["party"], "Write-ins", win_sum, "", "", ""])
    if c["total"] is not None and c["rows"]:
        s = sum(r["votes"] for r in cand_rows) + win_sum
        if s != c["total"]:
            problems.append(f"{c['where']} {c['office']}: rows sum {s} != "
                            f"office total {c['total']}")


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def unify_candidate_names(rows, problems):
    """Collapse spelling variants of the same candidate within each contest."""
    groups = defaultdict(list)
    for r in rows:
        groups[(r[2], r[3], r[4])].append(r)
    for (office, district, party), rs in groups.items():
        variants = defaultdict(list)
        for r in rs:
            if r[5] != "Write-ins":
                variants[norm_name_key(r[5])].append(r)
        for key, lst in variants.items():
            if not key:
                problems.append(f"{office} {district} {party}: blank candidate "
                                f"name in {len(lst)} rows")
                continue
            spellings = Counter(r[5] for r in lst)
            if len(spellings) == 1:
                continue
            winner, _ = spellings.most_common(1)[0]
            for r in lst:
                r[5] = winner
            problems.append(f"{office} {district} {party}: unified candidate "
                            f"spellings {dict(spellings)} -> {winner!r}")


def dedup_and_sort(rows_out, problems):
    precinct_order = {p: i for i, p in enumerate(CANONICAL_PRECINCTS)}
    seen = set()
    deduped = []
    for r in rows_out:
        key = tuple(r[:7])
        if key in seen:
            problems.append(f"dropped duplicate row {key}")
            continue
        seen.add(key)
        deduped.append(r)
    contest_order = {}

    def sort_key(r):
        ck = (r[2], r[3], r[4])
        if ck not in contest_order:
            contest_order[ck] = len(contest_order)
        return (precinct_order.get(r[1], 999), contest_order[ck],
                1 if r[5] == "Write-ins" else 0, r[5])
    deduped.sort(key=sort_key)
    rows_out[:] = deduped


def crosscheck_school_directors(blocks, rows_out, problems, notes):
    emitted = defaultdict(lambda: defaultdict(dict))
    for r in rows_out:
        if r[2].startswith("School Director"):
            emitted[(r[2], r[3], r[4])][r[1]][r[5]] = r[6]
    for b in blocks:
        key = (b.office, b.district, b.party)
        if key not in emitted:
            # per-precinct pages carry the contest header but no vote
            # figures; the crosstab prints the (zero) totals, so use it
            notes.append(f"p{b.page_no} {b.office} ({b.party}): contest "
                         f"missing from per-precinct pages; emitted from "
                         f"crosstab")
            emit_section1_block(b, rows_out, problems, notes,
                                force_zero_writeins=True)
            continue
        by_precinct = emitted[key]
        precincts = [pnorm for _, pnorm, _, _ in b.precinct_rows]
        win = defaultdict(int)
        for col in b.columns:
            if col["writein"]:
                for p, v in col["cells"].items():
                    win[p] += v
        for col in b.columns:
            if col["writein"] or col["name"] is None:
                continue
            key = norm_name_key(col["name"])
            for p in sorted(set(precincts)):
                per_p = by_precinct.get(p, {})
                match = next((v for k, v in per_p.items()
                              if norm_name_key(k) == key), 0)
                v1 = col["cells"].get(p, 0)
                if v1 != match:
                    problems.append(
                        f"CROSSCHECK MISMATCH {b.office} {b.district} "
                        f"({b.party}) {p} {col['name']!r}: crosstab {v1} vs "
                        f"per-precinct {match}")
        for p in sorted(set(precincts)):
            w1 = win.get(p, 0)
            w2 = by_precinct.get(p, {}).get("Write-ins", 0)
            if w1 != w2:
                problems.append(
                    f"CROSSCHECK MISMATCH {b.office} {b.district} ({b.party}) "
                    f"{p} Write-ins: crosstab {w1} vs per-precinct {w2}")


def run_pdf(input_path, rows_out, problems, notes):
    import pdfplumber
    sec1_blocks = []
    sec2_contests = []
    with pdfplumber.open(input_path) as pdf:
        for page in pdf.pages:
            n = page.page_number
            hchars = [c for c in page.chars if abs(c["matrix"][0]) > 0.5]
            parsed_lines = [(top, line_to_words(chars))
                            for top, chars in group_chars_into_lines(hchars)]
            if n <= 37:
                vchars = [c for c in page.chars
                          if abs(c["matrix"][0]) <= 0.5 and c["text"].strip()]
                sec1_blocks.extend(
                    build_blocks_from_lines(parsed_lines, n, vchars,
                                            problems, notes))
            else:
                sec2_contests.extend(
                    parse_section2_page(n, parsed_lines, problems, notes))
    authoritative, crosscheck = [], []
    for b in sec1_blocks:
        if b.office and b.office.startswith("School Director"):
            crosscheck.append(b)
        else:
            authoritative.append(b)
    for c in sec2_contests:
        emit_contest(c, rows_out, problems, notes)
    for b in authoritative:
        emit_section1_block(b, rows_out, problems, notes)
    crosscheck_school_directors(crosscheck, rows_out, problems, notes)
    return len(sec1_blocks), len(sec2_contests)


def run_txt(input_path, rows_out, problems, notes):
    """Degraded parse of the pdftotext -layout extract (no color info)."""
    with open(input_path) as fh:
        text = fh.read()
    sec1_blocks = []
    sec2_contests = []
    for page_no, page_text in enumerate(text.split("\f"), start=1):
        parsed_lines = []
        for raw in page_text.splitlines():
            if not raw.strip():
                continue
            words = [(m.group(0), float(m.start()), float(m.end()), 0.0)
                     for m in re.finditer(r"\S+", raw)]
            parsed_lines.append((0.0, words))
        if page_no <= 37:
            sec1_blocks.extend(
                build_blocks_from_lines(parsed_lines, page_no, [],
                                        problems, notes))
        else:
            sec2_contests.extend(
                parse_section2_page(page_no, parsed_lines, problems, notes,
                                    txt=True))
    authoritative, crosscheck = [], []
    for b in sec1_blocks:
        if b.office and b.office.startswith("School Director"):
            crosscheck.append(b)
        else:
            authoritative.append(b)
    for c in sec2_contests:
        emit_contest(c, rows_out, problems, notes)
    for b in authoritative:
        emit_section1_block(b, rows_out, problems, notes)
    crosscheck_school_directors(crosscheck, rows_out, problems, notes)
    return len(sec1_blocks), len(sec2_contests)


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "mail", "provisional"])
        w.writerows(rows)


def report(problems, notes, rows, output_path, n_blocks, n_pages2):
    print(f"wrote {len(rows)} rows to {output_path}")
    contests = sorted({(r[2], r[3], r[4]) for r in rows})
    print(f"contests: {len(contests)}  (section-1 blocks: {n_blocks}, "
          f"section-2 pages: {n_pages2})")
    for o, d, p in contests:
        print(f"  {o} | {d} | {p}")
    print(f"\nPROBLEMS ({len(problems)}):")
    for p in problems:
        print(f"  ! {p}")
    print(f"\nNOTES ({len(notes)}):")
    for n in notes:
        print(f"  - {n}")


def run(input_path, output_path):
    problems = []
    notes = []
    rows_out = []
    if input_path.lower().endswith(".pdf"):
        n_blocks, n_pages2 = run_pdf(input_path, rows_out, problems, notes)
    else:
        problems.append(
            "DEGRADED MODE: text input has no color information; write-in "
            "rows/columns other than literal 'Blank'/'Scattered' names cannot "
            "be identified. Use the PDF for authoritative results.")
        n_blocks, n_pages2 = run_txt(input_path, rows_out, problems, notes)
    unify_candidate_names(rows_out, problems)
    dedup_and_sort(rows_out, problems)
    write_csv(output_path, rows_out)
    report(problems, notes, rows_out, output_path, n_blocks, n_pages2)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    run(argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))