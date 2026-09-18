#!/usr/bin/env python3
"""Fulton County, PA 2023 General — county-level results parser.

Source: "Fulton County Certified Results Precinct 2023 General.pdf" — despite
the filename this is the county's certified ballot-position/return document
(8 pages) PLUS a Cumulative Results Report (7 pages) with countywide
Mail / Election Day / Provisional breakdowns for every contest.  There is no
per-precinct data anywhere in the file.  Recovered via PaddleOCR-VL
(work2023/txt/Fulton__ocr.txt); this parser consumes that OCR text extract,
with office identification keyed on candidate names (OCR drops several
office headers; verified against the rendered PDF pages).

Structure used:
- Certification pages: retention questions (Panella, Stabile — both Superior
  Court — and Common Pleas judge Jeremiah D. Zook) with YES/NO votes in
  digits.
- Cumulative Results Report: one table per contest with Choice / Party /
  Mail / Election Day Voting / Provisional [/ Total] columns, Cast Votes /
  Undervotes / Overvotes / Unresolved write-in votes rows.  Named write-in
  rows ("(W)") are folded into a single Write-ins row per contest.  Cross-
  endorsed candidates ("DEM, REP") are emitted as party "D/R" per repo
  convention.
- Header box: Registered Voters 3,944 / Ballots Cast 9,266 (42.56%).

Usage: python parsers/pa_fulton_general_2023_results_parser.py <ocr.txt> <out.csv>
"""
from __future__ import annotations

import csv
import html
import re
import sys

COUNTY = "Fulton"

REG_VOTERS, BALLOTS_CAST = 3944, 9266

NAME_FIX = {
    "Daniel Mccaffery": "Daniel McCaffery",
    "Harry F Smail Jr": "Harry F. Smail Jr.",
    "Harry F. Smail Jr": "Harry F. Smail Jr.",  # key post initial-period pass
}

# Contest -> office/district, keyed on the first candidate OCR shows for the
# table (several office headers were dropped by OCR; verified visually).
OFFICE_BY_CAND = {
    "Daniel McCaffery": ("Justice of the Supreme Court", ""),
    "Jill Beck": ("Judge of the Superior Court", ""),
    "Matt Wolf": ("Judge of the Commonwealth Court", ""),
    "Tamela Mellott Heming": ("Magisterial District Judge", "39-04-03"),
    "Devin C. Horne": ("Magisterial District Judge", "39-04-01"),
    "Paula Jean Shives": ("County Commissioner", ""),
    "Michael A. Sprague": ("County Sheriff", ""),
    "Phil Harper": ("District Attorney", ""),
    "Michelle D. Grammick": ("County Treasurer", ""),
    "Berley L. Souders": ("County Coroner", ""),
    "Holly R. Falkosky": ("County Auditor", ""),
    "Donald R. Truax Iii": ("School Director (4 Year)", "Central Fulton"),
    "L. Allen Morton": ("School Director (4 Year)", "Southern Fulton"),
    "Robert Alan Knepper": ("School Director (4 Year)", "Forbes Road"),
    "Anthony W. Vinson": ("School Director (2 Year)", "Forbes Road"),
}

TERM_WORDS = {"TWO": "2", "FOUR": "4", "SIX": "6"}


def map_header(h: str):
    """Cumulative-Results contest header -> (office, district), or None.

    Headers survived OCR on the school-district and municipal pages
    (pages ~13-38), e.g. 'AYR TOWNSHIP SUPERVISOR SIX YEAR TERM -
    (VOTE FOR ONE)'.
    """
    h = re.sub(r"\s+", " ", h).strip()
    m = re.match(r"(.+?) SCHOOL DISTRICT SCHOOL DIRECTOR "
                 r"(TWO|FOUR|SIX) YEAR TERM", h, re.I)
    if m:
        return (f"School Director ({TERM_WORDS[m.group(2)]} Year)",
                title(m.group(1)))
    m = re.match(r"(.+?) TOWNSHIP (SUPERVISOR|AUDITOR|CONSTABLE|TAX COLLECTOR)"
                 r" (TWO|FOUR|SIX) YEAR TERM", h, re.I)
    if m:
        return (f"Township {m.group(2).title()} ({TERM_WORDS[m.group(3)]} Year)",
                f"{title(m.group(1))} Township")
    m = re.match(r"(.+?) BOROUGH (MAYOR|AUDITOR|COUNCIL) (TWO|FOUR|SIX) YEAR TERM",
                 h, re.I)
    if m:
        return (f"Borough {m.group(2).title()} ({TERM_WORDS[m.group(3)]} Year)",
                title(m.group(1)).replace("Mcconnellsburg", "McConnellsburg"))
    return None

