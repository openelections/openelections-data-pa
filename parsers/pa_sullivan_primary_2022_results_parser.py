#!/usr/bin/env python3
"""Sullivan County, PA 2022 Primary (May 17, 2022) — precinct-level results parser.

Sources (both an "OFFICIAL Election Results" crosstab, same layout family as
Sullivan's 2022 general PDF — rotated 90-degree column headers: 15 precinct
columns + a horizontal TOTALS column, read via non-upright char bands, with
the one-cell-per-precinct numbers right-aligned on the same visual row as
the candidate name):

  * "Sullivan PA 2022 Primary Official Results Democratic.pdf" — Democratic
    contests: U.S. Senate, Governor, Lieutenant Governor, U.S. House 9th,
    State House 84th, Member of the Democratic State Committee, then one
    "MEMBER OF THE DEMOCRATIC COUNTY COMMITTEE - FEMALE/MALE - 4 yr term"
    block per precinct (pages 1-3; a trailing empty template grid of 0s on
    page 3 is ignored).
  * "Sullivan PA 2022 Primary Official Results Republican Recount.pdf" —
    OFFICIAL results for all Republican contests AFTER the June recount of
    the GOP U.S. Senate race (Oz vs McCormick).  Its TOTALS match the ENR
    post-recount anchor exactly (McCormick 439, Oz 400, Barnette 279, Sands
    76, ...), so it is the sole source for all Republican rows.

Layout/format notes:
  * Candidate names carry a "(X County)" residency qualifier, stripped.
  * Write-in rows are itemized by name ("Write-In <Name>", typos included);
    they are aggregated into one "Write Ins" row per contest/precinct and
    carry the contest's party (party-empty write-ins fail duplicate_entries
    in primaries).
  * No Registered Voters / Ballots Cast rows; no election-day/mail/
    provisional breakdown -> 7-column output.
  * Member of Democratic/Republican State Committee is named per the ENR
    anchor convention.  County Committee blocks are emitted as
    "Democratic County Committeeman/Committeewoman - <precinct>".

Usage: python parsers/pa_sullivan_primary_2022_results_parser.py <dem.pdf> <rep.pdf> <out.csv>
"""
from __future__ import annotations

import csv
import re
import sys

import pdfplumber

COUNTY = "Sullivan"

NUM_RE = re.compile(r"^\d{1,3}(,\d{3})*$|^\d{4}$")

FRONT_MATTER = {"OFFICIAL Election Results", "Primary Election"}
PARTY_BANNERS = {"REPUBLICAN", "DEMOCRATIC"}

HEADER_PREFIXES = {
    "REPRESENTATIVE IN",
    "REPRESENTATIVE IN CONGRESS",
    "REPRESENTATIVE IN THE GENERAL",
    "MEMBER OF THE",
    "MEMBER OF THE DEMOCRATIC STATE",
    "MEMBER OF THE REPUBLICAN STATE",
    "MEMBER OF THE DEMOCRATIC COUNTY",
    "MEMBER OF THE REPUBLICAN COUNTY",
}

# contest header (normalized upper) -> (office, district)
CONTEST_HEADERS = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE":
        ("Member of Democratic State Committee", ""),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", ""),
}

PRECINCT_MAP = {  # rotated-header band name -> output precinct name
    "Dushore Boro": "Dushore Borough",
    "Lopez Pct": "Lopez Precinct",
    "Laporte Boro": "Laporte Borough",
    "Forksville Boro": "Forksville Borough",
    "Bernice Pct": "Bernice Precinct",
    "Eagles Mere Boro": "Eagles Mere Borough",
    "Eagles Mere Bor": "Eagles Mere Borough",
    "Shrewsbury Twp": "Shrewsbury Township",
    "Laporte Twp": "Laporte Township",
    "Hillsgrove Twp": "Hillsgrove Township",
    "Colley Twp": "Colley Township",
    "Forks Twp": "Forks Township",
    "Davidson Twp": "Davidson Township",
    "Elkland Twp": "Elkland Township",
    "Cherry Twp": "Cherry Township",
    "Fox Twp": "Fox Township",
}

SECTION_RE = re.compile(r"^([A-Z ]+?)\s+(BORO|TOWNSHIP|PRECINCT)$")
GENDER_RE = re.compile(r"^COMMITTEE\s*-\s*(FE)?MALE\s*-\s?YR\s+TERM$")
WRITE_IN_RE = re.compile(r"^write\s?-\s?in", re.I)
RESIDENCY_RE = re.compile(r"\s*\([A-Za-z' ]+ county\)\s*$", re.I)


def title_case(text: str) -> str:
    """ALL CAPS -> Title Case, handling initials, hyphens, apostrophes, Mc/O'."""
    def cap_word(w: str) -> str:
        lw = w.lower()
        if lw.startswith("mc") and len(lw) > 2 and lw[2].isalpha():
            return "Mc" + lw[2].upper() + lw[3:]
        if lw.startswith("o'") and len(lw) > 2:
            return "O'" + lw[2].upper() + lw[3:]
        out = []
        cap = True
        for ch in w:
            if ch.isalpha():
                out.append(ch.upper() if cap else ch.lower())
                cap = False
            else:
                out.append(ch.lower())
                if ch in ".-'":
                    cap = True
        return "".join(out)
    return " ".join(cap_word(w) for w in text.split())


