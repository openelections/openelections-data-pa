#!/usr/bin/env python3
"""Union County PA 2022 general primary — Precinct Totals parser.

Input: PaddleOCR-VL (hosted API, work2023p/ocr_api.py) text extract of
"Union PA 2022 Primary Precinct Totals.pdf" (scanned, LANIER MP 4055, 52
pages / 26 precincts x 2 pages).  The source is the county's own
"Precinct Summary Report" book — same vendor family as the 2023 primary
(pa_union_primary_2023_results_parser.py) and the 2022 general
(pa_union_general_2022_results_parser.py), but the 2022 scan has no text
layer, so parsing runs over the OCR output, which renders each page as
HTML tables (<td> cells; several contests may share one <table>).

Usage:
    python parsers/pa_union_primary_2022_results_parser.py <ocr.txt> <output.csv>

Layout notes:
  * Precinct = 2 consecutive pages. Odd pages: US SENATOR, GOVERNOR,
    LIEUTENANT GOVERNOR, each (DEMOCRATIC) then (REPUBLICAN). Even pages:
    REP IN CONGRESS 15TH, GENERAL ASSEMBLY <76/83/85> (the precinct's
    district), DEMOCRATIC / REPUBLICAN STATE COMMITTEE, and the
    precinct's "<precinct> DEM / REP COMMITTEE" committeeperson races —
    which identify the precinct (the OCR loses the page header block on
    most pages, so committee titles are the name source; the page order
    cross-checks against the 2022 general file's 26 precincts).
  * Every contest prints Total Votes.  Write-in rows are emitted as
    "Write Ins" carrying the contest's party (primary convention).
  * Only totals are reported (no ED/mail/provisional columns exist in the
    precinct book) -> 7-column format, plus Registered Voters / Ballots
    Cast rows per precinct (source carries them).

OCR-repair handling (each repair is printed as REPAIR and validated by
candidate-sum == Total Votes):
  * merged candidate cell ("JOSH SHAPIRO\\nWrite-in") whose row carries one
    [votes,%] pair while the following numbers-only row carries the second
    name's [votes,%] -> split into two rows.
  * contest title row lost (OCR printed just "Vote For 1", or left a bare
    "(REPUBLICAN)" row): the block is placed by position in the page's
    fixed contest sequence after consistency checks against the titled
    neighbors.
  * merged votes+pct cell ("4 100.00%") -> first token is the votes.
  * a lost RV/BC line (page 45) is taken from the precinct's other page.
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Union"
FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

PAGE_SPLIT_RE = re.compile(r"===== PAGE (\d+) =====")
TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S)
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
RV_RE = re.compile(r"Registered Voters\s+([\d,]+)\s*-\s*Total Ballots\s+([\d,]+)")
HEADER_RE = re.compile(r"Election Day\s+(.+?)\s+Unofficial")
PCT_OR_NUM_RE = re.compile(r"^[\d,]+(\.\d+)?%?$")
VOTEFOR_TOTAL_RE = re.compile(r"^Vote For\s+(\d+)\s*[/\n ]\s*Total\s*Votes$", re.I)
VOTEFOR_RE = re.compile(r"^Vote For\s+(\d+)$", re.I)
TOTAL_ALONE_RE = re.compile(r"^Total Votes$", re.I)
STANDALONE_PARTY_RE = re.compile(r"^\((DEMOCRATIC|REPUBLICAN)\)$", re.I)
WRITEIN_RE = re.compile(r"^write-?ins?$", re.I)
TITLE_ROW_RE = re.compile(
    r"^(US SENATOR|GOVERNOR|LIEUTENANT GOVERNOR|REP IN CONGRESS|GENERAL ASSEMBLY|"
    r"DEMOCRATIC STATE COMMITTEE|REPUBLICAN STATE COMMITTEE|[A-Z0-9'.\- ]+ COMMITTEE)\b")
PARTY_TAG_RE = re.compile(r"\((DEMOCRATIC|REPUBLICAN)\)", re.I)
COMMITTEE_RE = re.compile(r"^(.+?)\s+(DEM|REP)\s+COMMITTEE$", re.I)

PRECINCT_NAME = {
    "BUFFALO 1": "Buffalo 1", "BUFFALO 2": "Buffalo 2",
    "E BUFFALO 1": "East Buffalo 1", "E BUFFALO 2": "East Buffalo 2",
    "E BUFFALO 3": "East Buffalo 3", "E BUFFALO 4": "East Buffalo 4",
    "W BUFFALO": "West Buffalo",
    "GREGG": "Gregg", "HARTLEY": "Hartley", "HARTLETON": "Hartleton",
    "KELLY 1": "Kelly 1", "KELLY 2": "Kelly 2", "LEWIS": "Lewis",
    "LEWISBURG 1": "Lewisburg 1", "LEWISBURG 2": "Lewisburg 2",
    "LEWISBURG 3": "Lewisburg 3", "LEWISBURG 4": "Lewisburg 4",
    "LIMESTONE": "Limestone", "UNION": "Union",
    "WHITE DEER 1": "White Deer 1", "WHITE DEER 2": "White Deer 2",
    "WHITE DEER 3": "White Deer 3", "WHITE DEER 4": "White Deer 4",
    "MIFFLINBURG E": "Mifflinburg East", "MIFFLINBURG W": "Mifflinburg West",
    "NEW BERLIN": "New Berlin",
    # spelled-out header forms (some page headers OCR cleanly)
    "EAST BUFFALO 1": "East Buffalo 1", "EAST BUFFALO 2": "East Buffalo 2",
    "EAST BUFFALO 3": "East Buffalo 3", "EAST BUFFALO 4": "East Buffalo 4",
    "WEST BUFFALO": "West Buffalo", "MIFFLINBURG EAST": "Mifflinburg East",
    "MIFFLINBURG WEST": "Mifflinburg West", "WHITE DEER 1": "White Deer 1",
}
# expected page-1 contest roles (0..5); the odd-page order is fixed
ODD_ROLES = ["US SENATOR", "US SENATOR", "GOVERNOR", "GOVERNOR",
             "LIEUTENANT GOVERNOR", "LIEUTENANT GOVERNOR"]
ODD_PARTIES = ["DEM", "REP", "DEM", "REP", "DEM", "REP"]
# expected page-2 slots: congress pair, GA pair (district per precinct),
# state committee pair, precinct committee pair
EVEN_ROLES = ["CONGRESS", "CONGRESS", "GA", "GA",
              "STATE COMMITTEE", "STATE COMMITTEE", "PRECINCT COMMITTEE", "PRECINCT COMMITTEE"]
EVEN_PARTIES = ["DEM", "REP", "DEM", "REP", "DEM", "REP", "DEM", "REP"]

# each precinct's General Assembly district (source prints "GENERAL ASSEMBLY
# 76/83/85" per precinct; district membership verified via the 2022 general file)
GA_DISTRICT = {
    "Buffalo 1": "76", "Buffalo 2": "76", "West Buffalo": "76", "Hartley": "76",
    "Kelly 1": "76", "Kelly 2": "76", "Lewis": "76", "Limestone": "76",
    "Hartleton": "76", "Lewisburg 1": "76", "Lewisburg 2": "76",
    "Lewisburg 3": "76", "Lewisburg 4": "76", "Mifflinburg East": "76",
    "Mifflinburg West": "76", "New Berlin": "76",
    "East Buffalo 1": "85", "East Buffalo 2": "85", "East Buffalo 3": "85",
    "East Buffalo 4": "85", "Union": "85",
    "Gregg": "83", "White Deer 1": "83", "White Deer 2": "83",
    "White Deer 3": "83", "White Deer 4": "83",
}

# source typo (OCR or print): "BUFFALO1 REP COMMITTEE" missing the space
SPELL_FIX = {"BUFFALO1": "BUFFALO 1"}

NAME_FIX = {
    "Dave Mccormick": "Dave McCormick",
}


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


def norm_name(name: str) -> str:
    name = " ".join(name.split())
    if WRITEIN_RE.match(name):
        return "Write Ins"
    name = title_case(name)
    name = NAME_FIX.get(name, name)
    if name == "Glenn Gt Thompson":  # source prints "GLENN GT THOMPSON"
        name = "Glenn GT Thompson"
    return name


def clean(txt: str) -> str:
    return re.sub(r"[ \t]+", " ", txt.replace("\\n", "\n")).strip()


def split_page_name(body: str) -> str | None:
    hm = HEADER_RE.search(body)
    return clean(hm.group(1)) if hm else None


def resolve_title(title: str) -> tuple[str, str]:
    """Raw title (party tag stripped) -> (office, district)."""
    t = re.sub(r"\s+", " ", title.strip()).strip().upper()
    t = re.sub(r"US SENATO$", "US SENATOR", t)
    if t == "US SENATOR":
        return "U.S. Senate", ""
    if t == "GOVERNOR":
        return "Governor", ""
    if t == "LIEUTENANT GOVERNOR":
        return "Lieutenant Governor", ""
    m = re.match(r"^REP IN CONGRESS (\d+)(?:ST|ND|RD|TH)?$", t)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^GENERAL ASSEMBLY (\d+)$", t)
    if m:
        return "State House", m.group(1)
    if t == "DEMOCRATIC STATE COMMITTEE":
        return "Member of Democratic State Committee", ""
    if t == "REPUBLICAN STATE COMMITTEE":
        return "Member of Republican State Committee", ""
    m = COMMITTEE_RE.match(t)
    if m:
        base = m.group(1).strip()
        base = SPELL_FIX.get(base, base)
        base = PRECINCT_NAME.get(base) or title_case(base)
        return (f"{base} {'Dem' if m.group(2).upper() == 'DEM' else 'Rep'} Committee"), ""
    raise ValueError(f"unmapped contest title: {title!r}")


def role_office(precinct: str, role: str, party: str) -> tuple[str, str]:
    """(office, district) for an untitled block, from its page slot role."""
    if role == "US SENATOR":
        return "U.S. Senate", ""
    if role == "GOVERNOR":
        return "Governor", ""
    if role == "LIEUTENANT GOVERNOR":
        return "Lieutenant Governor", ""
    if role == "CONGRESS":
        return "U.S. House", "15"
    if role == "GA":
        return "State House", (GA_DISTRICT.get(precinct) or "")
    if role == "STATE COMMITTEE":
        return ("Member of Democratic State Committee" if party == "DEM"
                else "Member of Republican State Committee"), ""
    if role == "PRECINCT COMMITTEE":
        base_raw = None
        for frag, std in PRECINCT_NAME.items():
            if std == precinct:
                base_raw = frag
                break
        if base_raw is None:
            base_raw = precinct.upper()
        return resolve_title(f"{base_raw} {'DEM' if party == 'DEM' else 'REP'} COMMITTEE")
    raise ValueError(f"unknown role {role!r}")


class Block:
    __slots__ = ("title", "party", "total", "rows", "repairs", "office", "district",
                 "vote_for")

    def __init__(self):
        self.title: str | None = None   # raw title (party tag stripped), or None
        self.party: str | None = None   # DEM / REP once known
        self.total: int | None = None   # Total Votes
        self.vote_for: int | None = None
        self.rows: list[tuple[str, int]] = []   # (raw name, votes)
        self.repairs: list[str] = []
        self.office: str | None = None
        self.district: str | None = None


def parse_page(body: str, page_no: int, log: list[str]):
    """Parse one page -> (blocks, header_name | None, (rv, bc) | None)."""
    name = split_page_name(body)
    rvm = RV_RE.search(body)
    rv = (int(rvm.group(1).replace(",", "")), int(rvm.group(2).replace(",", ""))) if rvm else None
    if rv is None:
        log.append(f"page {page_no}: RV/BC line missing")

    rows: list[list[str]] = []
    for tbl in TABLE_RE.findall(body):
        for r in ROW_RE.findall(tbl):
            cells = [clean(c) for c in CELL_RE.findall(r)]
            c0 = cells[0] if cells else ""
            # Party Distribution block rows (often shares a table with contests)
            if c0 == "Party Distribution" or c0 == "Total Ballots":
                continue
            if c0 in ("DEMOCRATIC", "REPUBLICAN") and len(cells) >= 2 and \
                    cells[1] and re.match(r"^[\d,]+$", cells[1]) and \
                    any(c.endswith("%") for c in cells[2:]):
                continue  # party ballot counts (DEM 148 21.99%)
            if not any(cells):
                continue
            rows.append(cells)

    blocks: list[Block] = []
    cur: Block | None = None
    pending_party: str | None = None

    def close():
        nonlocal cur
        if cur is not None:
            blocks.append(cur)
            cur = None

    i = 0
    while i < len(rows):
        row = rows[i]
        cell0 = row[0] if row else ""

        if any("Precinct Summary Report" in c for c in row if c):
            if name is None:
                hm = HEADER_RE.search(" ".join(row))
                if hm:
                    name = clean(hm.group(1))
            i += 1
            continue
        if not any(row):
            i += 1
            continue

        # bare party row (title lost by OCR, or party printed as its own row)
        if STANDALONE_PARTY_RE.match(cell0):
            party = "DEM" if cell0.upper().startswith("(D") else "REP"
            if cur is not None and cur.party is None:
                cur.party = party
            else:
                pending_party = party
            i += 1
            continue

        if TITLE_ROW_RE.match(cell0):
            close()
            cur = Block()
            tt = cell0
            ptm = PARTY_TAG_RE.search(tt)
            if ptm:
                cur.party = "DEM" if ptm.group(1).upper().startswith("DEM") else "REP"
                tt = (tt[:ptm.start()] + tt[ptm.end():])
                tt = re.sub(r"[\s/]+$", "", tt).strip()
            cur.title = clean(tt) or None
            if cur.party is None and pending_party:
                cur.party = pending_party
            pending_party = None
            i += 1
            continue

        m = VOTEFOR_TOTAL_RE.match(cell0)
        if m:
            if cur is not None and cur.total is not None and cur.rows:
                close()
            if cur is None:
                cur = Block()
            if cur.vote_for is None:
                cur.vote_for = int(m.group(1))
            if cur.party is None and pending_party:
                cur.party = pending_party
            pending_party = None
            nums = [c for c in row[1:] if c]
            if nums:
                m2 = re.match(r"^([\d,]+)", nums[0])
                if m2 and cur.total is None:
                    cur.total = int(m2.group(1).replace(",", ""))
            i += 1
            continue

        m = VOTEFOR_RE.match(cell0)
        if m:
            if cur is not None and cur.total is not None and cur.rows:
                close()
            if cur is None:
                cur = Block()
                if pending_party:
                    cur.party = pending_party
                    pending_party = None
            if cur.vote_for is None:
                cur.vote_for = int(m.group(1))
            i += 1
            continue

        m = TOTAL_ALONE_RE.match(cell0)
        if m:
            if cur is None:
                log.append(f"page {page_no}: Total Votes without block: {row}")
                i += 1
                continue
            if cur.total is None:
                nums = [c for c in row[1:] if c]
                m2 = re.match(r"^([\d,]+)", nums[0]) if nums else None
                if m2:
                    cur.total = int(m2.group(1).replace(",", ""))
                else:
                    log.append(f"page {page_no}: unparseable Total Votes: {row}")
            pending_party = None
            i += 1
            continue

        if all((not c) or PCT_OR_NUM_RE.match(c) for c in row):
            log.append(f"page {page_no}: orphan numeric row: {row}")
            i += 1
            continue

        # candidate row
        if cur is None or cur.total is None:
            log.append(f"page {page_no}: candidate row without contest total: {row}")
            i += 1
            continue
        if cur.party is None and pending_party:
            cur.party = pending_party
            pending_party = None
        votes = None
        for c in row[1:]:
            if not c:
                continue
            mv = re.match(r"^([\d,]+)", c)
            if mv:
                votes = int(mv.group(1).replace(",", ""))
                break
        if votes is None:
            log.append(f"page {page_no}: candidate row without votes: {row}")
            i += 1
            continue
        name_raw = cell0
        if "\n" in name_raw:
            parts = [p.strip() for p in name_raw.split("\n") if p.strip()]
            if len(parts) == 2:
                v2 = None
                if i + 1 < len(rows):
                    nxt = rows[i + 1]
                    if all((not c) or PCT_OR_NUM_RE.match(c) for c in nxt):
                        nn = [c for c in nxt if c]
                        if nn:
                            m2 = re.match(r"^([\d,]+)", nn[0])
                            if m2:
                                v2 = int(m2.group(1).replace(",", ""))
                if v2 is not None:
                    cur.rows.append((parts[0], votes))
                    cur.rows.append((parts[1], v2))
                    cur.repairs.append(
                        f"merged candidate cell {parts[0]!r}+{parts[1]!r} -> {votes} / {v2}")
                    i += 2
                    continue
                cur.rows.append((parts[0], votes))
                cur.repairs.append(f"merged candidate cell unresolved: {parts}")
                log.append(f"page {page_no}: merged candidate cell unresolved "
                           f"({parts[1]!r}); kept {parts[0]!r}={votes}")
                i += 1
                continue
            name_raw = name_raw.replace("\n", " ")
        cur.rows.append((name_raw, votes))
        i += 1
    close()
    return blocks, name, rv


def infer_blocks(precinct: str, page_no: int, blocks: list[Block], log: list[str]) -> None:
    """Assign each block its (office, district, party): titled blocks are matched
    by content against the page's expected contest set; untitled blocks are
    filled from the remaining slots in page order."""
    odd = page_no % 2 == 1
    roles, parties = (ODD_ROLES, ODD_PARTIES) if odd else (EVEN_ROLES, EVEN_PARTIES)
    expected: list[tuple[str, str, str]] = []
    for role, party in zip(roles, parties):
        expected.append((*role_office(precinct, role, party), party))
    if odd and len(blocks) != 6:
        raise SystemExit(f"page {page_no}: expected 6 contest blocks, got {len(blocks)}")
    if not odd and len(blocks) != 8:
        raise SystemExit(f"page {page_no}: expected 8 contest blocks, got {len(blocks)}")

    remaining = list(range(len(expected)))
    # pass 1: match titled blocks
    for b in blocks:
        if not b.title:
            continue
        got_office, got_dist = resolve_title(b.title)
        hit = [k for k in remaining if expected[k][0] == got_office and expected[k][1] == got_dist]
        if not hit:
            raise SystemExit(
                f"page {page_no}: titled block {b.title!r} -> ({got_office},{got_dist}) "
                f"not in expected slots {expected}")
        k = hit[0]
        remaining.remove(k)
        b.office, b.district = got_office, got_dist
        if b.party is None:
            b.party = expected[k][2]
    # pass 2: untitled blocks take the remaining slots in page order
    for b in [b for b in blocks if not b.title]:
        if not remaining:
            raise SystemExit(f"page {page_no}: untitled block with no expected slot left")
        k = remaining.pop(0)
        b.office, b.district = expected[k][0], expected[k][1]
        if b.party is None:
            b.party = expected[k][2]
        b.repairs.append(f"title inferred by page position: {b.office} ({b.party})")
    if remaining:
        raise SystemExit(f"page {page_no}: unfilled expected slots {remaining} for {precinct}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    input_path, output_path = sys.argv[1], sys.argv[2]
    raw = open(input_path, encoding="utf-8").read()
    chunks = re.split(PAGE_SPLIT_RE, raw)
    pages: dict[int, str] = {}
    for i in range(1, len(chunks), 2):
        pages[int(chunks[i])] = chunks[i + 1]
    if sorted(pages) != list(range(1, 53)):
        raise SystemExit(f"expected pages 1..52, got {sorted(pages)}")

    log: list[str] = []
    repairs: list[str] = []
    rows_out: list[list] = []
    used_names: list[str] = []

    for pi in range(26):
        p1, p2 = 2 * pi + 1, 2 * pi + 2
        b1, hdr1, rv1 = parse_page(pages[p1], p1, log)
        b2, hdr2, rv2 = parse_page(pages[p2], p2, log)

        # precinct name: committee titles on the even page, else header blob
        name = hdr1 or hdr2
        committee_name = None
        for b in b2:
            if b.title:
                m = COMMITTEE_RE.match(b.title.strip().upper())
                if m:
                    base = m.group(1).strip()
                    committee_name = PRECINCT_NAME.get(base)
                    if committee_name:
                        break
        if committee_name and name:
            if PRECINCT_NAME.get(clean(name).upper()) not in (None, committee_name):
                log.append(f"pages {p1}-{p2}: header name {name!r} != committee name "
                           f"{committee_name!r}")
        if committee_name:
            name = committee_name
        if not name:
            raise SystemExit(f"pages {p1}-{p2}: no precinct name available")
        std = PRECINCT_NAME.get(name.upper())
        if std is None:
            raise SystemExit(f"pages {p1}-{p2}: unrecognized precinct name {name!r}")
        name = std
        used_names.append(name)

        rv = rv1 or rv2
        if rv1 and rv2 and rv1 != rv2:
            log.append(f"pages {p1}-{p2}: RV/BC mismatch {rv1} vs {rv2}")
        if rv is None:
            raise SystemExit(f"pages {p1}-{p2}: no RV/BC line on either page")
        rows_out.append([COUNTY, name, "Registered Voters", "", "", "", rv[0]])
        rows_out.append([COUNTY, name, "Ballots Cast", "", "", "", rv[1]])

        infer_blocks(name, p1, b1, log)
        infer_blocks(name, p2, b2, log)
        for b in b1 + b2:
            csum = sum(v for _, v in b.rows)
            if b.total is None:
                raise SystemExit(f"{name} pages {p1}-{p2} {b.office}: Total Votes missing")
            if csum != b.total:
                raise SystemExit(
                    f"{name} pages {p1}-{p2} {b.office} ({b.party}): candidate sum {csum} "
                    f"!= Total Votes {b.total}: {b.rows}")
            for nm, v in b.rows:
                rows_out.append([COUNTY, name, b.office, b.district, b.party,
                                 norm_name(nm), v])
            for r in b.repairs:
                repairs.append(f"{name} {p1}/{p2} {b.office} ({b.party}): {r}")

    if len(set(used_names)) != 26:
        raise SystemExit(f"expected 26 distinct precincts, got {len(set(used_names))}: {used_names}")

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(rows_out)

    print(f"wrote {len(rows_out)} rows ({len(used_names)} precincts) -> {output_path}")
    print(f"precincts: {', '.join(used_names)}")
    if repairs:
        print(f"\nREPAIRS ({len(repairs)}):")
        for r in repairs:
            print("  REPAIR:", r)
    if log:
        print(f"\nLOG ({len(log)}):")
        for line in log:
            print("  LOG:", line)


if __name__ == "__main__":
    main()