# Cross-check: certification-page totals (partisan contests).
CERT_TOTALS = {
    "Justice of the Supreme Court": {"Daniel McCaffery": 695, "Carolyn Carluccio": 3002},
    "Judge of the Superior Court": {"Jill Beck": 715, "Timika Lane": 579,
                                    "Maria Battista": 2663, "Harry F. Smail Jr.": 2552},
    "Judge of the Commonwealth Court": {"Matt Wolf": 683, "Megan Martin": 3019},
}


def cells(tr: str):
    raw = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
    return [html.unescape(c.replace("\\n", " ")) for c in raw]


def plain(c: str) -> str:
    c = c.replace("\\n", " ")  # OCR emits literal backslash-n inside cells
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()


def cell_numbers(c: str):
    """Numeric tokens in a cell, ignoring percentage tokens like '53.41%'."""
    out = []
    for tok in plain(c).split():
        if "%" in tok:
            continue
        if re.fullmatch(r"\d{1,3}(,\d{3})*", tok):
            out.append(int(tok.replace(",", "")))
    return out


def title(name: str) -> str:
    out = []
    for w in name.split():
        out.append(w.capitalize())
    s = " ".join(out)
    s = s.replace("Mccaffery", "McCaffery")
    s = re.sub(r"\.$", "", s)  # OCR artifact: 'Hervey P. Hann.' -> 'Hervey P. Hann'
    # add a period after bare middle initials ('Jeremiah D Zook' -> 'Jeremiah D. Zook')
    s = re.sub(r"\b([A-Z])\b(?=\s)", r"\1.", s)
    return NAME_FIX.get(s, s)


def norm_party(raw: str) -> str:
    p = re.sub(r"[.,\s]+$", "", raw.strip()).upper()
    if p in ("DEM, REP", "DEM,REP", "REP, DEM"):
        return "D/R"
    return p