# the source (and the general 2022 file) prints "Delrosso"; the household's
# capital R spelling used in the repo is "DelRosso"
NAME_FIX = {
    "Carrie Lewis Delrosso": "Carrie Lewis DelRosso",
}

SECTION_SUFFIX = {"BORO": "Borough", "TOWNSHIP": "Township", "PRECINCT": "Precinct"}


def build_precincts(page) -> list[str]:
    """Output precinct names (15) from a page's rotated header bands."""
    bands: dict[int, list] = {}
    for c in page.chars:
        if not c["upright"]:
            bands.setdefault(round(c["x0"]), []).append(c)
    names = []
    for x in sorted(bands):
        cs = sorted(bands[x], key=lambda c: -c["top"])  # rotated: read top-down
        words: list[str] = []
        cur = cs[0]["text"]
        prev_top = cs[0]["top"]
        for c in cs[1:]:
            if prev_top - c["top"] > 8:
                words.append(cur)
                cur = c["text"]
            else:
                cur += c["text"]
            prev_top = c["top"]
        words.append(cur)
        names.append(" ".join(words))
    if len(names) != 16 or names[-1].replace(" ", "").lower() != "totals":
        raise ValueError(f"unexpected header columns: {names}")
    out = []
    for n in names[:15]:
        n = " ".join(n.split())
        if n not in PRECINCT_MAP:
            raise ValueError(f"unmapped precinct header {n!r}")
        out.append(PRECINCT_MAP[n])
    return out


