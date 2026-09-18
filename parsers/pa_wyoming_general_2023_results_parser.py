#!/usr/bin/env python3
"""Wyoming County PA -- 2023 General Election official municipal precinct tally.

Source: "Wyoming County Official Municipal Precinct Tally 2023 General.pdf"
(pdftotext -layout text, one page per \f-separated section).

Layout
------
Pages 1-20: per-contest tables.  Each block has rotated candidate-column
headers, 29 municipality rows (fewer for district-limited contests) and a
final county totals row (plus trailing statistic columns, ignored here).
Pages 21-49: per-municipality candidate/write-in detail reports for local
offices (township auditor, township supervisor, borough council, mayor,
constable, tax collector).

Column assignment in the contest tables is positional: candidate columns are
read left-to-right (the rotated labels sit at their column x-positions in the
layout text), and each municipality row's numbers are assigned to the columns
in order, padding missing trailing cells with 0.  Every block is reconciled
against its printed county totals row and mismatches are reported on stderr.

Usage: python pa_wyoming_general_2023_results_parser.py <input.txt> <output.csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Wyoming"

# ---------------------------------------------------------------------------
# Municipality names, as printed in the tally tables, with normalization of
# the Reg/Ind/typo variants.  "Windham Township" (no suffix) appears only in
# school-district blocks, where the source reports Reg and Ind combined.
# ---------------------------------------------------------------------------
MUNI_ALIASES = [
    (r"Braintrim\s+Township", "Braintrim Township"),
    (r"Clinton\s+Township", "Clinton Township"),
    (r"Eaton\s+Township", "Eaton Township"),
    (r"Exeter\s+Township", "Exeter Township"),
    (r"Factoryville\s+Borough\s+Ward\s+1", "Factoryville Borough Ward 1"),
    (r"Factoryville\s+Borough\s+Ward\s+2", "Factoryville Borough Ward 2"),
    (r"Falls\s+Township", "Falls Township"),
    (r"Forkston\s+Township", "Forkston Township"),
    (r"Laceyville\s+Borough", "Laceyville Borough"),
    (r"Lemon\s+Township", "Lemon Township"),
    (r"Mehoopany\s+Township", "Mehoopany Township"),
    (r"Meshoppen\s+Borough", "Meshoppen Borough"),
    (r"Meshoppen\s+Township", "Meshoppen Township"),
    (r"Monroe\s+Township", "Monroe Township"),
    (r"Nicholson\s+Borough(?:\s+at\s+Large)?", "Nicholson Borough"),
    (r"Nicholson\s+Township", "Nicholson Township"),
    (r"Nicholson\s+Towhship", "Nicholson Township"),
    (r"North\s+Branch\s+Township", "North Branch Township"),
    (r"Northmoreland\s+Township", "Northmoreland Township"),
    (r"Noxen\s+Township", "Noxen Township"),
    (r"Overfield\s+Township", "Overfield Township"),
    (r"Tunkhannock\s+Borough\s+Ward\s+1", "Tunkhannock Borough Ward 1"),
    (r"Tunkhannock\s+Borough\s+Ward\s+2", "Tunkhannock Borough Ward 2"),
    (r"Tunkhannock\s+Borough\s+Ward\s+3", "Tunkhannock Borough Ward 3"),
    (r"Tunkhannock\s+Borough\s+Ward\s+4", "Tunkhannock Borough Ward 4"),
    (r"Tunkhannock\s+Township\s*#\s*1", "Tunkhannock Township #1"),
    (r"Tunkhannock\s+Township\s*#\s*2", "Tunkhannock Township #2"),
    (r"Washington\s+Township", "Washington Township"),
    (r"Windham\s+Township\s+RE?G\b", "Windham Township Reg"),
    (r"Windham\s+Township\s+IND\b", "Windham Township Ind"),
    (r"Windham\s+Township\s+Ind\b", "Windham Township Ind"),
    (r"Windham\s+Township", "Windham Township"),
]
MUNI_RE = re.compile(
    r"^\s*(?:" + "|".join(f"(?:{pat})" for pat, _ in MUNI_ALIASES) + r")",
    re.IGNORECASE,
)


def muni_name(line: str):
    """Return (canonical_name, match) if the line starts a municipality row."""
    for pat, canon in MUNI_ALIASES:
        m = re.match(r"^\s*" + pat, line, re.IGNORECASE)
        if m:
            return canon, m
    return None, None


# ---------------------------------------------------------------------------
# Contest blocks on pages 1-20.  Candidates are listed in table column order
# (left-to-right); verified against the PDF and the county's Statement of
# Votes Cast.  The untitled page-10 block (BOB ROBERTS / REPUBLICAN) is the
# Sheriff contest per the county's official Statement of Votes Cast by
# Geography (the PDF header cell for that contest is blank in the source).
# ---------------------------------------------------------------------------
BLOCKS = [
    # (page, title_regex, occurrence, office, district, [(candidate, party)])
    (1, r"JUDICIAL RETENTION", 0, "Judge of the Superior Court Retention - Jack Panella", "",
     [("Yes", ""), ("No", "")]),
    (2, r"JUDICIAL RETENTION", 0, "Judge of the Superior Court Retention - Victor P. Stabile", "",
     [("Yes", ""), ("No", "")]),
    (3, r"JUSTICE OF THE SUPREME", 0, "Justice of the Supreme Court", "",
     [("Daniel McCaffery", "DEM"), ("Carolyn Carluccio", "REP"), ("Write-ins", "")]),
    (4, r"Judge of the Superior Court", 0, "Judge of the Superior Court", "",
     [("Jill Beck", "DEM"), ("Timika Lane", "DEM"), ("Maria Battista", "REP"),
      ("Harry F. Smail Jr.", "REP"), ("Write-ins", "")]),
    (5, r"COMMONWEALTH Court", 0, "Judge of the Commonwealth Court", "",
     [("Matt Wolf", "DEM"), ("Megan Martin", "REP"), ("Write-ins", "")]),
    (6, r"County Commissioner", 0, "County Commissioner", "",
     [("Ernie King", "DEM"), ("Tom Henry", "REP"), ("Rick Wilbur", "REP"), ("Write-ins", "")]),
    (7, r"County Auditor", 0, "County Auditor", "",
     [("Laura Dickson", "DEM"), ("Judy Shupp", "REP"), ("Ashley Darby", "REP"),
      ("Write-ins", "")]),
    (8, r"Prothonatary", 0, "County Prothonotary & Clerk of Courts", "",
     [("Cindy Zika Adams", "REP"), ("Write-ins", "")]),
    (9, r"Register of Wills", 0, "County Register of Wills & Recorder", "",
     [("Dennis L. Montross", "REP"), ("Write-ins", "")]),
    (10, r"BOB ROBERTS", 0, "Sheriff", "",
     [("Bob Roberts", "REP"), ("Write-ins", "")]),
    (11, r"MAGISTERIAL DISTRICT JUSTICE", 0, "Magisterial District Judge", "44-3-01",
     [("David Plummer", "D/R"), ("Write-ins", "")]),
    (12, r"Elk Lake School District", 0, "School Director (4 Year)",
     "Elk Lake School District Region 4", [("Harold G. Bender", "REP")]),
    (13, r"1--\s*4Years", 0, "School Director (4 Year)",
     "Lackawanna Trail School District Region 1",
     [("Heather Clark", "D/R"), ("Jaclyn Litwin", "D/R"), ("Write-ins", "")]),
    (13, r"1--\s*2Years", 0, "School Director (2 Year)",
     "Lackawanna Trail School District Region 1",
     [("Candace Haft", "REP"), ("Write-ins", "")]),
    (14, r"Region 2--\s*4 Years", 0, "School Director (4 Year)",
     "Lackawanna Trail School District Region 2", [("Eric Johnson", "D/R"), ("Write-ins", "")]),
    (15, r"Lake Lehman School Region 1", 0, "School Director (4 Year)",
     "Lake Lehman School District Region 1",
     [("Lorraine Farrell", "DEM"), ("Sara Saylor Kashatus", "DEM"),
      ("Thomas Scott Walsh", "REP"), ("Mark Wallace", "REP"), ("Write-ins", "")]),
    (16, r"Region 1--\s*4 Years", 0, "School Director (4 Year)",
     "Tunkhannock Area School District Region 1", [("Lori Bennett", "D/R"), ("Write-ins", "")]),
    (17, r"Region 2--\s*4 Years", 0, "School Director (4 Year)",
     "Tunkhannock Area School District Region 2",
     [("Kari Hilbert Oshirak", "D/R"), ("William Prebola", "REP"), ("Write-ins", "")]),
    (18, r"Region 3", 0, "School Director (4 Year)",
     "Tunkhannock Area School District Region 3", [("John Burke", "D/R"), ("Write-ins", "")]),
    (19, r"Wyalusing Area Region 3", 0, "School Director (4 Year)",
     "Wyalusing Area School District Region 3",
     [("Tiffani L Warner", "D/R"), ("Daryl Travis Knapp", "REP"), ("Write-ins", "")]),
    (20, r"Wyoming Area School District At Large", 0, "School Director (4 Year)",
     "Wyoming Area School District At Large",
     [("Peter J Butera", "D/R"), ("Rebecca Rutkoski", "D/R"), ("Michael A Kachmarsky", "D/R"),
      ("Kirby Kunkle", "DEM"), ("Mara A Pagnotti-Valenti", "D/R"), ("Len Pribula", "REP"),
      ("Write-ins", "")]),
    (20, r"Wyoming Area School District At Large", 1, "School Director (2 Year)",
     "Wyoming Area School District At Large",
     [("Nick Deangelo", "D/R"), ("Write-ins", "")]),
]

# Pages 21-49: per-municipality detail reports.
FIRST_DETAIL_PAGE = 21

DETAIL_MUNI = {
    "BRAINTRIM TOWNSHIP": "Braintrim Township",
    "CLINTON TOWNSHIP": "Clinton Township",
    "EATON TOWNSHIP": "Eaton Township",
    "EXETER TOWNSHIP": "Exeter Township",
    "FACTORYVILLE WARD 1": "Factoryville Borough Ward 1",
    "FACTORYVILLE WARD 2": "Factoryville Borough Ward 2",
    "FALLS TOWNSHIP": "Falls Township",
    "FORKSTON TOWNSHIP": "Forkston Township",
    "LACEYVILLE BOROUGH": "Laceyville Borough",
    "LEMON TOWNSHIP": "Lemon Township",
    "MEHOOPANY TOWNSHIP": "Mehoopany Township",
    "MESHOPPEN BOROUGH": "Meshoppen Borough",
    "MESHOPPEN TOWNSHIP": "Meshoppen Township",
    "MONROE TOWNSHIP": "Monroe Township",
    "NICHOLSON BOROUGH": "Nicholson Borough",
    "NICHOLSON TOWNSHIP": "Nicholson Township",
    "NORTH BRANCH TOWNSHIP": "North Branch Township",
    "NORTHMORELAND TOWNSHIP": "Northmoreland Township",
    "NOXEN TOWNSHIP": "Noxen Township",
    "OVERFIELD TOWNSHIP": "Overfield Township",
    "TUNKHANNOCK BORO WARD 1": "Tunkhannock Borough Ward 1",
    "TUNKHANNOCK BORO WARD 2": "Tunkhannock Borough Ward 2",
    "TUNKHANNOCK BORO WARD 3": "Tunkhannock Borough Ward 3",
    "TUNKHANNOCK BORO WARD 4": "Tunkhannock Borough Ward 4",
    "TUNKHANNOCK TOWNSHIP #1": "Tunkhannock Township #1",
    "TUNKHANNOCK TOWNSHIP #2": "Tunkhannock Township #2",
    "WASHINGTON TOWNSHIP": "Washington Township",
    "WINDHAM TOWNSHIP": "Windham Township",  # detail pages combine Reg + Ind
}

OFFICE_RE = re.compile(
    r"^(auditor|supervisor|constable|tax collector|mayor|councilmen|councilman|"
    r"council member)\b", re.IGNORECASE)
PARTY_RE = re.compile(
    r"\s(DEM/REP|Dem/Rep|DEMOCRATIC|Democratic|REPUBLICAN|Republican|REP|Rep|DEM|Dem)(?=\s|$)")
PARTY_CODE = {"DEM/REP": "D/R", "DEMOCRATIC": "DEM", "REPUBLICAN": "REP",
              "DEM": "DEM", "REP": "REP"}


def office_header(line: str):
    """Return (office, term) for a local-office header line, else None."""
    m = OFFICE_RE.match(line.strip())
    if not m or not re.search(r"vote\s*for", line, re.IGNORECASE):
        return None
    term = re.search(r"(\d+)\s*year", line, re.IGNORECASE)
    n = int(term.group(1)) if term else None
    key = m.group(1).lower()
    if key == "auditor":
        return f"Township Auditor ({n} Year)", n
    if key == "supervisor":
        return f"Township Supervisor ({n} Year)", n
    if key == "constable":
        return f"Constable ({n} Year)", n
    if key == "tax collector":
        return f"Tax Collector ({n} Year)", n
    if key == "mayor":
        return f"Mayor ({n} Year)", n
    return f"Borough Council ({n} Year)", n


def clean_candidate(raw: str):
    """Strip address/party/status text from a detail-report candidate row.

    Returns (name, party).
    """
    s = re.sub(r"\s+", " ", raw.strip())
    party = ""
    m = PARTY_RE.search(s)
    if m:
        party = PARTY_CODE[m.group(1).upper()]
        s = s[: m.start()]
    s = re.sub(r"\s+W-\d+\s*$", "", s)  # "Bob Robinson W-3" badge suffix
    d = re.search(r"\s+(?:PO\s*B|P\.?O\.?\s*B?o?x|\d)", s, re.IGNORECASE)
    if d:
        s = s[: d.start()]  # street address / PO box starts here
    s = re.sub(
        r"\s+(?:NOT INTERESTED|Not Interested|NOT REGISTERED|OUT OF DISTRICT|"
        r"Winner|FILED|Filed|Declined).*$", "", s, flags=re.IGNORECASE)
    s = s.rstrip(" `-,")
    if s.lower() == "scattered":
        return "Write-ins", ""
    if s.lower() == "nonsense":
        return "nonsense", ""
    return s, party


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {argv[0]} <input.txt> <output.csv>")
    with open(argv[1], encoding="utf-8") as fh:
        pages = fh.read().split("\f")

    rows = []   # (precinct, office, district, party, candidate, votes)
    report = []

    # --- contest blocks (pages 1-20) ---
    by_page = {}
    for b in BLOCKS:
        by_page.setdefault(b[0], []).append(b)
    for page in sorted(by_page):
        text = pages[page - 1]
        anchors = []
        for b in by_page[page]:
            _, title_re, occ, office, district, candidates = b
            ms = list(re.finditer(title_re, text, re.IGNORECASE))
            if len(ms) <= occ:
                raise ValueError(f"page {page}: title {title_re!r} occurrence {occ} missing")
            anchors.append((ms[occ].end(), b))
        anchors.sort(key=lambda a: a[0])
        for i, (pos, b) in enumerate(anchors):
            end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
            _, title_re, occ, office, district, candidates = b
            segment = text[pos:end]
            ncand = len(candidates)
            data = {}
            totals = None
            seen_muni = False
            for line in segment.splitlines():
                if not line.strip():
                    continue
                name, m = muni_name(line)
                if name:
                    nums = [int(n) for n in re.findall(r"\d+", line[m.end():])]
                    if name in data:
                        report.append(f"FAIL {office}: duplicate row {name}")
                    data[name] = nums
                    seen_muni = True
                    continue
                if seen_muni and re.search(r"\d", line):
                    nums = [int(n) for n in re.findall(r"\d+", line)]
                    if len(nums) < ncand:
                        report.append(f"FAIL {office}: short totals row {line.strip()!r}")
                        nums += [0] * (ncand - len(nums))
                    totals = nums[:ncand]
                    break
            label = f"{office} (p{page})"
            if totals is None:
                report.append(f"FAIL {office}: totals row not found")
                totals = [0] * ncand
            for j, (cand, _p) in enumerate(candidates):
                s = sum((v[j] if j < len(v) else 0) for v in data.values())
                if s != totals[j]:
                    report.append(
                        f"FAIL {office}: {cand} precinct sum {s} != printed total {totals[j]}")
            for precinct, vals in data.items():
                if len(vals) > ncand:
                    report.append(f"WARN {office}: {precinct}: extra numbers ignored")
                for j, (cand, party) in enumerate(candidates):
                    v = vals[j] if j < len(vals) else 0
                    if v:
                        rows.append((precinct, office, district, party, cand, v))

    # --- per-municipality local-office detail reports (pages 21-49) ---
    for pg in range(FIRST_DETAIL_PAGE, len(pages) + 1):
        muni = None
        office = None
        writeins = 0
        pending = []

        def flush():
            nonlocal writeins
            if office is not None:
                if writeins:
                    pending.append((muni, office, muni, "", "Write-ins", writeins))
                rows.extend(pending)
            writeins = 0
            pending.clear()

        for line in pages[pg - 1].splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            hdr = DETAIL_MUNI.get(re.sub(r"\s+", " ", stripped.upper()))
            if hdr:
                flush()
                muni, office = hdr, None
                continue
            oh = office_header(line)
            if oh:
                flush()
                office = oh[0]
                continue
            if stripped.upper().startswith(("OFFICE", "SCHOOL DIRECTOR")):
                flush()
                office = None
                continue
            if office is None or muni is None:
                continue
            if re.fullmatch(r"\d+", stripped):
                writeins += int(stripped)  # unnamed write-in candidate row
                continue
            ms = list(re.finditer(r"\d+", stripped))
            if not ms:
                continue  # label row with no votes
            votes = int(ms[-1].group())
            name, party = clean_candidate(stripped[: ms[-1].start()])
            if not name:
                writeins += votes
                continue
            pending.append((muni, office, muni, party, name, votes))
        flush()

    # --- write CSV ---
    fieldnames = ["county", "precinct", "office", "district", "party", "candidate",
                  "votes", "election_day", "mail", "provisional"]
    with open(argv[2], "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fieldnames)
        for precinct, office, district, party, cand, votes in rows:
            w.writerow([COUNTY, precinct, office, district, party, cand,
                        votes, "", "", ""])

    # --- validation summary ---
    ok = [r for r in report if r.startswith("WARN")]
    bad = [r for r in report if r.startswith("FAIL")]
    print(f"wrote {len(rows)} rows to {argv[2]}", file=sys.stderr)
    for r in ok:
        print(r, file=sys.stderr)
    for r in bad:
        print(r, file=sys.stderr)
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)