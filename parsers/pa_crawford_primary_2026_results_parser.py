#!/usr/bin/env python3
"""Parser for Crawford County 2026 General Primary (May 19, 2026) results.

Sources ("final signing" computing papers, Acrobat text layer over the scan,
same computing-paper family as the county's 2022 primary):

  * "Crawford County final-signing-Democratic-Fed-State-and-County.pdf"
  * "Crawford County final-signing-Republican-Fed-State-and-County.pdf"

Each contest is a 1-2 landscape-page spread ("COMPUTING PAPER / OFFICIAL
RETURNS"): precinct rows x Machine/Hand/Total column groups, where each
candidate (and the Scattered / write-in column) is a rotated header block
positioned over its group's Machine/Hand/Total triple, plus printed
"Page Totals" / "Totals from Page 1" / "Grand Total" rows (single-page
spreads: a bare "Page Totals" or "Totals").  The parser works from
pdfplumber word coordinates, the same way as
pa_crawford_primary_2022_results_parser.py:

  * office title   - upright words near the top of the page (the 2026 papers
                     print the district number in the title, e.g.
                     "REPRESENTATIVE IN CONGRESS, 16th District")
  * column labels  - rotated 'Machine'/'Hand'/'Total' whose x0 positions
                     define the 9 numbered column groups per page (unused
                     trailing groups print only a zero Total cell in data
                     rows; totals rows print all three zero cells)
  * candidates     - rotated header words above the label band, assigned to
                     their nearest column group and matched against the
                     expected candidate tokens for that contest
  * data rows      - upright words clustered by 'top'; leading words left of
                     the grid are the precinct name; totals-label rows are
                     used for verification, not output
  * SCATTERED      - unlike the 2022 papers, write-ins are attributed to
                     precincts in 2026 (the per-precinct Scattered columns
                     carry real values; there is no county-wide SCATTERED
                     row), so each precinct's Scattered column is written to
                     the output as a "Write Ins" candidate row carrying the
                     contest's ballot party

Output:

  * precinct CSV  - plain 7-column schema.  Machine/Hand are a ballot-MEDIUM
                    split (mail-in ballots are scanned into the machine
                    count), not an election-day/absentee/provisional split:
                    the 2026 DOS certified anchor for Crawford shows Governor
                    REP election_day 4059 / mail 1013 / provisional 6
                    against a printed Machine 5061 / Hand 17 (same 5078
                    total), so the breakdown columns are left empty, matching
                    the county's 2023-primary computing-paper practice.
  * county CSV    - the printed Grand Total (or single-page "Totals") row per
                    contest, both parties combined; the county-file header
                    convention keeps the breakdown columns with empty cells.

Contest coverage: the papers carry every contest the DOS certified precinct
export carries for Crawford (Governor, Lieutenant Governor, U.S. House 16,
State Senate 50, State House 6/64/65 both parties) plus both State Committee
contests, which the DOS export never carries.  No DOS fallback was needed.

Two printed contest columns are NOT in the DOS certified export / the 2026
statewide county anchor (2026/20260519__pa__primary__county.csv) and are
nevertheless kept here because the county's own paper reports them
precinct by precinct (verified against that paper's totals rows):

  * State Senate 50 DEM: the paper prints a Sue Mulvey column (1090 votes),
    a "Michele Brooks" column (302 votes - write-in attribution: the same
    pattern other counties use for named write-in candidates) and Scattered
    (201); the certified export carries no State Senate 50 DEM candidate for
    Crawford.
  * Governor REP: the paper prints a "Doug Mastriano" column (128 votes).
    Mastriano withdrew from the 2026 GOP gubernatorial primary and his name
    still printed on the ballot (other counties' sources label his totals
    "WRITE-IN"); the certified export carries only Stacy Garrity.  The
    Scattered column (110) sits next to it.

The MEMBER OF STATE REPUBLICAN COMMITTEE pages print no candidate names
("(Vote For Not More Than Three) (1 male, 1 female, 1 either gender)") and
consist of the Scattered column only (534 write-in votes); there are
accordingly no named-candidate rows for that contest in either DOS or this
output.

Usage:
    python parsers/pa_crawford_primary_2026_results_parser.py <dem_pdf> \
        <rep_pdf> <precinct_output_csv> <county_output_csv>

The parser exits nonzero on any validation failure.
"""

from __future__ import annotations

import csv
import difflib
import re
import sys
from collections import OrderedDict

import pdfplumber

