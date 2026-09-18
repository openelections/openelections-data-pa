#!/usr/bin/env python3
"""Parser for Crawford County 2023 Municipal Primary (May 16, 2023).

Two source families, auto-detected by file name:

1. "County Results" computing papers (scanned; PaddleOCR-VL text
   ``Crawford__{Dem,Rep}_County_Results_2023_Primary__ocr.txt``).  One
   two-page spread per contest ("OFFICIAL RETURNS ... Held in the several
   Election Districts"): rows are election districts, each candidate a
   Machine/Hand/Total column group, plus a SCATTERED column and printed
   Page Totals / Totals from Page 1 / Grand Total rows.  Contests cover
   the judicial + county offices.  Output is the 9-column county schema;
   votes = the printed Total column.  Machine/Hand are a ballot-medium
   split, not an ED/mail/provisional split, so the breakdown columns are
   left empty (matching the 2023 general practice for this county).

   Several contest spreads OCR'd badly: DEM Coroner p2, DEM Register &
   Recorder p2, REP Supreme Court p2, REP Coroner (both pages) and REP
   Auditor p2 are header-only stubs, and the hosted-OCR page for REP
   Prothonotary p2 garbled its totals-row labels ("Trom Twp 10/11/12").
   For those contests the Grand Total rows were transcribed by eye from
   200-dpi renders of the source PDF pages and live in VISION_TOTALS.
   Every vision contest whose data pages did OCR is cross-checked at
   runtime against the OCR-parsed page-1 "Page Totals" row, and the
   parser fails loudly on any disagreement.

2. "Local Results" certificates (pdftotext -layout text
   ``Crawford_County_{Democratic,Republican}_Local_Results_2023_Primary.txt``):
   one section per municipality, ward/borough/township offices with
   MACHINE/HAND/TOTALS per candidate.  Output is the 10-column precinct
   schema; every row's party comes from which ballot's file it came from
   (DEM / REP), including the "SCATTERED" -> Write-ins rows.

Usage:
    python parsers/pa_crawford_primary_2023_results_parser.py <input> [<input2> ...] <output>

All inputs must belong to one family; the output schema follows the
family (9 columns for County Results, 10 for Local Results).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import OrderedDict

COUNTY = "Crawford"

COUNTY_FIELDS = ["county", "office", "district", "party", "candidate", "votes",
                 "election_day", "mail", "provisional"]
PRECINCT_FIELDS = ["county", "precinct", "office", "district", "party",
                   "candidate", "votes", "election_day", "mail", "provisional"]

# ==========================================================================
# OCR computing-paper parsing (county-level source)
# ==========================================================================

CONTEST_OFFICE = {
    "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
    "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
    "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
    "COMMISSIONER": "County Commissioner",
    "CORONER": "Coroner",
    "AUDITOR": "County Auditor",
    "DISTRICT ATTORNEY": "District Attorney",
    "REGISTER AND RECORDER": "Register & Recorder",
    "PROTHONOTARY": "Prothonotary",
    "SHERIFF": "Sheriff",
}

# Canonical candidate names (fixes OCR garbles; matches the conventions of
# work2023/county_level/20231107__pa__general__crawford__county.csv, which
# used title case for the countywide candidates).
CANON = {
    "DANIEL MCCAFFERY": "Daniel McCaffery",
    "DEBBIE KUNSELMAN": "Debbie Kunselman",
    "DBBIE KUNSELMAN": "Debbie Kunselman",
    "PAT DUGAN": "Pat Dugan",
    "TIMIKA LANE": "Timika Lane",
    "JILL BECK": "Jill Beck",
    "MATT WOLF": "Matt Wolf",
    "BRYAN NEFT": "Bryan Neft",
    "PATRICIA A. MCCULLOUGH": "Patricia A. McCullough",
    "PATRICIA A MCCULLOUGH": "Patricia A. McCullough",
    "CAROLYN CARLUCCIO": "Carolyn Carluccio",
    "MARIA BATTISTA": "Maria Battista",
    "MARIA BATTSTA": "Maria Battista",
    "HARRY F SMAIL JR": "Harry F. Smail Jr.",
    "HARRY F. SMAIL JR.": "Harry F. Smail Jr.",
    "JOSH PRINCE": "Josh Prince",
    "MEGAN MARTIN": "Megan Martin",
    "CHRISTOPHER R SEELEY": "Christopher R. Seeley",
    "SHERMAN ALLEN": "Sherman Allen",
    "TODD SIPLE": "Todd Siple",
    "BRENDA BRADEN": "Brenda Braden",
    "ROGER L SCHLOSER": "Roger L. Schloser",
    "SCOTT T SCHELL": "Scott Schell",
    "ERIC HENRY": "Eric Henry",
    "ERIC COSTON": "Eric Coston",
    "AIMEE C. SPITZER": "Aimee C. Spitzer",
    "AIMEE C SPITZER": "Aimee C. Spitzer",
    "TONI LONGO": "Toni Longo",
    "DARIEN PFAFF": "Darien Pfaff",
    "RENEE KISER": "Renee Kiser",
    "STACEY A HOLZER": "Stacey A. Holzer",
    "KELSEY ZIMMERMAN": "Kelsey Zimmerman",
    "JOSHUA MANUEL": "Joshua Manuel",
    "PAULA DIGIACOMO": "Paula DiGiacomo",
    "BETH M FORBES": "Beth M. Forbes",
    "ROAN HUNTER": "Roan Hunter",
    "EMMY ARNETT": "Emmy Arnett",
    "DAVE POWERS": "Dave Powers",
    "DAVID POWERS": "Dave Powers",
}

# Grand Total rows transcribed by eye from 200-dpi renders of the source PDF
# pages, for contest spreads whose OCR text is a stub or garbled.  Values are
# (machine, hand, total) in the printed column order; the Scattered column is
# keyed "Write-ins".
VISION_TOTALS = {
    ("DEM", "Coroner"): [
        ("Eric Coston", (229, 2, 231)),
        ("Toni Longo", (127, 0, 127)),
        ("Write-ins", (264, 2, 266)),
    ],
    ("DEM", "Register & Recorder"): [
        ("Write-ins", (175, 2, 177)),
    ],
    ("REP", "Justice of the Supreme Court"): [
        ("Patricia A. McCullough", (5597, 10, 5607)),
        ("Carolyn Carluccio", (3056, 1, 3057)),
        ("Write-ins", (23, 0, 23)),
    ],
    ("REP", "Coroner"): [
        ("Eric Coston", (5954, 10, 5964)),
        ("Aimee C. Spitzer", (1609, 2, 1611)),
        ("Toni Longo", (1804, 4, 1808)),
        ("Write-ins", (17, 0, 17)),
    ],
    ("REP", "County Auditor"): [
        ("Renee Kiser", (4924, 6, 4930)),
        ("Stacey A. Holzer", (2659, 4, 2663)),
        ("Kelsey Zimmerman", (4487, 9, 4496)),
        ("Joshua Manuel", (2811, 3, 2814)),
        ("Write-ins", (46, 1, 47)),
    ],
    ("REP", "Prothonotary"): [
        ("Roan Hunter", (2720, 4, 2724)),
        ("Emmy Arnett", (6559, 8, 6567)),
        ("Write-ins", (8, 0, 8)),
    ],
}

# Scattered-column (Write-ins) values transcribed by eye from the printed
# "Grand Total" row of each contest's second page (200-dpi renders), for the
# contests NOT covered by VISION_TOTALS.  The OCR Grand Total row frequently
# misaligns the Scattered cell (reads 0), so these transcriptions are
# authoritative; each reconciles as TF1 + page-2 PageTotals == Grand Total
# (e.g. DEM Superior scattered 21 + 16 = 37).
VISION_WRITEINS = {
    ("DEM", "Justice of the Supreme Court"): (19, 1, 20),
    ("DEM", "Judge of the Superior Court"): (36, 1, 37),
    ("DEM", "Judge of the Commonwealth Court"): (16, 1, 17),
    ("DEM", "County Commissioner"): (436, 5, 441),
    ("DEM", "District Attorney"): (130, 2, 132),
    ("DEM", "Prothonotary"): (181, 1, 182),
    ("DEM", "Sheriff"): (107, 1, 108),
    ("REP", "Judge of the Superior Court"): (51, 0, 51),
    ("REP", "Judge of the Commonwealth Court"): (18, 0, 18),
    ("REP", "County Commissioner"): (66, 0, 66),
    ("REP", "District Attorney"): (41, 0, 41),
    ("REP", "Register & Recorder"): (19, 0, 19),
    ("REP", "Sheriff"): (63, 0, 63),
}

# Page-1 "Page Totals" for the vision contests whose page 1 DID parse,
# transcribed from the same page images; asserted against the OCR parse.
VISION_PAGE1 = {
    ("DEM", "Coroner"): [
        ("Eric Coston", (128, 2, 130)),
        ("Toni Longo", (62, 0, 62)),
        ("Write-ins", (136, 2, 138)),
    ],
    ("DEM", "Register & Recorder"): [
        ("Write-ins", (92, 2, 94)),
    ],
    ("REP", "Justice of the Supreme Court"): [
        ("Patricia A. McCullough", (2894, 2, 2896)),
        ("Carolyn Carluccio", (1546, 0, 1546)),
        ("Write-ins", (11, 0, 11)),
    ],
    ("REP", "County Auditor"): [
        ("Renee Kiser", (2445, 1, 2446)),
        ("Stacey A. Holzer", (1306, 1, 1307)),
        ("Kelsey Zimmerman", (2411, 2, 2413)),
        ("Joshua Manuel", (1500, 0, 1500)),
        ("Write-ins", (30, 1, 31)),
    ],
    ("REP", "Prothonotary"): [
        ("Roan Hunter", (1444, 0, 1444)),
        ("Emmy Arnett", (3319, 2, 3321)),
        ("Write-ins", (4, 0, 4)),
    ],
}


# ---- OCR table plumbing --------------------------------------------------

TD = re.compile(r"<td([^>]*)>(.*?)</td>", re.S)
TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
COLSPAN = re.compile(r'colspan="?(\d+)"?')
HEADER_JUNK = re.compile(
    r"COMPUTING|OFFICIAL RETURNS|PRIMARY ELECTION|ELECTION DISTRICTS|"
    r"CRAWFORD COUNTY|HELD IN|TUESDAY|MAY 16|CERTIFICAT|FINAL", re.I)
MHT_SET = {"Machine", "Hand", "Total"}
CONTEST_RE = re.compile(r"^(.+?)\s*\((\d+)\s*/\s*(\d+)\s*year terms?\)\s*$", re.I)
TOTALS_LABEL = re.compile(
    r"^(page totals|age totals|fage totals|totals from page 1|grand total)$", re.I)


def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def table_rows(html):
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        for r in TR.findall(tbl):
            cells = []
            for attrs, body in TD.findall(r):
                m = COLSPAN.search(attrs)
                cells.append((strip_tags(body), int(m.group(1)) if m else 1))
            if cells:
                yield cells


def expand(cells):
    out = []
    for v, s in cells:
        out.extend([v] * s)
    return out


def width(cells):
    return sum(s for _, s in cells)


def starts(cells):
    out, run = [], 0
    for _, s in cells:
        out.append(run)
        run += s
    return out


def parse_int(v):
    v = (v or "").strip().replace(",", "")
    return int(v) if re.fullmatch(r"-?\d+", v) else None


def group_values(cells, offset):
    """(machine, hand, total) per column group of a data/totals row."""
    exp = expand(cells)
    out = {}
    g, i = 0, offset
    while i + 2 < len(exp):
        out[g] = tuple((parse_int(exp[i + k]) or 0) for k in range(3))
        g += 1
        i += 3
    return out


def merge_sums(acc, gv):
    for g, v in gv.items():
        m0, h0, t0 = acc.get(g, (0, 0, 0))
        acc[g] = (m0 + v[0], h0 + v[1], t0 + v[2])


def contest_office(header, path, pg):
    if header in CONTEST_OFFICE:
        return CONTEST_OFFICE[header]
    raise SystemExit(f"unrecognized contest header in {path} page {pg}: {header!r}")


def parse_computing_page(html):
    rows = list(table_rows(html))
    header = None
    for cells in rows:
        for v, _ in cells:
            m = CONTEST_RE.match(v.strip())
            if m:
                header = m.group(1).strip().upper()
                break
        if header:
            break
    if not header:
        return None

    # locate the Machine/Hand/Total row
    mht_i = None
    for i, cells in enumerate(rows):
        vals = [v for v, _ in cells]
        if sum(1 for v in vals if v in MHT_SET) >= 3:
            mht_i = i
            break
    info = {"header": header, "names": {}, "scattered": None,
            "district_sums": {}, "page_totals": None, "totals_from_p1": None,
            "grand_total": None}
    if mht_i is None:
        return info

    # candidate-name row: nearest preceding row with non-header text
    for i in range(mht_i - 1, -1, -1):
        texts = [v for v, _ in rows[i]]
        alpha = [v for v in texts if v and re.search(r"[A-Za-z]", v)]
        if not alpha:
            continue
        if all(HEADER_JUNK.search(v) for v in alpha):
            continue
        for (val, _), start in zip(rows[i], starts(rows[i])):
            v = val.strip()
            if not v or HEADER_JUNK.search(v):
                continue
            if v.upper().startswith("SCATTERED"):
                # a genuine scattered column sits beyond the first groups
                if start >= 9:
                    info["scattered"] = start // 3
            elif not v.isdigit():
                info["names"].setdefault(start // 3, v)
        break

    page_re = re.compile(r"^PAGE\s+\d+$", re.I)
    for cells in rows[mht_i + 1:]:
        label = cells[0][0].strip()
        if TOTALS_LABEL.match(label):
            gv = group_values(cells, offset=1)
            low = label.lower()
            if "grand" in low:
                info["grand_total"] = gv
            elif "from page 1" in low:
                info["totals_from_p1"] = gv
            else:
                info["page_totals"] = gv
        elif page_re.match(label):
            continue
        elif re.fullmatch(r"[\d\s,]*", label):
            merge_sums(info["district_sums"], group_values(cells, offset=0))
        else:
            merge_sums(info["district_sums"], group_values(cells, offset=1))
    return info


def canon_name(raw):
    base = raw.split(",")[0]
    key = re.sub(r"\s+", " ", base).strip().upper().rstrip(".")
    if key in CANON:
        return CANON[key]
    return re.sub(r"\s+", " ", base).strip()


def parse_county_ocr(paths):
    """Parse the Dem/Rep computing papers -> list of contest dicts."""
    contest_pages = OrderedDict()
    for path in paths:
        low = path.lower()
        if "dem" in low:
            party = "DEM"
        elif "rep" in low:
            party = "REP"
        else:
            raise SystemExit(f"cannot infer ballot party from file name: {path}")
        text = open(path, encoding="utf-8").read()
        parts = re.split(r"===== PAGE (\d+) =====", text)
        pages = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        for pg in sorted(pages):
            info = parse_computing_page(pages[pg])
            if info:
                office = contest_office(info["header"], path, pg)
                contest_pages.setdefault((party, office), []).append((pg, info))

    contests = []
    for (party, office), pinfos in contest_pages.items():
        pinfos.sort(key=lambda x: x[0])
        names, scattered = {}, None
        for _, info in pinfos:
            for g, n in info["names"].items():
                names.setdefault(g, n)
            if scattered is None and info["scattered"] is not None:
                scattered = info["scattered"]

        # page 1 of the pair = the page without a Grand Total row
        pt1 = pt2 = tf1 = gt = None
        for _, info in pinfos:
            if info["grand_total"]:
                gt = info["grand_total"]
                pt2 = info["page_totals"]
                tf1 = info["totals_from_p1"]
            else:
                pt1 = info["page_totals"]

        groups = sorted(names)
        if scattered is not None and scattered not in groups:
            groups.append(scattered)

        totals, sources, notes = {}, {}, []
        for g in groups:
            candidates = []
            if gt and g in gt and gt[g][0] + gt[g][1] == gt[g][2]:
                candidates.append((gt[g], "Grand Total row"))
            if pt1 and pt2 and g in pt1 and g in pt2:
                a, b = pt1[g], pt2[g]
                s = (a[0] + b[0], a[1] + b[1], a[2] + b[2])
                if s[0] + s[1] == s[2]:
                    candidates.append((s, "PageTotals p1+p2"))
            dsum = {}
            for _, info in pinfos:
                if g in info["district_sums"]:
                    merge_sums(dsum, {g: info["district_sums"][g]})
            if dsum and g in dsum:
                v = dsum[g]
                if v[0] + v[1] == v[2]:
                    candidates.append((v, "sum of district rows"))
            if candidates:
                if g == scattered and len(candidates) > 1:
                    # the OCR Grand Total row often misaligns the Scattered
                    # cell; the per-page PageTotals rows parse aligned, so
                    # prefer their sum for the write-in column
                    candidates.sort(key=lambda c: 0 if c[1] == "PageTotals p1+p2" else 1)
                totals[g], sources[g] = candidates[0]
                if len(candidates) > 1 and any(c[0] != candidates[0][0] for c in candidates[1:]):
                    notes.append(f"{party} {office} g{g}: OCR sources disagree "
                                 f"{[c[0] for c in candidates]}")
            else:
                totals[g], sources[g] = None, None

        contests.append({"party": party, "office": office, "names": names,
                         "scattered": scattered, "totals": totals,
                         "sources": sources, "notes": notes, "pt1": pt1})
    return contests


def build_county_rows(contests):
    rows, notes = [], []
    for c in contests:
        party, office = c["party"], c["office"]
        vision = {n: v for n, v in VISION_TOTALS.get((party, office), [])}
        if vision:
            # cross-check: OCR page-1 Page Totals vs transcribed values
            vp1 = dict(VISION_PAGE1.get((party, office), []))
            if vp1 and c["pt1"]:
                name_to_group = {canon_name(n): g for g, n in c["names"].items()}
                for cname, want in vp1.items():
                    g = c["scattered"] if cname == "Write-ins" else name_to_group.get(cname)
                    got = c["pt1"].get(g) if g is not None else None
                    if got is None:
                        notes.append(f"{party} {office} {cname}: page-1 PageTotals not parsed by OCR "
                                     f"(transcribed {want})")
                    elif tuple(got) != tuple(want):
                        raise SystemExit(f"VISION cross-check FAILED: {party} {office} {cname} "
                                         f"OCR page-1 PageTotals {tuple(got)} != transcribed {want}")
            for cname, v in vision.items():
                rows.append({"county": COUNTY, "office": office, "district": "",
                             "party": party, "candidate": cname, "votes": v[2]})
                notes.append(f"{party} {office} {cname}: total {v[2]} transcribed from PDF page image "
                             f"(OCR page stub/garbled)")
            continue
        for g in sorted(c["names"]):
            name = canon_name(c["names"][g])
            t = c["totals"].get(g)
            if t is None:
                notes.append(f"{party} {office} {name}: NO usable OCR total - row omitted")
                continue
            rows.append({"county": COUNTY, "office": office, "district": "",
                         "party": party, "candidate": name, "votes": t[2]})
        if c["scattered"] is not None:
            g = c["scattered"]
            t = c["totals"].get(g)
            vis = VISION_WRITEINS.get((party, office))
            if vis is not None:
                if t is not None and t[2] != vis[2]:
                    notes.append(f"{party} {office} Write-ins: OCR total {t[2]} disagrees with "
                                 f"printed Grand Total {vis[2]} transcribed from the page image; "
                                 f"using {vis[2]}")
                elif t is None or t[2] == 0:
                    notes.append(f"{party} {office} Write-ins: {vis[2]} transcribed from the "
                                 f"printed Grand Total row (OCR page image)")
                rows.append({"county": COUNTY, "office": office, "district": "",
                             "party": party, "candidate": "Write-ins", "votes": vis[2]})
            elif t is None:
                notes.append(f"{party} {office} Write-ins: NO usable OCR total - row omitted")
            else:
                rows.append({"county": COUNTY, "office": office, "district": "",
                             "party": party, "candidate": "Write-ins", "votes": t[2]})
        notes.extend(c["notes"])
    return rows, notes


# ==========================================================================
# Local certificate parsing (precinct source)
# ==========================================================================

PRIMARY_LABELS = {
    "SCHOOL DIRECTOR", "SUPERVISOR", "AUDITOR", "COUNCIL", "TREASURER",
    "CONTROLLER", "CONSTABLE", "MAYOR", "TAX COLLECTOR",
}

ROW = re.compile(
    r"^(?P<text>.*?)\s{2,}(?P<mac>\d[\d,]*)\s+(?P<hand>\d[\d,]*)\s+(?P<tot>\d[\d,]*)\s*$")
MUNI = re.compile(r"RESULTS OF VOTES CAST IN (.+?)\s*$")
RESIDENCE = re.compile(r",\s+[A-Za-z .'\-]*County\.?\s*$", re.I)
TERM = re.compile(r"\((\d+)\s*/\s*(\d+)\s*YRS?\)")

SCHOOL_FULL = {
    "PENNCREST": "Penncrest School District",
    "CRAWFORD CENTRAL": "Crawford Central School District",
    "CONNEAUT": "Conneaut School District",
    "TITUSVILLE": "Titusville School District",
    "JAMESTOWN": "Jamestown School District",
    "CORRY": "Corry School District",
    "UNION CITY": "Union City School District",
}

# a wrapped school-district label printed with a single space before the
# candidate name (e.g. "PENNCREST School Dist. ELI SKELTON, Crawford County")
SCHOOL_PREFIX = re.compile(
    r"^([A-Za-z][A-Za-z .']*?School Dist\.?)\s+(?P<name>.+)$", re.I)


def full_label(frag):
    f = frag.strip().upper()
    if len(f) < 4 or f in PRIMARY_LABELS:
        return frag
    matches = [p for p in PRIMARY_LABELS if p.startswith(f)]
    return matches[0] if len(matches) == 1 else frag


def muni_type(muni):
    u = muni.upper()
    if "TWP" in u:
        return "Township"
    if "BORO" in u:
        return "Borough"
    return "City"


class LocalState:
    """Accumulates contests for one municipality section."""

    def __init__(self, results, muni):
        self.results = results
        self.muni = muni
        self.parts = []
        self.rows = []

    def flush(self):
        if self.rows:
            self.results.append((self.muni, list(self.parts), list(self.rows)))
        self.rows = []

    def frag(self, text):
        frag = full_label(text)
        if not frag:
            return
        fu = frag.upper()
        if fu == "OFFICE OF" or fu.startswith("OFFICE OF "):
            return
        if fu in PRIMARY_LABELS and (self.rows or any(
                p.upper() in PRIMARY_LABELS for p in self.parts)):
            if self.rows:
                self.flush()
                self.parts = []
        if not self.parts or self.parts[-1] != frag:
            self.parts.append(frag)

    def candidate(self, name, votes):
        cand = RESIDENCE.sub("", name.strip()).strip()
        if cand.upper() == "SCATTERED":
            cand = "Write-ins"
        self.rows.append((cand, votes))


def parse_local_txt(path):
    with open(path, encoding="utf-8") as fh:
        pages = fh.read().split("\x0c")

    results = []
    state = None
    muni = None
    for pg in pages:
        if "RESULTS OF VOTES CAST IN" not in pg:
            if results:
                break  # rollup pages begin
            continue
        for raw in pg.split("\n"):
            line = raw.rstrip()
            if not line.strip():
                continue
            m = MUNI.search(line)
            if m:
                new_muni = re.sub(r"\s+", " ", m.group(1).strip())
                if new_muni != muni:
                    if state:
                        state.flush()
                    muni = new_muni
                    state = LocalState(results, muni)
                continue
            if muni is None:
                continue
            if "CANDIDATE" in line.upper() and "NAME" in line.upper():
                continue
            if "May 16, 2023" in line and "Primary" in line:
                continue
            if re.fullmatch(r"[\d\s,]+", line):
                continue
            rm = ROW.match(line)
            if rm and rm.group("text").strip():
                groups = [g for g in re.split(r"\s{2,}", rm.group("text").strip()) if g]
                name = groups[-1].strip()
                name_u = name.upper()
                if name_u in PRIMARY_LABELS or re.fullmatch(
                        r"\(\d+\s*/\s*\d+\s*YRS?\)", name_u):
                    for g in groups[:-1]:
                        state.frag(g)
                    state.frag(name)
                    continue
                if name.isdigit():
                    for g in groups[:-1]:
                        state.frag(g)
                    continue
                for g in groups[:-1]:
                    state.frag(g)
                sm = SCHOOL_PREFIX.match(name)
                if sm:
                    # wrapped school-district label glued to the candidate
                    state.frag(sm.group(1))
                    name = sm.group("name").strip()
                state.candidate(name, int(rm.group("tot").replace(",", "")))
            else:
                # a SCATTERED row whose Machine/Hand columns collapsed to a
                # single printed value (pdftotext squeezed them out)
                sm = re.match(r"^\s*SCATTERED\s+([\d,]+)\s*$", line)
                if sm:
                    state.candidate("Write-ins", int(sm.group(1).replace(",", "")))
                    continue
                frag = line[:19].strip()
                if frag:
                    state.frag(frag)
    if state:
        state.flush()
    return results


def split_label(parts, muni):
    lab = " ".join(p.strip() for p in parts if p.strip())
    up = lab.upper()
    mt = muni_type(muni)
    if up.startswith("SCHOOL DIRECTOR"):
        rest = lab[len("SCHOOL DIRECTOR"):]
        rest = re.sub(r"\(\d+\s*/\s*\d+\s*yrs?\)", "", rest, flags=re.I)
        rest = re.sub(r"\s+", " ", rest).strip()
        district = None
        for key, full in SCHOOL_FULL.items():
            if rest.upper().startswith(key):
                district = full
                m = re.search(r"REGION\s+([0-9IVX]+)", rest, re.I)
                if m:
                    district = f"{full}, Region {m.group(1)}"
                break
        if district is None and rest:
            district = rest.title()
        return ("School Director", district)
    if up.startswith("SUPERVISOR"):
        office = "Township Supervisor"
    elif up.startswith("AUDITOR"):
        office = f"{mt} Auditor"
    elif up.startswith("COUNCIL"):
        office = f"{mt} Council"
    elif up.startswith("TREASURER"):
        office = f"{mt} Treasurer"
    elif up.startswith("CONTROLLER"):
        office = "City Controller"
    elif up.startswith("CONSTABLE"):
        office = "Constable"
    elif up.startswith("MAYOR"):
        office = "Mayor"
    elif up.startswith("TAX COLLECTOR"):
        office = "Tax Collector"
    else:
        office = lab.title()
    return (office, muni)


def build_precinct_rows(results_all):
    # one pass to collect term variants per (office, district)
    entries = []
    terms = {}
    for party, results in results_all:
        for muni, parts, rows in results:
            office, district = split_label(parts, muni)
            m = TERM.search(" ".join(parts).upper())
            term = int(m.group(2)) if m else None
            entries.append((party, muni, term, office, district, rows))
            terms.setdefault((office, district), set()).add(term)
    out = []
    for party, muni, term, office, district, rows in entries:
        office_out = office
        if term is not None and len(terms[(office, district)]) > 1:
            office_out = f"{office} ({term} Year)"
        for cand, votes in rows:
            out.append({
                "county": COUNTY, "precinct": muni, "office": office_out,
                "district": district or "", "party": party, "candidate": cand,
                "votes": votes, "election_day": "", "mail": "", "provisional": "",
            })
    return out


# ==========================================================================
# CLI
# ==========================================================================

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("output")
    args = ap.parse_args(argv)

    county_paths = [p for p in args.inputs if "county_results" in
                    re.sub(r"[ _\-]+", "_", p.lower())]
    local_paths = [p for p in args.inputs if p not in county_paths]
    if county_paths and local_paths:
        raise SystemExit("mixing County Results and Local Results inputs; run twice")
    if county_paths:
        contests = parse_county_ocr(county_paths)
        rows, notes = build_county_rows(contests)
        fields = COUNTY_FIELDS
        n_contests = len({(r["office"], r["party"]) for r in rows})
    else:
        results_all = []
        for path in local_paths:
            low = path.lower()
            if "dem" in low:
                party = "DEM"
            elif "rep" in low:
                party = "REP"
            else:
                raise SystemExit(f"cannot infer ballot party from file name: {path}")
            res = parse_local_txt(path)
            print(f"{path}: {len(res)} contest blocks", file=sys.stderr)
            results_all.append((party, res))
        rows = build_precinct_rows(results_all)
        notes = []
        fields = PRECINCT_FIELDS
        n_contests = len({(r["office"], r["district"]) for r in rows})

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    desc = f"{len({(r['office'], r['party']) for r in rows})} party-contests" if county_paths \
        else f"{len({r['precinct'] for r in rows})} municipalities"
    print(f"wrote {len(rows)} rows ({n_contests} contests, {desc}) -> {args.output}",
          file=sys.stderr)
    for n in notes:
        print("  NOTE:", n, file=sys.stderr)


if __name__ == "__main__":
    main()