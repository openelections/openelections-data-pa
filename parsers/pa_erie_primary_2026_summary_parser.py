#!/usr/bin/env python3
"""Parse Erie County PA 2026 Primary county-level summary results.

Source: Erie County 031365_2026_general_primary_official_summary_results.pdf

Erie's "Summary for: All Contests..." report uses a unique layout. Each contest
block looks like::

    GOVERNOR-DEM (Vote for 1)
    DEM
    Precincts Reported: 149 of 149 (100.00%)

                                                 Election Day   Mail-In   Provisional    Total
    Undervotes                                           288       147               2        437

    Candidate                           Party    Election Day   Mail-In   Provisional    Total
    JOSH SHAPIRO                         DEM           11,024          11,978              80    23,082
    Total Votes                                         11,104          12,013              82    23,199

                                                 Election Day   Mail-In   Provisional    Total
    robert gannon                       WRITE-IN             1               0               0        1
    ...

The party is a SUFFIX on the office header (``-DEM``/``-REP``), not a prefix.
Multi-line contest headers wrap with "DISTRICT-<PARTY> (Vote for N)" on the
second line (e.g. "REPRESENTATIVE IN THE GENERAL ASSEMBLY 1ST LEGISLATIVE\\n
DISTRICT-DEM (Vote for 1)"). Per-precinct committee races
("ERIE WARD <ward> DISTRICT <dist> <PARTY> COMMITTEE...") are skipped.

Named write-in candidates are aggregated into a single "Write-In Totals" row
per (office, district, party).

Usage:
    uv run python parsers/pa_erie_primary_2026_summary_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import subprocess
import sys
from pathlib import Path

# Contest header: ends with "(Vote for N)". The office text (everything
# before "(Vote for N)") may span multiple lines, so the parser below joins
# the preceding line(s) until the office text makes sense.
VOTE_FOR_RE = re.compile(r"\(Vote for\s+(\d+)\)\s*$", re.IGNORECASE)

# Party suffix on office header: "GOVERNOR-DEM", "LIEUTENANT GOVERNOR-REP",
# "REPRESENTATIVE IN CONGRESS – 16TH DISTRICT-DEM".
PARTY_SUFFIX_RE = re.compile(r"-(DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON)\s*$",
                              re.IGNORECASE)

# District extraction: "16TH DISTRICT", "1ST LEGISLATIVE DISTRICT" (possibly
# split across two lines, joined before matching).
DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+(?:LEGISLATIVE\s+|CONGRESSIONAL\s+|SENATORIAL\s+)?"
    r"DIS(?:TRICT|T)?\b",
    re.IGNORECASE,
)

STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
}

# Per-precinct committee race: "ERIE WARD 01 DISTRICT 01 DEMOCRATIC COMMITTEEMAN"
# etc. Skip these.
PER_PRECINCT_COMMITTEE_RE = re.compile(
    r"\b(?:DEMOCRATIC|REPUBLICAN)\s+COMMITTEE\s*(?:MAN|WOMAN|PERSON)?S?\b"
    r"|^\s*[A-Z][A-Z\s]+\s+WARD\s+\d+\s+DISTRICT\s+\d+\b"
    r"|^\s*COMMITTEE\s*(?:MAN|WOMAN|PERSON)S?\b",
    re.IGNORECASE,
)

# Candidate data row: "NAME PARTY <ED> <Mail-In> <Prov> <Total>".
# Name may have spaces, periods, hyphens, apostrophes. Party is uppercase
# alphanumerics (DEM, REP, WRITE-IN). Numbers may have commas.
CANDIDATE_ROW_RE = re.compile(
    r"^\s*(.+?)\s+(DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON|WRITE-IN|NONPARTISAN|NON)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)

# "Total Votes" / "Undervotes" / "Overvotes" row (no name, no party).
AGG_ROW_RE = re.compile(
    r"^\s*(Total Votes|Undervotes|Overvotes)\s+"
    r"(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*$"
)

FIELDNAMES = [
    "county", "office", "district", "party", "candidate",
    "votes", "election_day", "mail", "provisional",
]

_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "").strip()
    out = []
    for w in s.split():
        if _ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif w.upper() in ("JR", "SR"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif len(w) >= 3 and w[:2].lower() == "mc":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _normalize_office(raw: str) -> tuple[str, str]:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    key = re.sub(r"\s+", " ", key).strip()
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "")
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "")
    return (raw.title(), district)


def _join_contest_header(lines: list[str], end_idx: int) -> str:
    """Join the line at end_idx with preceding lines until the office text
    contains a party suffix. Erie wraps "REPRESENTATIVE IN THE GENERAL
    ASSEMBLY 1ST LEGISLATIVE\\nDISTRICT-DEM (Vote for 1)" across two lines.

    Heuristic: strip "(Vote for N)" from the body, then check if the body
    (after stripping any party suffix) starts with a known office keyword.
    If not (e.g. just "DISTRICT"), prepend the previous non-empty line.
    """
    header = lines[end_idx]
    vote_for_match = VOTE_FOR_RE.search(header)
    if not vote_for_match:
        return header
    body = header[:vote_for_match.start()].rstrip()
    body_no_party = PARTY_SUFFIX_RE.sub("", body).strip()
    upper = body_no_party.upper()
    starts_with_office = any(upper == k or upper.startswith(k + " ")
                             for k in STATEWIDE_OFFICES)
    if starts_with_office:
        return body
    j = end_idx - 1
    while j >= 0 and not lines[j].strip():
        j -= 1
    if j < 0:
        return body
    prev = lines[j].strip()
    return prev + " " + body


def _merge_wrapped_candidates(lines: list[str]) -> list[str]:
    """Merge wrapped candidate name lines into the data row.

    Long candidate names wrap across two lines around the data row::

        DOMINIC CHAVES
                                           DEM           1,865     1,846            18      3,729
        GARDUNIO

    Becomes::

        DOMINIC CHAVES GARDUNIO   DEM           1,865     1,846            18      3,729
    """
    out: list[str] = []
    i = 0
    n = len(lines)
    party_alt = r"(?:DEM|REP|GP|LBR|IND|GRN|WEP|WFP|PGH|CON|WRITE-IN|NONPARTISAN|NON)"
    data_row_re = re.compile(rf"^\s+{party_alt}\s+\d[\d,]*\s+\d[\d,]*\s+\d[\d,]*\s+\d[\d,]*\s*$")
    while i < n:
        line = lines[i]
        # Name-only line at column 0 with no digits, followed by an indented
        # data row — start of a wrapped candidate.
        if (line and not line[0].isspace() and not re.search(r"\d", line)
            and i + 1 < n and data_row_re.match(lines[i + 1])):
            name_parts = [line.strip()]
            data_line = lines[i + 1]
            j = i + 2
            while j < n:
                nxt = lines[j]
                if (nxt and not nxt[0].isspace() and not re.search(r"\d", nxt)):
                    name_parts.append(nxt.strip())
                    j += 1
                else:
                    break
            m = re.match(rf"^\s+({party_alt})\s+(.*)$", data_line)
            if m:
                party = m.group(1)
                rest = m.group(2)
                out.append(f"{' '.join(name_parts)} {party} {rest}")
                i = j
                continue
        out.append(line)
        i += 1
    return out


def parse_summary(county: str, pdf_path: Path) -> list[dict]:
    proc = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    lines = _merge_wrapped_candidates(proc.stdout.split("\n"))
    rows: list[dict] = []
    # Aggregate write-in candidates into a single "Write-In Totals" row.
    writein_agg: dict[tuple, dict] = {}
    current_office = ""
    current_district = ""
    current_party = ""
    skip_block = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if VOTE_FOR_RE.search(line):
            header_text = _join_contest_header(lines, i)
            # Strip "(Vote for N)" — already removed by _join_contest_header.
            # Strip party suffix.
            psm = PARTY_SUFFIX_RE.search(header_text)
            if psm:
                party = psm.group(1).upper()
                office_text = PARTY_SUFFIX_RE.sub("", header_text).strip()
            else:
                # Member of Democratic/Republican State Committee has no
                # party suffix; party is in the office text itself.
                party = ""
                office_text = header_text.strip()
            # Skip per-precinct committee races.
            if PER_PRECINCT_COMMITTEE_RE.search(office_text):
                skip_block = True
                current_office = ""
                i += 1
                continue
            skip_block = False
            current_office, current_district = _normalize_office(office_text)
            # For State Committee contests (no party suffix), derive party
            # from office name.
            if not party:
                if "DEMOCRATIC" in office_text.upper():
                    party = "DEM"
                elif "REPUBLICAN" in office_text.upper():
                    party = "REP"
            current_party = party
            i += 1
            continue
        if skip_block or not current_office:
            i += 1
            continue
        # Skip banner / column-header / blank lines.
        s = line.strip()
        if not s:
            i += 1
            continue
        if re.match(r"^Page:\s*\d+\s+of\s+\d+", s, re.IGNORECASE):
            i += 1
            continue
        if re.match(r"^\d+/\d+/\d+\s+\d", s):
            i += 1
            continue
        if "Election Summary" in s or "Summary for:" in s:
            i += 1
            continue
        if "ERIE COUNTY" in s or "MAY 19, 2026" in s or "OFFICIAL ELECTION" in s:
            i += 1
            continue
        if re.match(r"^Elector Group", s, re.IGNORECASE):
            i += 1
            continue
        if re.match(r"^(Democratic|Republican|Total|Mail-In|Provisional|Election Day)\b", s, re.IGNORECASE):
            # elector-group table or column header
            i += 1
            continue
        if "Precincts Reported" in s or "Cards Cast" in s:
            i += 1
            continue
        if re.match(r"^Candidate\s+Party\b", s, re.IGNORECASE):
            i += 1
            continue
        if re.match(r"^(Election Day|Mail-In|Provisional|Total)\s*(Election Day|Mail-In|Provisional|Total)?\s*$", s, re.IGNORECASE):
            i += 1
            continue
        # Aggregate rows (Total Votes, Undervotes, Overvotes) — skip.
        am = AGG_ROW_RE.match(s)
        if am:
            i += 1
            continue
        cm = CANDIDATE_ROW_RE.match(s)
        if cm:
            name = cm.group(1).strip()
            party_col = cm.group(2).upper()
            ed = int(cm.group(3).replace(",", ""))
            mi = int(cm.group(4).replace(",", ""))
            pr = int(cm.group(5).replace(",", ""))
            total = int(cm.group(6).replace(",", ""))
            if party_col == "WRITE-IN":
                key = (current_office, current_district, current_party)
                agg = writein_agg.setdefault(key, {
                    "county": county, "office": current_office,
                    "district": current_district, "party": current_party,
                    "candidate": "Write-In Totals",
                    "votes": 0, "election_day": 0, "mail": 0, "provisional": 0,
                })
                agg["votes"] += total
                agg["election_day"] += ed
                agg["mail"] += mi
                agg["provisional"] += pr
                i += 1
                continue
            candidate = _finalize_candidate(name)
            rows.append({
                "county": county,
                "office": current_office,
                "district": current_district,
                "party": current_party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "mail": mi,
                "provisional": pr,
            })
            i += 1
            continue
        i += 1

    rows.extend(writein_agg.values())
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_summary("Erie", pdf_path)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    offices = len({(r["office"], r["district"]) for r in rows})
    print(f"Wrote {len(rows)} rows across {offices} contests to {out_path}")


if __name__ == "__main__":
    main(sys.argv)