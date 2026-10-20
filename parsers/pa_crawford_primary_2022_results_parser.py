#!/usr/bin/env python3
"""Parser for Crawford County 2022 Primary (May 17, 2022) precinct results.

Sources (all "COMPUTING PAPER / OFFICIAL RETURNS" crosstabs, Acrobat text layer
over the scan - same family as the county's 2022/2023 computing papers; one
1-2 landscape-page spread per contest):

  * "Crawford PA Revised 2022P State-Fed Dem Races.pdf"  (9 DEM contests)
  * "Crawford PA Revised 2022P State-Fed Rep Races.pdf"  (9 REP contests)
  * "Crawford PA Revised 2022P US Senator Recount.pdf"   (the June recount of
    the Republican U.S. Senator race; supersedes the pre-recount GOP U.S.
    Senate numbers in the Rep Races file)

Every contest page is a grid: precinct rows x candidate column groups, where
each candidate/Scattered group carries rotated (90-degree) Machine / Hand /
Total sub-column labels (12 groups printed on every page; unused trailing
groups carry only a printed zero Total cell).  The parser works from
pdfplumber word coordinates:

  * office title   - upright words near the top of the page
  * column labels  - rotated 'Machine'/'Hand'/'Total' whose x0 positions
    define the numeric column groups
  * candidates     - rotated header words above the labels, assigned to their
    nearest column group and validated against expected candidate tokens
  * data rows      - upright words clustered by 'top'; leading non-numeric
    words left of the grid are the precinct name; trailing rows
    ('Page Totals', 'Totals from Page 1', 'Grand Total') are used for
    verification, not output

Validation (parser exits nonzero on any failure):
  * every parsed precinct label must be one of the county's 68 election
    districts (list taken from the shipped text-based 2022 general computing
    paper, 20221108__pa__general__crawford__precinct.csv)
  * machine + hand == total for every candidate in every precinct row
  * Grand Total / page Totals rows must equal the sums over the contest's
    precinct rows, Total cell by candidate
  * each contest must list exactly the expected precinct set (68 EDs
    countywide; 39 / 13 / 16 EDs for State House districts 6 / 64 / 65),
    each exactly once
  * write-in-only contests (State House 64 / 65, DEM) are handled: the header
    of their Scattered group is the only candidate header on the page

Semantics (same county tabulation as the 2022 general parser, where the ENR
cross-check established Machine 10961 + Hand 120 = Total 11081 with ENR
provisional 120):

    votes        = Total
    election_day = Machine   (machine-counted ballots)
    provisional  = Hand      (hand-counted paper ballots)
    mail         = ""        (source does not separate absentee/mail)

Per the 2022-primary house rules the party column carries the contest's
ballot party (DEM/REP) on every row, Write Ins included.

Usage:
    python parsers/pa_crawford_primary_2022_results_parser.py <dem_pdf> <rep_pdf> \
        [recount_pdf] <output_csv>

When the recount file is supplied, the Rep Races file's own U.S. Senate
contest is parsed only for a printed comparison against the recount totals
(deltas are reported to stderr, not written to the output).
"""

from __future__ import annotations

import csv
import difflib
import re
import sys
from collections import OrderedDict

import pdfplumber

COUNTY = "Crawford"
FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

WI = "Write Ins"  # the Scattered column
SCATTERED_TOKENS = ["SCATTERED", "SCATERRED", "SCATTRED", "SCTTERED"]