COUNTY = "Crawford"
PRECINCT_FIELDS = ["county", "precinct", "office", "district", "party",
                   "candidate", "votes"]
COUNTY_FIELDS = ["county", "office", "district", "party", "candidate", "votes",
                 "election_day", "mail", "provisional"]

WI = "Write Ins"          # the Scattered column (per-precinct in 2026)
SCATTERED_TOKENS = ["SCATTERED"]

# The county's 61 election districts (as printed on the papers; the Titusville
# wards were consolidated to 3 districts and the boards now total 61 EDs).
PRECINCTS = [
    "Athens Twp", "Beaver Twp", "Bloomfield Twp", "Blooming Valley Boro",
    "Cambridge Twp", "Cambridge Springs Boro", "Centerville Boro",
    "Cochranton Boro", "Conneaut Twp", "Conneaut Lake Boro",
    "Conneautville Boro", "Cussewago Twp", "East Fairfield Twp",
    "East Fallowfield Twp", "East Mead Twp", "Fairfield Twp", "Greenwood Twp",
    "Hayfield Twp", "Hydetown Boro", "Linesville Boro", "Meadville 1-1",
    "Meadville 1-2", "Meadville 2-1", "Meadville 2-2", "Meadville 2-3",
    "Meadville 3-1", "North Shenango Twp", "Oil Creek Twp", "Pine Twp",
    "Randolph Twp", "Richmond Twp", "Rockdale Twp", "Rome Twp", "Sadsbury Twp",
    "Saegertown Boro", "South Shenango Twp", "Sparta Twp", "Spartansburg Boro",
    "Spring Twp", "Springboro Boro", "Steuben Twp", "Summerhill Twp",
    "Summit Twp", "Titusville 1", "Titusville 2", "Titusville 3",
    "Townville Boro", "Troy Twp", "Union Twp", "Venango Twp", "Venango Boro",
    "Vernon Twp 1", "Vernon Twp 2", "Vernon Twp 3", "Wayne Twp",
    "West Fallowfield Twp", "West Mead Twp 1", "West Mead Twp 2",
    "West Shenango Twp", "Woodcock Twp", "Woodcock Boro",
]
PRECNORM = {re.sub(r"[^A-Z0-9]", "", p.upper()): p for p in PRECINCTS}

CORRECTIONS: dict[str, str] = {}

# State House district coverage (the papers print one page spread per House
# district; HD6 is the remainder of the county).
HD64 = {"Hydetown Boro", "Oil Creek Twp", "Rome Twp", "Steuben Twp",
        "Titusville 1", "Titusville 2", "Titusville 3", "Townville Boro",
        "Troy Twp"}
HD65 = {"Athens Twp", "Bloomfield Twp", "Blooming Valley Boro", "Cambridge Twp",
        "Cambridge Springs Boro", "Centerville Boro", "Cussewago Twp",
        "Richmond Twp", "Rockdale Twp", "Saegertown Boro", "Sparta Twp",
        "Spartansburg Boro", "Venango Twp", "Venango Boro", "Woodcock Twp",
        "Woodcock Boro"}
LEG_PRECINCTS = {
    ("State House", "6"): set(PRECINCTS) - HD64 - HD65,
    ("State House", "64"): HD64,
    ("State House", "65"): HD65,
}