def parse(path: str):
    src = open(path, encoding="utf-8").read()
    rows: list[list] = []
    problems: list[str] = []

    tables = re.findall(r"<table.*?</table>", src, re.S)

    # --- retention questions: YES/NO votes in digits (certification pages) ---
    for t in tables:
        text = plain(t)
        if "RETENTION" not in text.upper():
            continue
        for m in re.finditer(
                r"(JUDGE OF THE SUPERIOR COURT|JUDGE OF THE COURT OF COMMON PLEAS)"
                r"\s*RETENTION ELECTION\s*([A-Z][A-Z .]+?)\s*Shall", text):
            court, name = m.group(1), m.group(2).strip()
            if "SUPERIOR" in court:
                office = f"Superior Court Retention - {title(name)}"
            else:
                office = f"Court of Common Pleas Retention - {title(name)}"
            tail = text[m.start():]
            yes = re.search(r"YES VOTES IN DIGITS\s*([\d,]+)", tail)
            no = re.search(r"NO VOTES IN DIGITS\s*([\d,]+)", tail)
            if yes and no:
                rows.append([COUNTY, office, "", "", "Yes",
                             yes.group(1).replace(",", ""), "", "", ""])
                rows.append([COUNTY, office, "", "", "No",
                             no.group(1).replace(",", ""), "", "", ""])

    # --- cumulative results report tables --------------------------------
    # Pass 1: assign each breakdown table an (office, district).  Tables on
    # the school-district/municipal pages carry a readable contest header
    # div (mapped via map_header); elsewhere OCR dropped the headers, so a
    # table is keyed by its first non-(W) candidate (OFFICE_BY_CAND).  A
    # table with no named candidate but a Cast Votes row is a page-span
    # continuation of the previous table.
    assigned = []  # (table_html, office, district)
    last_key = None
    last_header = None  # most recent contest-header line (div or markdown)
    pos = 0  # end offset of the previous table
    event_re = re.compile(
        r"(?P<table><table.*?</table>)|"
        r'(?:<div style="text-align: center;">(?P<divhdr>[^<]+)</div>)|'
        r"(?P<mdhdr>^#{1,3} (?P<mdtext>[A-Z][^\n]{5,120})$)", re.S | re.M)
    for m in event_re.finditer(src):
        if m.group("table"):
            t = m.group("table")
            pos = m.end()
            hdr_text = last_header
            last_header = None
            if hdr_text and "RETENTION" in hdr_text.upper():
                continue  # certification retention tables, handled separately
            hdr_key = map_header(hdr_text or "")
            text = plain(t)
            trs = re.findall(r"<tr>(.*?)</tr>", t, re.S)
            if not trs or "Choice Party" not in text[:200]:
                continue
            first_cand = None
            for tr in trs[1:]:
                cs = [plain(c) for c in cells(tr)]
                if not cs:
                    continue
                name = cs[0]
                if name and name.lower() != "choice" and "(W)" not in name \
                        and "Cast Votes" not in name \
                        and "Undervotes" not in name and "Unresolved" not in name:
                    first_cand = name
                    break
            key = None
            if hdr_key is not None:
                key = hdr_key  # readable header wins
            elif first_cand is not None:
                cand_key = OFFICE_BY_CAND.get(title(first_cand))
                if cand_key is not None:
                    key = cand_key
                elif "Cast Votes" in text and last_key and "Undervotes" in text:
                    # table whose header OCR dropped and whose first
                    # candidate is unknown (e.g. a second ballot-style
                    # table under the previous contest's header) — treat
                    # as a continuation of that contest
                    key = last_key
                else:
                    problems.append(
                        f"unrecognized contest (first candidate {title(first_cand)!r})")
                    continue
            elif "Cast Votes" in text and last_key and "Undervotes" in text:
                key = last_key  # page-span continuation
            else:
                continue
            last_key = key
            assigned.append((t, key[0], key[1]))
        else:
            hdr = (m.group("divhdr") or m.group("mdtext") or "").strip()
            if m.start() > pos and ("RETENTION" in hdr.upper() or map_header(hdr)):
                last_header = hdr

    # Pass 2: accumulate rows per office across that office's tables.  One
    # OCR table can hold TWO contests (the next contest's header row is
    # embedded in the same <table> element) — such rows switch the current
    # contest mid-table.  Cast Votes rows are collected per contest; each
    # ballot-style table under one header carries its own Cast Votes, so
    # validation compares rows against the SUM of Cast Votes rows.
    state: dict = {}  # (office, district) -> dict(...)
    order = []
    cur_key = None
    for t, office, district in assigned:
        cur_key = (office, district)
        st = state.setdefault(cur_key, {"cand_rows": [], "writeins": [0, 0, 0],
                                        "under": [], "over": [], "casts": []})
        trs = re.findall(r"<tr>(.*?)</tr>", t, re.S)
        prev_cand = None
        for tr in trs[1:]:
            cs = [plain(c) for c in cells(tr)]
            if not cs:
                continue
            vals = []
            for c in cs:
                vals.extend(cell_numbers(c))
            joined = " ".join(cs)
            # embedded next-contest header row (OCR merged two tables)
            embedded = map_header(cs[0]) if cs[0] and len(cs) <= 2 else None
            if embedded is not None:
                cur_key = embedded
                st = state.setdefault(cur_key, {"cand_rows": [], "writeins": [0, 0, 0],
                                                "under": [], "over": [], "casts": []})
                prev_cand = None
                continue
            if "Cast Votes" in joined:
                if vals:
                    st["casts"].append(vals)
                continue
            if "Undervotes" in joined and vals:
                st["under"].append(vals)
                continue
            if "Overvotes" in joined and vals:
                st["over"].append(vals)
                continue
            if "Unresolved" in joined:
                continue  # all-zero in this source
            name = cs[0]
            # wrapped party cell (OCR splits 'DEM, REP' across two rows)
            if not name and len(cs) >= 2 and re.fullmatch(r"(?i)(DEM|REP)[,.]?", cs[1]):
                if prev_cand is not None:
                    base = st["cand_rows"][prev_cand]
                    st["cand_rows"][prev_cand] = (
                        base[0], norm_party(base[1].rstrip(",") + " " + cs[1])) + base[2:]
                continue
            if not name or not vals:
                continue
            if "(W)" in name:
                if len(vals) >= 3:
                    st["writeins"][0] += vals[0]
                    st["writeins"][1] += vals[1]
                    st["writeins"][2] += vals[2]
                continue
            party = ""
            for c in cs[1:]:
                pt = plain(c).upper()
                if pt.startswith("DEM") or pt.startswith("REP") or pt.startswith("IND"):
                    party = norm_party(plain(c))
                    break
            if len(vals) < 3:
                problems.append(f"{office}: short row {cs!r}")
                continue
            st["cand_rows"].append((title(name), party, vals[0], vals[1], vals[2]))
            prev_cand = len(st["cand_rows"]) - 1

    for (office, district), st in state.items():
        cand_rows, writeins = st["cand_rows"], st["writeins"]
        if not cand_rows and not any(writeins):
            problems.append(f"{office} ({district}): no candidate rows")
            continue
        for name, party, mail, ed, prov in cand_rows:
            rows.append([COUNTY, office, district, party, name,
                         str(mail + ed + prov), str(ed), str(mail), str(prov)])
        if any(writeins):
            rows.append([COUNTY, office, district, "", "Write-ins",
                         str(sum(writeins)), str(writeins[1]),
                         str(writeins[0]), str(writeins[2])])
        # Undervotes/Overvotes: the source prints summary rows once per
        # ballot-style pool (page-split tables carry them only on the last
        # piece), so summing across tables is correct in both cases.
        for label, entries in (("Undervotes", st["under"]), ("Overvotes", st["over"])):
            if entries:
                vals = [sum(e[i] for e in entries if len(e) >= 3) for i in range(3)]
                mail, ed, prov = vals[0], vals[1], vals[2]
                rows.append([COUNTY, office, district, "", label,
                             str(mail + ed + prov), str(ed), str(mail), str(prov)])

        # validation: candidates + write-ins == sum of Cast Votes rows
        # (under/over are reported separately and are NOT in Cast Votes;
        # multi-ballot-style tables each carry their own Cast Votes)
        if st["casts"]:
            tot = [sum(c[i] for c in st["casts"]) for i in range(3)]
            for col, idx in (("mail", 0), ("election_day", 1), ("provisional", 2)):
                total = sum(r[2 + idx] for r in cand_rows) + writeins[idx]
                if total != tot[idx]:
                    problems.append(
                        f"{office} ({district}) {col}: rows sum {total} != Cast Votes {tot[idx]}")
        # cross-check appellate candidates against the certification pages
        for name, party, mail, ed, prov in cand_rows:
            cert = CERT_TOTALS.get(office, {}).get(name)
            if cert is not None and mail + ed + prov != cert:
                problems.append(
                    f"{office}/{name}: cumulative {mail+ed+prov} != certification {cert}")

    missing = ({o for o, _ in OFFICE_BY_CAND.values()}
               - {o for o, _ in state})
    missing = {o for o in missing if not o.startswith("School Director")}
    if missing:
        problems.append(f"missing contests: {sorted(missing)}")
    if problems:
        sys.stderr.write("FULTON PROBLEMS:\n" + "\n".join(problems) + "\n")

    # metadata rows from the report header box
    rows.insert(0, [COUNTY, "Registered Voters", "", "", "", str(REG_VOTERS), "", "", ""])
    rows.insert(1, [COUNTY, "Ballots Cast", "", "", "", str(BALLOTS_CAST), "", "", ""])
    return rows


def main() -> None:
    inp, out = sys.argv[1], sys.argv[2]
    rows = parse(inp)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "office", "district", "party", "candidate",
                    "votes", "election_day", "mail", "provisional"])
        w.writerows(rows)
    print(f"{COUNTY}: wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()