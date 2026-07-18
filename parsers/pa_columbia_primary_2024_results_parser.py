#!/usr/bin/env python3
"""Parse Columbia County PA 2024 Primary precinct results.

Source: Columbia PA SOVC_May_23_2024.pdf (crosstab format via pdftotext -layout).
Up to two contests appear side by side per page; each contest has a
"Reg. Total <cand1> <cand2> ... Write-in" column header followed by precinct
rows of the form ``<precinct> <reg> <total> <v1> <%1> <v2> <%2> ...``.
The first contest (DEM President) additionally has a Turnout column block
(reg/ballots/turnout%/reg_dup) before the contest's own metadata.

Candidate names are hard-coded per contest because the multi-line wrapped
candidate headers are unreliable to parse positionally.
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path


OFFICE_MAP = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
}

SKIP_OFFICE_PREFIXES = (
    "DELEGATE TO THE",
    "ALTERNATE DELEGATE",
)

OFFICE_HEADER_RE = re.compile(
    "(PRESIDENT OF THE UNITED STATES|UNITED STATES SENATOR|ATTORNEY GENERAL"
    "|AUDITOR GENERAL|STATE TREASURER|REPRESENTATIVE IN CONGRESS"
    "|SENATOR IN THE GENERAL ASSEMBLY|REPRESENTATIVE IN THE GENERAL ASSEMBLY"
    "|DELEGATE TO THE)"
)

DISTRICT_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT\b", re.IGNORECASE)
DISTRICT_SUFFIX_RE = re.compile(r"\s+(\d+)(?:ST|ND|RD|TH)?\s*$")
PARTY_RE = re.compile(r"\((DEMOCRATIC|REPUBLICAN)\)")
PCT_TOKEN_RE = re.compile(r"^[\d.]+%$|^-$")
NUM_TOKEN_RE = re.compile(r"^[\d,]+$")
DATA_RE = re.compile(r"^\s*([A-Z][A-Z0-9.\-/ ]+?)\s+(\d[\d,]*)\s+.*$")

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "provisional", "absentee",
]

SKIP_PREFIXES = (
    "Statement of Votes Cast",
    "COLUMBIA COUNTY",
    "PENNSYLVANIA",
    "GENERAL PRIMARY",
    "RESULTS",
    "Date:",
    "Time:",
    "Jurisdiction Wide",
)

# Hard-coded candidate lists per (office, district, party).
# Last entry is always "Write-In Totals".
CANDIDATES = {
    ("President", "", "DEM"): [
        "Joseph R Biden Jr", "Dean Phillips", "Write-In Totals",
    ],
    ("President", "", "REP"): [
        "Nikki R Haley", "Donald J Trump", "Write-In Totals",
    ],
    ("U.S. Senate", "", "DEM"): [
        "Robert P Casey Jr", "Write-In Totals",
    ],
    ("U.S. Senate", "", "REP"): [
        "Dave McCormick", "Write-In Totals",
    ],
    ("Attorney General", "", "DEM"): [
        "Jack Stollsteimer", "Eugene DePasquale", "Joe Khan",
        "Keir Bradford-Grey", "Jared Solomon", "Write-In Totals",
    ],
    ("Attorney General", "", "REP"): [
        "Dave Sunday", "Craig Williams", "Write-In Totals",
    ],
    ("Auditor General", "", "DEM"): [
        "Malcolm Kenyatta", "Mark Pinsley", "Write-In Totals",
    ],
    ("Auditor General", "", "REP"): [
        "Tim DeFoor", "Write-In Totals",
    ],
    ("State Treasurer", "", "DEM"): [
        "Ryan Bizzarro", "Erin McClelland", "Write-In Totals",
    ],
    ("State Treasurer", "", "REP"): [
        "Stacy Garrity", "Write-In Totals",
    ],
    ("U.S. House", "9", "DEM"): [
        "Amanda Waldman", "Write-In Totals",
    ],
    ("U.S. House", "9", "REP"): [
        "Dan Meuser", "Write-In Totals",
    ],
    ("State Senate", "27", "DEM"): [
        "Patricia Lawton", "Write-In Totals",
    ],
    ("State Senate", "27", "REP"): [
        "Lynda J Schlegel Culver", "Write-In Totals",
    ],
    ("State House", "109", "DEM"): [
        "Nick McGaw", "Write-In Totals",
    ],
    ("State House", "109", "REP"): [
        "Matt Yoder", "Robert Leadbeter", "Write-In Totals",
    ],
}


def extract_text(pdf_path: Path) -> str:
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), tmp_path],
            check=True, capture_output=True,
        )
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def normalize_office(raw: str) -> tuple[str, str]:
    upper = re.sub(r"\s+", " ", raw.upper()).strip()
    district = ""
    dm = DISTRICT_RE.search(upper)
    if dm:
        district = str(int(dm.group(1)))
        upper = DISTRICT_RE.sub("", upper).strip()
    else:
        ds = DISTRICT_SUFFIX_RE.search(upper)
        if ds:
            district = str(int(ds.group(1)))
            upper = DISTRICT_SUFFIX_RE.sub("", upper).strip()
    for key, (norm, extract) in OFFICE_MAP.items():
        if upper.startswith(key):
            return (norm, district if extract else "")
    return (raw.title(), district)


def _is_data_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.upper() == "TOTAL" or stripped.startswith("Total "):
        return False
    return bool(DATA_RE.match(stripped))


def _collect_contest_header(lines: list[str], start_idx: int) -> tuple[list[str], int]:
    header_lines = []
    i = start_idx
    header_lines.append(lines[i])
    i += 1
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if s == "Jurisdiction Wide":
            break
        if _is_data_line(lines[i]):
            break
        if s.startswith("Total "):
            break
        header_lines.append(lines[i])
        i += 1
    return header_lines, i


def _find_reg_positions(header_lines: list[str]) -> list[int]:
    for hl in header_lines:
        if "Reg." in hl:
            positions = [m.start() for m in re.finditer(r"Reg\.", hl)]
            # Filter out Turnout blocks: a "Reg." column whose header text
            # contains "Ballots" / "Cast" / "Turnout" (the turnout metadata
            # block) is not a real contest. The first contest (DEM President)
            # has a Turnout block on the left of the real contest.
            real = []
            for pos in positions:
                # determine the column slice end (next Reg. or end of line)
                next_pos = min([p for p in positions if p > pos], default=len(hl))
                sub = hl[pos:next_pos]
                # also look at the line below (Voters line) if present
                if "Ballots" in sub or "Cast" in sub or "Turnout" in sub:
                    continue
                real.append(pos)
            return real
    return []


def _infer_party(office: str, district: str, cand_frags: str) -> str:
    """When the party label is missing from the contest header, infer it by
    matching candidate last names from the column header against the DEM and
    REP candidate lists. Returns "DEM", "REP", or "" if no match."""
    if not cand_frags:
        return ""
    frags_upper = cand_frags.upper()
    best_party = ""
    best_score = 0
    for party in ("DEM", "REP"):
        cands = CANDIDATES.get((office, district, party))
        if not cands:
            continue
        score = 0
        for cand in cands:
            if cand == "Write-In Totals":
                continue
            # match the last token of the candidate name (the surname)
            last = cand.split()[-1].upper()
            if last and last in frags_upper:
                score += 1
        if score > best_score:
            best_score = score
            best_party = party
    return best_party


def _detect_contest_at_col(header_lines: list[str], col: int, end_col: int) -> tuple[str, str, str]:
    """Detect (office_text, party, full_office_text) for the contest at column range [col, end_col).
    Scans header lines for office keywords and party markers within the column slice."""
    office_frags: list[str] = []
    candidate_frags: list[str] = []
    party = ""
    seen_reg = False
    for hl in header_lines:
        if len(hl) <= col:
            continue
        sub = hl[col:end_col].strip()
        if not sub:
            continue
        if "Reg." in sub:
            seen_reg = True
            candidate_frags.append(sub)
            continue
        if seen_reg:
            candidate_frags.append(sub)
            continue
        # above Reg. line — office text or party
        pm = PARTY_RE.search(sub)
        if pm and not party:
            party = "DEM" if "DEMOCRATIC" in pm.group(1) else "REP"
            sub_no_party = PARTY_RE.sub("", sub).strip()
            if sub_no_party and (OFFICE_HEADER_RE.search(sub_no_party) or _starts_with_office_key(sub_no_party)):
                office_frags.append(sub_no_party)
            continue
        if OFFICE_HEADER_RE.search(sub) or _starts_with_office_key(sub):
            office_frags.append(sub)
        elif sub == sub.upper() and sub not in ("",):
            # Continuation of office text — may include digits (e.g. "ASSEMBLY 109TH").
            if office_frags:
                office_frags.append(sub)
    office_text = " ".join(office_frags).strip()
    return (office_text, party, " ".join(candidate_frags))


def _starts_with_office_key(s: str) -> bool:
    upper = s.upper()
    for key in OFFICE_MAP:
        if upper.startswith(key) or key.startswith(upper):
            return True
    return False


def _parse_data_row_line(line: str, reg_positions: list[int]) -> tuple[str, list[list[str]]]:
    # Require two consecutive pure-number tokens after the precinct name to
    # avoid matching digits inside the precinct name (e.g. "BERWICK 1ST WARD").
    m = re.match(r"^(\s*.+?)\s+(\d[\d,]*)\s+(\d[\d,]*)", line)
    if not m:
        return ("", [])
    precinct = m.group(1).strip()
    first_digit_pos = m.start(2)
    per_contest = []
    for ci, col in enumerate(reg_positions):
        end_col = reg_positions[ci + 1] if ci + 1 < len(reg_positions) else len(line)
        start = max(col, first_digit_pos) if ci == 0 else col
        sub = line[start:end_col].strip()
        toks = sub.split() if sub else []
        # Drop stray "%" tokens (these appear when a Turnout-block % token
        # straddles the slice boundary).
        toks = [t for t in toks if t != "%"]
        per_contest.append(toks)
    return (precinct, per_contest)


def _parse_contest_tokens(toks: list[str], candidates: list[str]) -> list[tuple[str, int]]:
    if not candidates or not toks:
        return []
    n_cands = len(candidates)
    # Structure after column slicing: [reg_voters, total, v1, %1, v2, %2, ...]
    # i.e. 2 metadata + 2*N candidate tokens.
    if len(toks) < 2 + 2 * n_cands:
        return []
    pairs = toks[2:2 + 2 * n_cands]
    out = []
    for i, cand in enumerate(candidates):
        v = pairs[2 * i]
        votes = 0 if v == "-" else int(v.replace(",", ""))
        out.append((cand, votes))
    return out


def parse_columbia(text: str) -> list[dict]:
    rows: list[dict] = []
    lines = text.split("\n")
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if any(stripped.startswith(p) for p in SKIP_PREFIXES):
            i += 1
            continue
        if OFFICE_HEADER_RE.search(line):
            header_lines, end_idx = _collect_contest_header(lines, i)
            reg_positions = _find_reg_positions(header_lines)
            if not reg_positions:
                i = end_idx
                continue
            contest_keys: list[tuple[str, str, str] | None] = []
            for ci, col in enumerate(reg_positions):
                end_col = reg_positions[ci + 1] if ci + 1 < len(reg_positions) else max(
                    (len(hl) for hl in header_lines), default=col + 1
                )
                office_text, party, cand_frags = _detect_contest_at_col(header_lines, col, end_col)
                if not office_text:
                    contest_keys.append(None)
                    continue
                if any(office_text.upper().startswith(p) for p in SKIP_OFFICE_PREFIXES):
                    contest_keys.append(None)
                    continue
                office, district = normalize_office(office_text)
                # If party is missing (office name wrapped onto the party line),
                # infer by matching candidate last names from the column header
                # against the DEM and REP candidate lists for this office/district.
                if not party:
                    party = _infer_party(office, district, cand_frags)
                if not party:
                    contest_keys.append(None)
                    continue
                contest_keys.append((office, district, party))
            contest_cands = [CANDIDATES.get(k) if k else None for k in contest_keys]
            if not any(contest_cands):
                i = end_idx
                continue
            j = end_idx
            while j < n:
                s = lines[j].strip()
                if not s:
                    j += 1
                    continue
                if s.startswith("Total ") or s == "Total":
                    break
                if OFFICE_HEADER_RE.search(lines[j]):
                    break
                if any(s.startswith(p) for p in SKIP_PREFIXES):
                    j += 1
                    continue
                if s == "Jurisdiction Wide":
                    j += 1
                    continue
                precinct, per_contest = _parse_data_row_line(lines[j], reg_positions)
                if not precinct or not per_contest:
                    j += 1
                    continue
                if precinct.upper() == "TOTAL":
                    j += 1
                    continue
                for ci, toks in enumerate(per_contest):
                    cands = contest_cands[ci] if ci < len(contest_cands) else None
                    if not cands:
                        continue
                    ck = contest_keys[ci]
                    co, cd, cp = ck
                    pairs = _parse_contest_tokens(toks, cands)
                    for cand_name, votes in pairs:
                        rows.append({
                            "county": "Columbia", "precinct": precinct,
                            "office": co, "district": cd,
                            "party": cp, "candidate": cand_name,
                            "votes": votes, "election_day": "", "provisional": "", "absentee": "",
                        })
                j += 1
            i = j
            continue
        i += 1
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    text = extract_text(pdf_path)
    rows = parse_columbia(text)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)