# Contest definitions: (title key, district) -> per party (office, named
# candidates).  Each candidate is (display name, uppercase tokens to find in
# that column's rotated header).  Every contest also has a Scattered
# write-in column, matched via the WI tokens.
CONTESTS = {
    ("GOVERNOR", ""): {
        "DEM": ("Governor", [("Josh Shapiro", ["SHAPIRO"])]),
        "REP": ("Governor", [("Stacy Garrity", ["GARRITY"]),
                             ("Doug Mastriano", ["MASTRIANO"])]),
    },
    ("LIEUTENANT GOVERNOR", ""): {
        "DEM": ("Lieutenant Governor", [("Austin Davis", ["DAVIS"])]),
        "REP": ("Lieutenant Governor", [("John Ventre", ["VENTRE"]),
                                        ("Jason Richey", ["RICHEY"])]),
    },
    ("REPRESENTATIVE IN CONGRESS", "16"): {
        "DEM": ("U.S. House", [("Justin Wagner", ["WAGNER"])]),
        "REP": ("U.S. House", [("Mike Kelly", ["KELLY"])]),
    },
    ("SENATOR IN THE GENERAL ASSEMBLY", "50"): {
        "DEM": ("State Senate", [("Sue Mulvey", ["MULVEY"]),
                                 ("Michele Brooks", ["BROOKS"])]),
        "REP": ("State Senate", [("Michele Brooks", ["BROOKS"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "6"): {
        "DEM": ("State House", [("Michael Walker", ["WALKER"])]),
        "REP": ("State House", [("Brad Roae", ["ROAE"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "64"): {
        "DEM": ("State House", [("Amanda Shaffer", ["SHAFFER"])]),
        "REP": ("State House", [("R Lee James", ["JAMES"])]),
    },
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY", "65"): {
        "DEM": ("State House", [("Destiny Howard", ["HOWARD"])]),
        "REP": ("State House", [("Kathy Rapp", ["RAPP"])]),
    },
    ("MEMBER OF STATE DEMOCRATIC COMMITTEE", ""): {
        "DEM": ("Member of Democratic State Committee",
                [("Dan Walk", ["WALK,"])]),
    },
    ("MEMBER OF STATE REPUBLICAN COMMITTEE", ""): {
        # the paper prints no candidate names: Scattered write-ins only
        "REP": ("Member of Republican State Committee", []),
    },
}

DISTRICT_RE = re.compile(r"(\d+)(?:TH|ST|ND|RD) DISTRICT")


def resolve_title(title):
    """Return (title_key, district) for a contest page title, or None."""
    up = re.sub(r"\s+", " ", title.upper().replace("EPRESENTATIVE",
                                                  "REPRESENTATIVE"))
    for key in ("MEMBER OF STATE DEMOCRATIC COMMITTEE",
                "MEMBER OF STATE REPUBLICAN COMMITTEE",
                "LIEUTENANT GOVERNOR",
                "REPRESENTATIVE IN CONGRESS",
                "SENATOR IN THE GENERAL ASSEMBLY",
                "REPRESENTATIVE IN THE GENERAL ASSEMBLY",
                "GOVERNOR"):
        if key in up:
            break
    else:
        return None
    if key == "REPRESENTATIVE IN CONGRESS" or key.endswith("GENERAL ASSEMBLY"):
        m = DISTRICT_RE.search(up)
        if not m:
            raise ValueError(f"no district number in title: {title!r}")
        return key, m.group(1)
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

        self.title = " ".join(w["text"] for w in sorted(
            (w for w in self.upright if w["top"] < 70), key=lambda w: w["x0"]))

        self.machines = sorted(w["x0"] for w in self.rotated
                               if w["text"][::-1] == "Machine")
        self.hands = sorted(w["x0"] for w in self.rotated
                            if w["text"][::-1] == "Hand")
        self.totals = sorted(w["x0"] for w in self.rotated
                             if w["text"][::-1] == "Total")
        if not self.machines or not (len(self.machines) == len(self.hands)
                                     == len(self.totals)):
            raise ValueError(
                f"{path} page {page_no}: inconsistent column labels "
                f"({len(self.machines)} Machine / {len(self.hands)} Hand / "
                f"{len(self.totals)} Total)")
        self.n_groups = len(self.machines)
        self.anchors = []          # (kind, x) for every subcolumn
        for m, h, t in zip(self.machines, self.hands, self.totals):
            self.anchors += [("M", m), ("H", h), ("T", t)]
        # left edge of the count grid: numeric words left of this belong to
        # the label/header text ("Titusville 1", a strayed "May 19, 2026"
        # date line), not to a cell
        self.cell_left = min(x for _kind, x in self.anchors) - 12
        self.label_top = min([w["top"] for w in self.rotated
                              if w["text"][::-1] == "Machine"]) - 6
        self.groups = {}           # group index -> joined rotated header text

    def col_index(self, word):
        center = (word["x0"] + word["x1"]) / 2
        kind, ax = min(self.anchors, key=lambda t: abs(center - t[1]))
        if abs(center - ax) > 10:
            raise ValueError(
                f"{self.path} page {self.page_no} word {word['text']!r} at "
                f"x={center:.1f}: no column anchor")
        return self.anchors.index((kind, ax)), kind

    def header_groups(self):
        """Assign rotated candidate-name words to column groups."""
        for w in self.rotated:
            if w["top"] >= self.label_top:
                continue            # the Machine/Hand/Total band itself
            center = (w["x0"] + w["x1"]) / 2
            gi = min(range(self.n_groups),
                     key=lambda i: abs(self.machines[i] - center))
            if abs(self.machines[gi] - center) > 45:
                raise ValueError(
                    f"{self.path} page {self.page_no} header word "
                    f"{w['text'][::-1]!r} at x={center:.1f}: no column group")
            self.groups[gi] = self.groups.get(gi, "")
            text = w["text"][::-1]
            self.groups[gi] = (text if not self.groups[gi]
                               else self.groups[gi] + " " + text)
        return self.groups

    def rows(self, min_top=178):
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
            label = " ".join(w["text"] for w in ws
                             if (w["x0"] + w["x1"]) / 2 < 155).strip()
            lab_low = re.sub(r"\s+", " ", label.lower())
            if re.match(r"^(page totals|totals from page|grand total|totals)\b",
                        lab_low):
                cells = {}      # (group index, 0=M 1=H 2=T) -> value
                # grid cells only: precinct labels ("Titusville 1", "Venango
                # Boro 2"), totals labels ("Totals from Page 1") and stray
                # header digits ("May 19, 2026") sit left of the first
                # Machine column
                for w in (w for w in ws if (w["x0"] + w["x1"]) / 2 >= self.cell_left
                          and re.fullmatch(r"[\d,]+", w["text"])):
                    idx, kind = self.col_index(w)
                    gi, subcol = divmod(idx, 3)
                    if subcol != ("M", "H", "T").index(kind):
                        raise ValueError(
                            f"{self.path} page {self.page_no} totals row "
                            f"{label!r}: value for {kind} not in its column")
                    if (gi, subcol) in cells:
                        raise ValueError(
                            f"{self.path} page {self.page_no} totals "
                            f"{label!r}: two values for column group {gi} "
                            f"subcol {subcol}")
                    cells[(gi, subcol)] = int(w["text"].replace(",", ""))
                out.append(("totals", lab_low, cells))
            else:
                if not label:
                    raise ValueError(
                        f"{self.path} page {self.page_no}: unlabeled data row "
                        f"at top={top:.0f}")
                cells = {}
                # grid cells only: precinct labels ("Titusville 1") and
                # stray header digits sit left of the first Machine column
                for w in (w for w in ws if (w["x0"] + w["x1"]) / 2 >= self.cell_left
                          and re.fullmatch(r"[\d,]+", w["text"])):
                    idx, _kind = self.col_index(w)
                    if idx in cells:
                        raise ValueError(
                            f"{self.path} page {self.page_no} row {label!r}: "
                            f"two values in cell {idx}")
                    cells[idx] = int(w["text"].replace(",", ""))
                if not cells:
                    # page-header text ("On Tuesday, May 19, 2026") can dip
                    # into the row region on the GOP papers; it carries no
                    # grid cells.  A real data row always prints at least
                    # one numeric cell, and skipped rows are caught
                    # downstream by the exact precinct-coverage check.
                    print(f"  {self.path} page {self.page_no}: skipping "
                          f"row {label!r} (no grid cells)", file=sys.stderr)
                    continue
                # a wholly-zero trailing group prints only its zero Total
                # cell; a real column prints its Machine or Hand cell too
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
        # --- bind used column groups to expected candidates by token ---------
        used = sorted({gi for page in self.pages for gi in page.groups})
        expected = self.cands + [(WI, SCATTERED_TOKENS)]
        assignments = {}            # group idx -> candidate display name
        for gi in used:
            texts = [page.groups[gi].upper() for page in self.pages
                     if gi in page.groups]
            joined = " ".join(texts)
            matches = [name for name, tokens in expected
                       if any(t in joined for t in tokens)]
            if len(matches) != 1:
                raise ValueError(
                    f"{self.path} {self.office} {self.district}: column "
                    f"g{gi} headers {texts!r} match {matches} ambiguously")
            if matches[0] in assignments.values():
                raise ValueError(
                    f"{self.path} {self.office} {self.district}: candidate "
                    f"{matches[0]!r} bound twice (g{gi})")
            assignments[gi] = matches[0]
        missing = [name for name, _t in expected
                   if name not in assignments.values()]
        if missing:
            raise ValueError(
                f"{self.path} {self.office} {self.district}: candidates not "
                f"found in column headers: {missing}")
        self.assignments = assignments
        self.used = sorted(assignments)
        self.wi_group = next((gi for gi, name in assignments.items()
                              if name == WI), None)
        if self.wi_group is None:
            raise ValueError(
                f"{self.path} {self.office} {self.district}: no Scattered "
                f"column found")

        # --- data rows -------------------------------------------------------
        rows = []        # (page_no, precinct, {group: (m, h, t)})
        totals = []      # (page_no, label, {(group, subcol): value})
        for page in self.pages:
            for kind, label, cells in page.rows():
                if kind == "totals":
                    totals.append((page.page_no, label, cells))
                    continue
                if label.upper().startswith("SCAT"):
                    raise ValueError(
                        f"{self.path} page {page.page_no}: unexpected "
                        f"county-wide SCATTERED row (2026 write-ins are "
                        f"per-precinct)")
                key = PRECNORM.get(re.sub(r"[^A-Z0-9]", "", label.upper()))
                if key is None:
                    norm = re.sub(r"[^A-Z0-9]", "", label.upper())
                    if norm in CORRECTIONS:
                        key = CORRECTIONS[norm]
                    else:
                        close = difflib.get_close_matches(norm, PRECNORM,
                                                          2, 0.8)
                        if len(close) == 1:
                            key = PRECNORM[close[0]]
                    if key is None:
                        raise ValueError(
                            f"{self.path} page {page.page_no}: unknown "
                            f"precinct label {label!r}")
                    self.append_note(
                        f"{self.path} page {page.page_no}: precinct "
                        f"{label!r} -> {key!r}")
                per_cand = {}
                for gi in self.used:
                    m = cells.get(gi * 3 + 0, 0)
                    h = cells.get(gi * 3 + 1, 0)
                    t = cells.get(gi * 3 + 2, 0)
                    if m + h != t:
                        raise ValueError(
                            f"{self.path} page {page.page_no} precinct "
                            f"{label!r} candidate "
                            f"{assignments[gi]!r}: machine {m} + hand {h} "
                            f"!= total {t}")
                    per_cand[gi] = (m, h, t)
                rows.append((page.page_no, key, per_cand))

        # each precinct exactly once per contest
        precincts = [p for _pg, p, _c in rows]
        if len(set(precincts)) != len(precincts):
            dupes = sorted({p for p in precincts if precincts.count(p) > 1})
            raise ValueError(
                f"{self.path} {self.office} {self.district}: repeated "
                f"precincts {dupes}")
        self.rows = rows

        # --- verification against the printed totals rows -------------------
        first_page_no = self.pages[0].page_no

        def sum_over(subset):
            sel = [r for r in rows if subset is None or r[0] == subset]
            return OrderedDict(
                (gi, (sum(r[2][gi][0] for r in sel),
                      sum(r[2][gi][1] for r in sel),
                      sum(r[2][gi][2] for r in sel)))
                for gi in self.used)

        sums = sum_over(None)
        self.notes = [] if not hasattr(self, "notes") else self.notes
        for pg, label, cells in totals:
            if label.startswith("grand total"):
                target, scope = sums, "all"
            elif label.startswith("totals from page"):
                target, scope = sum_over(first_page_no), "page1"
            elif label.startswith("page totals"):
                target, scope = sum_over(pg), f"page{pg}"
            elif label.startswith("totals"):
                target, scope = sums, "all"
            else:
                raise ValueError(
                    f"{self.path} {self.office} {self.district}: unrecognized "
                    f"totals row {label!r}")
            for gi in self.used:
                m, h, t = target[gi]
                pm, ph, pt = (cells.get((gi, k)) for k in range(3))
                if pm is None and ph is None and pt is None:
                    continue            # trailing zero group not printed here
                if (pm if pm is not None else 0) != m or \
                        (ph if ph is not None else 0) != h or \
                        (pt if pt is not None else 0) != t:
                    raise ValueError(
                        f"{self.path} {self.office} {self.district}: printed "
                        f"{label!r} (M={pm},H={ph},T={pt}) for candidate "
                        f"{assignments[gi]!r} but {scope} rows sum to "
                        f"(M={m},H={h},T={t})")
        self.sums = sums                  # per used group, (m, h, t)

        # countywide totals: Grand Total when printed, else the single-page
        # "Page Totals" / "Totals" row
        grand = None
        for _pg, label, cells in totals:
            if label.startswith("grand total"):
                grand = cells
                break
        if grand is None:
            cand = [cells for _pg, label, cells in totals
                    if label.startswith(("totals", "page totals"))
                    and "from page" not in label]
            if cand:
                grand = cand[-1]
        if grand is None:
            raise ValueError(
                f"{self.path} {self.office} {self.district}: no county-wide "
                f"totals row found")
        self.grand = {gi: grand.get((gi, 2), 0) for gi in self.used}

        # each contest must cover its expected precinct set
        expected_set = LEG_PRECINCTS.get((self.office, self.district),
                                         set(PRECINCTS))
        if set(precincts) != expected_set:
            missing = sorted(expected_set - set(precincts))
            extra = sorted(set(precincts) - expected_set)
            raise ValueError(
                f"{self.path} {self.office} {self.district}: precinct "
                f"coverage mismatch missing={missing} extra={extra}")
        return sums

    def append_note(self, note):
        if not hasattr(self, "notes"):
            self.notes = []
        self.notes.append(note)


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
        if (key, district) not in CONTESTS or \
                party not in CONTESTS[(key, district)]:
            raise ValueError(
                f"{path} page {page_no}: title {key!r} district "
                f"{district!r} not expected on the {party} ballot")
        if pending and (pending.title_key != key or
                        pending.district != district):
            raise ValueError(
                f"{path} page {page_no}: title changed under open contest "
                f"{pending.title_key}: {pending.title_key!r} -> {key!r}")
        if pending is None:
            pending = Contest(path, party, [pg], key, district)
        else:
            pending.pages.append(pg)
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
    # all arguments except the last two are the two source PDFs
    raw = argv if argv is not None else sys.argv[1:]
    if len(raw) < 4:
        raise SystemExit(__doc__)
    *sources, precinct_out, county_out = raw

    dem_pdf = next((p for p in sources if re.search(r"dem", p, re.I)), None)
    rep_pdf = next((p for p in sources
                    if p != dem_pdf and re.search(r"rep", p, re.I)), None)
    if dem_pdf is None or rep_pdf is None or set(sources) != {dem_pdf, rep_pdf}:
        raise SystemExit(
            "inputs must be the Democratic and the Republican final-signing "
            f"computing papers; got {sources}")

    out_rows = []

    def load(path, party):
        contests = read_file(path, party)
        for data in contests.values():
            data.parse()
        return contests

    dem = load(dem_pdf, "DEM")
    rep = load(rep_pdf, "REP")

    # DEM vs REP precinct coverage of a shared contest must agree
    for key in dem:
        if key in rep:
            d = {p for _pg, p, _c in dem[key].rows}
            r = {p for _pg, p, _c in rep[key].rows}
            if d != r:
                raise ValueError(
                    f"{key}: DEM/REP precinct sets differ: "
                    f"missing on REP={sorted(d - r)}, "
                    f"missing on DEM={sorted(r - d)}")

    n_contests = 0
    for source in (dem, rep):
        for data in source.values():
            n_contests += 1
            label = f"{data.office} {data.district}".strip()
            for _pg, precinct, per_cand in data.rows:
                for gi in sorted(per_cand):
                    m, _h, t = per_cand[gi]
                    out_rows.append({
                        "county": COUNTY, "precinct": precinct,
                        "office": data.office, "district": data.district,
                        "party": data.party, "candidate":
                            data.assignments[gi],
                        "votes": t,
                    })
            summary = ", ".join(
                f"{data.assignments[gi]}={data.grand[gi]}" for gi in
                sorted(data.assignments,
                       key=lambda g: (g == data.wi_group,
                                      data.assignments[g] == WI)))
            print(f"  {data.party} {label} ({len(data.rows)} precincts): "
                  f"{summary} [scattered votes are per-precinct Write Ins]",
                  file=sys.stderr)

    # --- countywide summary file from the printed totals rows ---------------
    county_rows = []
    for source in (dem, rep):
        for data in source.values():
            for gi in sorted(data.assignments):
                county_rows.append({
                    "county": COUNTY, "office": data.office,
                    "district": data.district, "party": data.party,
                    "candidate": data.assignments[gi],
                    "votes": data.grand[gi], "election_day": "", "mail": "",
                    "provisional": "",
                })

    with open(precinct_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRECINCT_FIELDS)
        w.writeheader()
        w.writerows(out_rows)
    with open(county_out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COUNTY_FIELDS)
        w.writeheader()
        w.writerows(county_rows)

    print(f"wrote {len(out_rows)} precinct rows, {n_contests} contest "
          f"spreads, {len({r['precinct'] for r in out_rows})} precincts -> "
          f"{precinct_out}", file=sys.stderr)
    print(f"wrote {len(county_rows)} countywide rows -> {county_out}",
          file=sys.stderr)
    return out_rows, county_rows


if __name__ == "__main__":
    main()