# The county's 68 election districts, from the shipped text-based 2022 general
# computing paper (20221108__pa__general__crawford__precinct.csv).
PRECINCTS = [
    "Athens Twp", "Beaver Twp", "Bloomfield Twp", "Blooming Valley Boro",
    "Cambridge Springs Boro", "Cambridge Twp", "Centerville Boro",
    "Cochranton Boro", "Conneaut Lake Boro", "Conneaut Twp", "Conneautville Boro",
    "Cussewago Twp", "East Fairfield Twp", "East Fallowfield Twp",
    "East Mead Twp", "Fairfield Twp", "Greenwood Twp", "Hayfield Twp",
    "Hydetown Boro", "Linesville Boro", "Meadville 1-1", "Meadville 1-2",
    "Meadville 2-1", "Meadville 2-2", "Meadville 2-3", "Meadville 3-1",
    "Meadville 3-2", "Meadville 4", "Meadville 5", "North Shenango Twp",
    "Oil Creek Twp", "Pine Twp", "Randolph Twp", "Richmond Twp", "Rockdale Twp",
    "Rome Twp", "Sadsbury Twp", "Saegertown Boro", "South Shenango Twp",
    "Sparta Twp", "Spartansburg Boro", "Spring Twp", "Springboro Boro",
    "Steuben Twp", "Summerhill Twp", "Summit Twp", "Titusville 1-1",
    "Titusville 1-2", "Titusville 2-1", "Titusville 2-2", "Titusville 3",
    "Titusville 4", "Titusville 5", "Townville Boro", "Troy Twp", "Union Twp",
    "Venango Boro", "Venango Twp", "Vernon Twp 1", "Vernon Twp 2",
    "Vernon Twp 3", "Wayne Twp", "West Fallowfield Twp", "West Mead Twp 1",
    "West Mead Twp 2", "West Shenango Twp", "Woodcock Boro", "Woodcock Twp",
]
PRECNORM = {re.sub(r"[^A-Z0-9]", "", p.upper()): p for p in PRECINCTS}

# Known OCR garbles of precinct labels in these scans (normalized form -> ED).
CORRECTIONS = {
    "HYDETOWNTWP": "Hydetown Boro",
}

# Legislative-district precinct coverage; other contests must cover all 68 EDs.
LEG_PRECINCTS = {
    ("State House", "6"): {'Beaver Twp', 'Cochranton Boro', 'Conneaut Lake Boro', 'Conneaut Twp', 'Conneautville Boro', 'East Fairfield Twp', 'East Fallowfield Twp', 'East Mead Twp', 'Fairfield Twp', 'Greenwood Twp', 'Hayfield Twp', 'Linesville Boro', 'Meadville 1-1', 'Meadville 1-2', 'Meadville 2-1', 'Meadville 2-2', 'Meadville 2-3', 'Meadville 3-1', 'Meadville 3-2', 'Meadville 4', 'Meadville 5', 'North Shenango Twp', 'Pine Twp', 'Randolph Twp', 'Sadsbury Twp', 'South Shenango Twp', 'Spring Twp', 'Springboro Boro', 'Summerhill Twp', 'Summit Twp', 'Union Twp', 'Vernon Twp 1', 'Vernon Twp 2', 'Vernon Twp 3', 'Wayne Twp', 'West Fallowfield Twp', 'West Mead Twp 1', 'West Mead Twp 2', 'West Shenango Twp'},
    ("State House", "64"): {'Hydetown Boro', 'Oil Creek Twp', 'Rome Twp', 'Steuben Twp', 'Titusville 1-1', 'Titusville 1-2', 'Titusville 2-1', 'Titusville 2-2', 'Titusville 3', 'Titusville 4', 'Titusville 5', 'Townville Boro', 'Troy Twp'},
    ("State House", "65"): {'Athens Twp', 'Bloomfield Twp', 'Blooming Valley Boro', 'Cambridge Springs Boro', 'Cambridge Twp', 'Centerville Boro', 'Cussewago Twp', 'Richmond Twp', 'Rockdale Twp', 'Saegertown Boro', 'Sparta Twp', 'Spartansburg Boro', 'Venango Boro', 'Venango Twp', 'Woodcock Boro', 'Woodcock Twp'},
}