def body_lines(page, y_min=40.0):
    """Upright body words grouped into visual rows (top within 3pt).

    y_min is low because the page break drops real data rows right below the
    running page head (recount file: the Lt Gov rows at y~55 on page 2); the
    actual title/banner front matter is skipped by label matching instead,
    and the rotated column headers are filtered out via the upright flag."""
    ws = [w for w in page.extract_words()
          if w["top"] >= y_min and w["upright"]]
    ws.sort(key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in ws:
        if lines and abs(w["top"] - lines[-1][-1]["top"]) < 3:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
        yield ln


def norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", label).strip()


def parse_party_pdf(pdf_path: str, party: str, precincts: list[str],
                    problems: list[str]) -> list[list]:
    """Parse one party's crosstab PDF into standardized rows."""
    rows: list[list] = []
    pdf = pdfplumber.open(pdf_path)

    # numeric-column centers, built ONLY from full-width data rows so the
    # digit "4" in "4 yr term" headers cannot pollute the clustering
    xs: list[float] = []
    for page in pdf.pages:
        for ln in body_lines(page):
            cells = [w for w in ln if NUM_RE.match(w["text"])
                     and w["x0"] > 200]
            if len(cells) >= 12:
                xs.extend(w["x1"] for w in cells)
    xs.sort()
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < 3:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centers = [sorted(c)[len(c) // 2] for c in clusters]
    if len(centers) != 16:
        raise ValueError(f"expected 16 numeric columns in {party} file, "
                         f"got {len(centers)}: {centers}")

    def col_of(w) -> int:
        c = min(range(16), key=lambda i: abs(w["x1"] - centers[i]))
        if abs(w["x1"] - centers[c]) > 8:
            problems.append(f"{party}: number {w['text']!r} at x1 "
                            f"{w['x1']:.0f} not near a column center")
        return c

    cur_office: str | None = None
    cur_district = ""
    committee_section = ""          # "Cherry Township" or "" outside blocks
    committee_gender = ""           # "Committeeman"/"Committeewoman"
    pending: list[str] = []         # header fragments awaiting continuation
    wins_agg: dict[tuple, dict[int, int]] = {}   # (office, district) -> {col: votes}

    def emit_candidate(label: str, cells: dict[int, int]):
        name = RESIDENCY_RE.sub("", label, count=1).strip()
        name = NAME_FIX.get(name, name)
        total = cells.get(15)
        s = sum(v for k, v in cells.items() if k < 15)
        if total is not None and total != s:
            problems.append(f"{party} {cur_office or committee_office()}: {name} "
                            f"precinct sum {s} != TOTALS {total}")
        for i in range(15):
            if i in cells:  # emit every cell the source prints, incl. 0s
                rows.append([COUNTY, precincts[i], cur_office or committee_office(),
                             cur_district, party, name, str(cells[i])])

    def committee_office() -> str:
        return f"Democratic County {committee_gender or '?'} - {committee_section or '?'}"

    def cur_key() -> tuple:
        return (cur_office or committee_office(), cur_district)

    def flush_wins(key: tuple) -> None:
        agg = wins_agg.pop(key, None)
        if not agg:
            return
        for i in range(15):
            if agg.get(i):
                rows.append([COUNTY, precincts[i], key[0], key[1], party,
                             "Write Ins", str(agg[i])])

    def set_contest(new_office: str, new_district: str) -> None:
        nonlocal cur_office, cur_district, committee_section, committee_gender
        flush_wins(cur_key())
        cur_office, cur_district = new_office, new_district
        committee_section, committee_gender = "", ""
        pending.clear()

    for page in pdf.pages:
        for ln in body_lines(page):
            label_parts = [w for w in ln if not NUM_RE.match(w["text"])]
            label = norm_label(" ".join(w["text"] for w in label_parts))
            upper = label.upper()
            compact = norm_label(upper)
            num_ws = [w for w in ln if NUM_RE.match(w["text"])]
            cells: dict[int, int] = {}

            # ---- skip front matter, party banners, date lines ------------
            if label in FRONT_MATTER or compact in PARTY_BANNERS \
                    or re.match(r"^May\s", label):
                continue

            # ---- committee section continuations ("COMMITTEE - FEMALE - 4 yr term")
            m = GENDER_RE.match(compact)
            if m:
                # emit the previous gender's block before the key changes
                flush_wins(cur_key())
                committee_gender = "Committeewoman" if m.group(1) else "Committeeman"
                cur_office = None
                pending.clear()
                continue

            # cells (incl. their column assignment) only now that header lines
            # are ruled out — the digit "4" of "4 yr term" must not be mapped
            def map_cells():
                for w in num_ws:
                    c = col_of(w)
                    cells[c] = cells.get(c, 0) + int(w["text"].replace(",", ""))
            if num_ws:
                map_cells()

            # ---- contest headers (possibly wrapped onto 2 lines) ---------
            joined = norm_label(" ".join(pending + [label]))
            jupper = joined.upper()
            head = CONTEST_HEADERS.get(jupper)
            if head is None:
                m = re.match(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)"
                             r"(?:TH|ST|ND|RD)?\s+DISTRICT$", jupper)
                if m:
                    head = ("U.S. House", m.group(1))
            if head is None:
                m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s*-\s*"
                             r"(\d+)(?:TH|ST|ND|RD)?\s+DISTRICT$", jupper)
                if m:
                    head = ("State House", m.group(1))
            if head is not None:
                set_contest(*head)
                continue
            if not num_ws and (jupper in HEADER_PREFIXES or pending):
                pending.append(label)
                jjoin = norm_label(" ".join(pending)).upper()
                if jjoin not in HEADER_PREFIXES and jjoin not in CONTEST_HEADERS:
                    problems.append(f"{party}: unrecognized header/label "
                                    f"{jjoin!r}")
                    pending.clear()
                continue

            # ---- county-committee section header --------------------------
            sm = SECTION_RE.match(upper)
            if sm:
                section = (title_case(sm.group(1)) + " "
                           + SECTION_SUFFIX[sm.group(2)])
                if section not in precincts:
                    problems.append(f"{party}: section {section!r} is not a "
                                    f"known precinct")
                flush_wins(cur_key())
                committee_section, committee_gender = section, ""
                cur_office = None
                cur_district = ""
                pending.clear()
                continue

            # ---- numbers-only line ---------------------------------------
            if num_ws and not label:
                if any(cells.values()):
                    problems.append(f"{party}: unexplained numbers-only line "
                                    f"{cells!r}")
                continue
            if not num_ws:
                if label:
                    problems.append(f"{party}: unrecognized text-only line "
                                    f"{label!r}")
                continue

            # ---- data rows (label + numbers) ------------------------------
            if not cur_office and not committee_section:
                problems.append(f"{party}: row before any contest: {label!r}")
                continue
            if WRITE_IN_RE.match(label):
                rest = re.sub(r"^write\s?-\s?in\s*[-–]?\s*", "", label,
                              flags=re.I)
                rest = RESIDENCY_RE.sub("", rest, count=1).strip()
                bycol = {k: v for k, v in cells.items()}
                total = cells.get(15)
                if total is not None:
                    s = sum(v for k, v in cells.items() if k < 15)
                    if total != s:
                        problems.append(f"{party} {cur_key()[0]}: write-in "
                                        f"{rest or '<blank precinct-sum check>'} "
                                        f"precinct sum {s} != TOTALS {total}")
                key = cur_key()
                agg = wins_agg.setdefault(key, {})
                for k, v in bycol.items():
                    agg[k] = agg.get(k, 0) + v
                continue
            emit_candidate(label, cells)

    flush_wins(cur_key())
    return rows


def parse(dem_pdf: str, rep_pdf: str):
    problems: list[str] = []
    probe = pdfplumber.open(dem_pdf)
    precincts = build_precincts(probe.pages[0])
    dem_rows = parse_party_pdf(dem_pdf, "DEM", precincts, problems)
    rep_rows = parse_party_pdf(rep_pdf, "REP", precincts, problems)
    return dem_rows + rep_rows, problems


def main() -> None:
    if len(sys.argv) != 4:
        sys.exit("usage: pa_sullivan_primary_2022_results_parser.py "
                 "<dem.pdf> <rep.pdf> <out.csv>")
    dem_pdf, rep_pdf, out = sys.argv[1:4]
    rows, problems = parse(dem_pdf, rep_pdf)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")
    if problems:
        sys.stderr.write("SULLIVAN PROBLEMS:\n" + "\n".join(problems) + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()