# Contest definitions: (title key, district) -> per party (office, candidates).
# Each candidate is (display name, required uppercase tokens to find in that
# column's rotated header, on any page of the contest).  Candidates are listed
# in printed column order; the last entry is the Scattered column.
STATE_HOUSE_DEM = {
    "6": [("Nerissa Galt", ["GALT"])],
    "64": [],
    "65": [],
}


def _c(office, cands):
    return (office, cands + [(WI, SCATTERED_TOKENS)])


CONTESTS = {
    ("US SENATOR", ""): {
        "DEM": _c("U.S. Senate", [
            ("Alex Khalil", ["KHALIL"]), ("Conor Lamb", ["LAMB"]),
            ("Malcolm Kenyatta", ["KENYATTA"]), ("John Fetterman", ["FETTERMAN"])]),
        "REP": _c("U.S. Senate", [
            ("Kathy Barnette", ["BARNETTE"]), ("Mehmet Oz", ["OZ", "MEHMET"]),
            ("George Bochetto", ["BOCHETTO"]), ("Jeff Bartos", ["BARTOS"]),
            ("Dave McCormick", ["MCCORMICK"]), ("Sean Gale", ["GALE", "SEAN"]),
            ("Carla Sands", ["SANDS"])]),
    },
    ("GOVERNOR", ""): {
        "DEM": _c("Governor", [("Josh Shapiro", ["SHAPIRO"])]),
        "REP": _c("Governor", [
            ("Lou Barletta", ["BARLETTA"]), ("Douglas V Mastriano", ["MASTRIANO"]),
            ("Nche Zama", ["ZAMA"]), ("Dave White", ["WHITE"]),
            ("Melissa Hart", ["HART"]), ("Bill McSwain", ["MCSWAIN"]),
            ("Charlie Gerow", ["GEROW"]), ("Joe Gale", ["GALE", "JOE"]),
            ("Jake Corman", ["CORMAN"])]),
    },
    ("LIEUTENANT GOVERNOR", ""): {
        "DEM": _c("Lieutenant Governor", [
            ("Austin Davis", ["DAVIS"]), ("Ray Sosa", ["SOSA"]),
            ("Brian Sims", ["SIMS"])]),
        "REP": _c("Lieutenant Governor", [
            ("Clarice Schillinger", ["SCHILLINGER"]),
            ("James Jones", ["JONES"]), ("Rick Saccone", ["SACCONE"]),
            ("John Brown", ["BROWN"]), ("Chris Frye", ["FRYE"]),
            ("Jeff Coleman", ["COLEMAN"]), ("Russ Diamond", ["DIAMOND"]),
            ("Carrie Delrosso", ["DELROSSO"]), ("Teddy Daniels", ["DANIELS"])]),
    },
    ("UNITED STATES REPRESENTATIVE IN CONGRESS", "16"): {
        "DEM": _c("U.S. House", [("Dan Pastore", ["PASTORE"]),
                                 ("Rick Telesz", ["TELESZ"])]),
        "REP": _c("U.S. House", [("Mike Kelly", ["KELLY"])]),
    },
    ("SENATOR IN THE GENERAL ASSEMBLY", "50"): {
        "DEM": _c("State Senate", [("Rianna Czech", ["CZECH"])]),
        "REP": _c("State Senate", [("Michele Brooks", ["BROOKS"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "6"): {
        "DEM": _c("State House", [("Nerissa Galt", ["GALT"])]),
        "REP": _c("State House", [("Brad Roae", ["ROAE"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "64"): {
        "DEM": _c("State House", []),          # write-ins only
        "REP": _c("State House", [("R. Lee James", ["JAMES"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "65"): {
        "DEM": _c("State House", []),          # write-ins only
        "REP": _c("State House", [("Kathy Rapp", ["RAPP"])]),
    },
    ("MEMBER OF THE DEMOCRATIC STATE COMMITTEE", ""): {
        "DEM": _c("Member of Democratic State Committee",
                  [("Christopher R. Seeley", ["SEELEY"])]),
    },
    ("MEMBER OF THE REPUBLICAN STATE COMMITTEE", ""): {
        "REP": _c("Member of Republican State Committee", [
            ("Michael D. Forbes", ["FORBES"]), ("Christine J. Greig", ["CHRISTINE"]),
            ("George D. Greig", ["GEORGE"])]),
    },
}

STATE_LEG_RE = {
    "SENATOR IN THE GENERAL ASSEMBLY": (re.compile(r"(\d+)TH SENATORIAL"), "State Senate"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": (re.compile(r"(\d+)TH LEGISLATIVE"), "State House"),
}


def resolve_title(title):
    """Return (title_key, district) for a contest page title, or None."""
    up = re.sub(r"\s+", " ", title.upper())
    # order matters: "LIEUTENANT GOVERNOR" contains "GOVERNOR"
    for key in ("MEMBER OF THE DEMOCRATIC STATE COMMITTEE",
                "MEMBER OF THE REPUBLICAN STATE COMMITTEE",
                "LIEUTENANT GOVERNOR",
                "UNITED STATES REPRESENTATIVE IN CONGRESS",
                "SENATOR IN THE GENERAL ASSEMBLY",
                "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
                "GOVERNOR", "US SENATOR"):
        if key in up:
            break
    else:
        return None
    if key in STATE_LEG_RE:
        m = STATE_LEG_RE[key][0].search(up)
        if not m:
            raise ValueError(f"no district number in title: {title!r}")
        return key, m.group(1)
    if key == "UNITED STATES REPRESENTATIVE IN CONGRESS":
        m = re.search(r"(\d+)TH CONGRESSIONAL", up)
        return key, (m.group(1) if m else "")
    return key, ""


def nearest(items, value):
    return min(items, key=lambda t: abs(t - value))


class Page:
    """One parsed contest page."""

    def __init__(self, page, page_no, path):
        self.page_no = page_no
        self.path = path
        words = page.extract_words()
        self.upright = [w for w in words if w.get("upright", True)]
        self.rotated = [w for w in words if not w.get("upright", True)]

        title_words = [w["text"] for w in sorted(
            (w for w in self.upright if w["top"] < 60), key=lambda w: w["x0"])]
        self.title = " ".join(title_words)

        col_labels = [w for w in self.rotated if 140 <= w["top"] < 160]
        self.machines = sorted(w["x0"] for w in col_labels
                               if w["text"][::-1] == "Machine")
        self.hands = sorted(w["x0"] for w in col_labels
                            if w["text"][::-1] == "Hand")
        self.totals = sorted(w["x0"] for w in col_labels
                             if w["text"][::-1] == "Total")
        if not self.machines or not (len(self.machines) == len(self.hands)
                                     == len(self.totals)):
            raise ValueError(
                f"{path} page {page_no}: inconsistent column labels "
                f"({len(self.machines)} Machine / {len(self.hands)} Hand / "
                f"{len(self.totals)} Total)")
        self.n_groups = len(self.machines)
        self.anchors = []
        for m, h, t in zip(self.machines, self.hands, self.totals):
            self.anchors += [("M", m), ("H", h), ("T", t)]
        self.groups = {}  # group index -> joined rotated header text

    def col_index(self, word):
        center = (word["x0"] + word["x1"]) / 2
        kind, ax = min(self.anchors, key=lambda t: abs(center - t[1]))
        if abs(center - ax) > 12:
            raise ValueError(
                f"{self.path} page {self.page_no} word {word['text']!r} at "
                f"x={center:.1f}: no column anchor")
        return self.anchors.index((kind, ax)), kind

    def header_groups(self):
        for w in self.rotated:
            if w["top"] >= 140:
                continue
            gi = min(range(self.n_groups),
                     key=lambda i: abs(self.machines[i] - w["x0"]))
            if abs(self.machines[gi] - w["x0"]) > 50:
                raise ValueError(
                    f"{self.path} page {self.page_no} header word "
                    f"{w['text']!r} at x={w['x0']:.1f}: no column group")
            text = w["text"][::-1]
            self.groups[gi] = self.groups.get(gi, "")
            self.groups[gi] = (text if not self.groups[gi]
                               else self.groups[gi] + " " + text)
        return self.groups

    def rows(self, min_top=180):
        buckets = []
        for w in sorted((w for w in self.upright if w["top"] >= min_top),
                        key=lambda w: w["top"]):
            if not buckets or abs(w["top"] - buckets[-1]) >= 4:
                buckets.append(w["top"])
        rows_by_top = {}
        for w in self.upright:
            if w["top"] < min_top:
                continue
            rows_by_top.setdefault(nearest(buckets, w["top"]), []).append(w)
        out = []
        for top in sorted(rows_by_top):
            ws = sorted(rows_by_top[top], key=lambda w: w["x0"])
            joined = " ".join(w["text"] for w in ws)
            if re.fullmatch(r"PAGE\s+\d+", joined.strip()):
                continue
            label_words = [w["text"] for w in ws if w["x0"] < 155]
            nums = [w for w in ws if w["x0"] >= 155
                    and re.fullmatch(r"[\d,]+", w["text"])]
            label = " ".join(label_words).strip()
            if not nums:
                raise ValueError(
                    f"{self.path} page {self.page_no}: row {label!r} at "
                    f"top={top:.0f} has no numeric cells ({joined[:80]!r})")
            lab_low = re.sub(r"\s+", " ", label.lower())
            if re.match(r"^(page totals|totals from page|grand total)", lab_low):
                cells = {}  # (group index, 0=M 1=H 2=T) -> value
                for w in nums:
                    idx, kind = self.col_index(w)
                    gi, subcol = divmod(idx, 3)
                    if subcol != ("M", "H", "T").index(kind):
                        raise ValueError(
                            f"{self.path} page {self.page_no} totals row "
                            f"{label!r}: value for {kind} not in its column")
                    if gi not in self.groups:
                        continue  # zero cells of unused groups; ignore
                    if (gi, subcol) in cells:
                        raise ValueError(
                            f"{self.path} page {self.page_no} totals "
                            f"{label!r}: two values for column group {gi} "
                            f"subcol {subcol}")
                    cells[(gi, subcol)] = int(w["text"].replace(",", ""))
                out.append(("totals", lab_low, cells))
            else:
                if not label_words:
                    raise ValueError(
                        f"{self.path} page {self.page_no}: unlabeled data row at "
                        f"top={top:.0f}")
                cells = {}
                for w in nums:
                    idx, _kind = self.col_index(w)
                    if idx in cells:
                        raise ValueError(
                            f"{self.path} page {self.page_no} row {label!r}: "
                            f"two values in cell {idx}")
                    cells[idx] = int(w["text"].replace(",", ""))
                out.append(("data", label, cells))
        return out


class Contest:
    """A contest spread: 1-2 consecutive identically-titled pages."""

    def __init__(self, path, party, pages, title_key, district):
        self.path = path
        self.party = party
        self.pages = pages          # list[Page] in order
        self.title_key = title_key
        self.district = district
        try:
            self.office, self.cands = CONTESTS[(title_key, district)][party]
        except KeyError:
            raise ValueError(
                f"{path}: no {party} contest defined for title {title_key!r} "
                f"district {district!r}")

    def parse(self):
        self.corrections = []
        # candidate-to-group mapping, validated against the union of header
        # text across the contest's pages (single-page OCR misses tolerated)
        used = sorted({gi for page in self.pages for gi in page.groups})
        if len(used) != len(self.cands):
            raise ValueError(
                f"{self.path} {self.office} {self.district}: header column "
                f"groups {used} do not match {len(self.cands)} candidates")
        for (name, tokens), gi in zip(self.cands, used):
            texts = [page.groups[gi].upper() for page in self.pages
                     if gi in page.groups]
            joined = " ".join(texts)
            if not any(t in joined for t in tokens):
                raise ValueError(
                    f"{self.path} {self.office} {self.district}: candidate "
                    f"{name!r} tokens {tokens} not found in column {gi} "
                    f"headers {texts!r}")
        self.used = used

        rows = []        # (page_no, precinct, [per candidate (m, h, t)])
        totals = []      # (page_no, label, {group index: value})
        scat_row = {}    # SCATTERED row: group idx -> scattered-col Total value
        for page in self.pages:
            for kind, label, cells in page.rows():
                if kind == "totals":
                    totals.append((page.page_no, label, cells))
                    continue
                if label.upper().startswith("SCAT"):
                    # a trailing "SCATTERED" row: county-wide write-ins that
                    # the primary computing papers do not attribute to
                    # precincts (all per-precinct Scattered columns are 0).
                    # Verified against the printed Grand Total, not written.
                    for gi in self.used:
                        scat_row[gi] = cells.get(gi * 3 + 2, 0)
                    continue
                key = PRECNORM.get(re.sub(r"[^A-Z0-9]", "", label.upper()))
                if key is None:
                    norm = re.sub(r"[^A-Z0-9]", "", label.upper())
                    # OCR garbles confirmed present in these scans (the
                    # corrected label is validated by the precinct-coverage
                    # check below)
                    key = CORRECTIONS.get(norm)
                    if key is None:
                        # otherwise accept a unique close match and document it
                        close = difflib.get_close_matches(norm, PRECNORM, 2, 0.8)
                        if len(close) == 1:
                            key = PRECNORM[close[0]]
                    if key is None:
                        raise ValueError(
                            f"{self.path} page {page.page_no}: unknown precinct "
                            f"label {label!r}")
                    self.corrections.append(
                        f"{self.path} page {page.page_no}: OCR precinct "
                        f"{label!r} -> {key!r}")
                per_cand = []
                for gi in used:
                    m = cells.get(gi * 3 + 0, 0)
                    h = cells.get(gi * 3 + 1, 0)
                    t = cells.get(gi * 3 + 2, 0)
                    if m + h != t:
                        raise ValueError(
                            f"{self.path} page {page.page_no} precinct "
                            f"{label!r} candidate "
                            f"{self.cands[used.index(gi)][0]!r}: machine {m} + "
                            f"hand {h} != total {t}")
                    per_cand.append((m, h, t))
                rows.append((page.page_no, key, per_cand))

        # each precinct exactly once per contest
        precincts = [p for _pg, p, _c in rows]
        if len(set(precincts)) != len(precincts):
            dupes = sorted({p for p in precincts if precincts.count(p) > 1})
            raise ValueError(
                f"{self.path} {self.office} {self.district}: repeated precincts "
                f"{dupes}")
        self.rows = rows

        # --- verification against the printed totals rows -------------------
        first_page_no = self.pages[0].page_no
        last_page_no = self.pages[-1].page_no

        def sum_over(subset):
            sel = [r for r in rows if subset is None or r[0] == subset]
            return OrderedDict(
                (ci, (sum(r[2][ci][0] for r in sel),
                      sum(r[2][ci][1] for r in sel),
                      sum(r[2][ci][2] for r in sel)))
                for ci in range(len(self.cands)))

        sums = sum_over(None)
        for pg, label, cells in totals:
            if label.startswith("grand total"):
                target, scope = sums, "all"
            elif label.startswith("totals from page"):
                target, scope = sum_over(first_page_no), "page1"
            elif label.startswith("page totals"):
                if len(self.pages) > 1 and pg == last_page_no:
                    target, scope = sum_over(last_page_no), "page2"
                elif len(self.pages) > 1:
                    target, scope = sum_over(first_page_no), "page1"
                else:
                    target, scope = sums, "all"
            else:
                raise ValueError(
                    f"{self.path} {self.office} {self.district}: unrecognized "
                    f"totals row {label!r}")
            for gi in {g for g, _s in cells}:
                if gi not in self.used:
                    continue  # zero cells of unused trailing groups
                ci = self.used.index(gi)
                m, h, t = target[ci]
                pm = cells.get((gi, 0))
                ph = cells.get((gi, 1))
                pt = cells.get((gi, 2))
                if self.cands[ci][0] == WI:
                    # Scattered column: the county-wide SCATTERED row sits at
                    # the end of the LAST page and is counted on top of the
                    # (zero) per-precinct cells; it is only included in the
                    # Grand Total (and, for single-page spreads, Page Totals),
                    # so intermediate totals rows are not scattered-checked.
                    if label.startswith("grand total") or len(self.pages) == 1:
                        required = t + scat_row.get(gi, 0)
                        if pt is not None and pt != required:
                            raise ValueError(
                                f"{self.path} {self.office} {self.district}: "
                                f"printed {label!r} scattered total {pt} but "
                                f"precinct rows {t} + SCATTERED row "
                                f"{scat_row.get(gi, 0)} = {required}")
                    continue
                if pt is None:
                    raise ValueError(
                        f"{self.path} {self.office} {self.district}: printed "
                        f"{label!r} has no Total for candidate "
                        f"{self.cands[ci][0]!r}")
                if pm is not None and pm != m or ph is not None and ph != h \
                        or pt is not None and pt != t:
                    raise ValueError(
                        f"{self.path} {self.office} {self.district}: printed "
                        f"{label!r} (M={pm},H={ph},T={pt}) for candidate "
                        f"{self.cands[ci][0]!r} but {scope} rows sum to "
                        f"(M={m},H={h},T={t})")
        self.scat_row = scat_row

        # each contest must cover its expected precinct set
        expected = LEG_PRECINCTS.get((self.office, self.district), set(PRECINCTS))
        if set(precincts) != expected:
            missing = sorted(expected - set(precincts))
            extra = sorted(set(precincts) - expected)
            raise ValueError(
                f"{self.path} {self.office} {self.district}: precinct coverage "
                f"mismatch missing={missing} extra={extra}")
        return sums


def read_file(path, party):
    """Group a PDF's pages into contest spreads and parse each."""
    parsed = []  # (page_no, Page)
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages):
            try:
                pg = Page(page, page_no, path)
                pg.header_groups()
            except ValueError:
                continue
            parsed.append((page_no, pg))
    contests = OrderedDict()
    pending = None
    for k, (page_no, pg) in enumerate(parsed):
        resolved = resolve_title(pg.title)
        if resolved is None:
            if pg.groups:
                raise ValueError(
                    f"{path} page {page_no}: header words {pg.groups} but no "
                    f"office title ({pg.title!r})")
            continue
        key, district = resolved
        if (key, district) not in CONTESTS or party not in CONTESTS[(key, district)]:
            raise ValueError(
                f"{path} page {page_no}: title {key!r} district {district!r} "
                f"not expected on the {party} ballot")
        if pending and pending.title_key != key:
            raise ValueError(
                f"{path} page {page_no}: unexpected title {pg.title!r} after "
                f"unterminated contest {pending.title_key}")
        if pending and pending.district != district:
            raise ValueError(
                f"{path} page {page_no}: district changed under open contest "
                f"{pending.title_key}: {pending.district!r} -> {district!r}")
        if pending is None:
            pending = Contest(path, party, [pg], key, district)
        else:
            pending.pages.append(pg)
        # does anything follow for the same contest?
        more = False
        if k + 1 < len(parsed) and parsed[k + 1][0] == page_no + 1:
            nres = resolve_title(parsed[k + 1][1].title)
            if nres is not None and nres == resolved:
                more = True
        if not more:
            contests[(key, district)] = pending
            pending = None
    if pending is not None:
        raise ValueError(f"{path}: unterminated contest {pending.title_key}")
    return contests


def main(argv=None):
    # manual arg handling (nargs='+' positional + output positional is ambiguous
    # in argparse): all arguments except the last are source PDFs.
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 3:
        raise SystemExit(__doc__)
    *sources, output = args

    dem_pdf = next((p for p in sources if re.search(r"dem", p, re.I)), None)
    recount_pdf = next((p for p in sources if re.search(r"recount", p, re.I)), None)
    rep_pdf = next((p for p in sources
                    if p not in (dem_pdf, recount_pdf) and re.search(r"rep", p, re.I)),
                   None)
    if dem_pdf is None or rep_pdf is None or (recount_pdf and "recount" != "recount"):
        raise SystemExit(
            "inputs must be the Dem Races, Rep Races and optionally the US "
            f"Senator Recount PDFs plus the output path; got {sources}")
    unknown = [p for p in sources if p not in (dem_pdf, rep_pdf, recount_pdf)]
    if unknown:
        raise SystemExit(f"unrecognized input(s): {unknown}")

    out_rows = []
    notes = []

    def load(path, party):
        contests = read_file(path, party)
        for data in contests.values():
            data.sums = data.parse()
        return contests

    dem = load(dem_pdf, "DEM")
    rep = load(rep_pdf, "REP")
    recount = load(recount_pdf, "REP") if recount_pdf else None

    # --- GOP U.S. Senate: use the recount when supplied ----------------------
    if recount:
        main_sen = rep.pop(("US SENATOR", ""))
        rec_sen = recount[("US SENATOR", "")]
        if len(main_sen.cands) != len(rec_sen.cands):
            raise ValueError("recount / main file candidate counts differ")
        notes.append("GOP U.S. Senate rows: from the recount file "
                     "(OFFICIAL COMPUTATIONS OF RECOUNTED VOTES)")
        notes.append("pre-recount vs recount GOP U.S. Senate county totals: " +
                     "; ".join(
                         f"{n}: {main_sen.sums[i][2]} -> {rec_sen.sums[i][2]}"
                         + ("" if main_sen.sums[i][2] == rec_sen.sums[i][2]
                            else f" (delta {rec_sen.sums[i][2]-main_sen.sums[i][2]:+d})")
                         for i, (n, _t) in enumerate(main_sen.cands)))
        rep[("US SENATOR", "")] = rec_sen

    n_contests = 0
    for source in (dem, rep):
        for data in source.values():
            n_contests += 1
            label = f"{data.office} {data.district}".strip()
            for _pg, precinct, per_cand in data.rows:
                for ci, (m, h, t) in enumerate(per_cand):
                    name, _tok = data.cands[ci]
                    if name == WI and t == 0:
                        continue  # write-ins are county-wide (SCATTERED row)
                    out_rows.append({
                        "county": COUNTY, "precinct": precinct,
                        "office": data.office, "district": data.district,
                        "party": data.party, "candidate": name, "votes": t,
                        "election_day": m if m else "",
                        "mail": "",
                        "provisional": h if h else "",
                    })
            summary = ", ".join(
                f"{n}={data.sums[i][2]}" for i, (n, _t) in enumerate(data.cands)
                if n != WI)
            scat = data.scat_row.get(data.used[-1], 0)
            if scat:
                summary += f"; SCATTERED row (county-wide write-ins, not output)={scat}"
            for corr in data.corrections:
                print("  NOTE:", corr, file=sys.stderr)
            print(f"  {data.party} {label} ({len(data.rows)} precincts): "
                  f"{summary}", file=sys.stderr)

    with open(output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(out_rows)

    print(f"wrote {len(out_rows)} rows, {n_contests} contests, "
          f"{len({r['precinct'] for r in out_rows})} precincts -> {output}",
          file=sys.stderr)
    for n in notes:
        print("  NOTE:", n, file=sys.stderr)


if __name__ == "__main__":
    main()