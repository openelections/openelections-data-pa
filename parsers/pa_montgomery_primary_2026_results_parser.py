#!/usr/bin/env python3
"""Parse Montgomery County PA 2026 Primary precinct results.

Source: Montgomery County StatementOfVotesCastRPT 5.pdf — a 3,374-page
"Statement of Votes Cast" PDF. The first ~60 pages are a turnout report
by precinct (skipped). Contest results follow.

Layout (contest pages):

  * Office header at y~36 (horizontal, only on contest-start page):
    "Governor - DEM (Vote for 1)" or
    "Member of Democratic State Committee (Senate District 4) (Vote for 4)".

  * Rotated candidate name(s) at top of page, one per candidate column.
    Each column has a top-to-bottom display of "Last First (Party)" with
    the words stored character-reversed (e.g. ``ORIPAHS`` = ``SHAPIRO``).
    Party token appears as ``)MED(`` = ``(DEM)``.

  * Per-precinct data rows: "Election Day", "Mail-in", "Provisional",
    "Total" rows with vote counts in columns aligned under each
    candidate's rotated header. Write-in subcolumn and Total column
    also present.

Pages where the office header is a per-precinct committeeperson race
(``<Precinct> Democratic Committeeperson (Vote for 2)``) or a township
referendum are skipped.

Usage:
    uv run python parsers/pa_montgomery_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import natural_pdf as npdf

from electionware_primary_np import (
    PRIMARY_FIELDNAMES,
    STATEWIDE_OFFICES,
    _finalize_candidate,
)

# District ordinal — Montgomery writes "4th District" (lowercase ordinal,
# no CONGRESSIONAL/LEGISLATIVE word).
DISTRICT_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+"
    r"(?:CONGRESSIONAL\s+|LEGISLATIVE\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

# Horizontal office header at y~36 on contest-start pages, e.g.
# "Governor - DEM (Vote for 1)" / "Senator in the General Assembly 4th
# District - DEM (Vote for 1)".
OFFICE_HEADER_RE = re.compile(
    r"^(.+?)\s*-\s*(DEM|REP|NON|GP|GRN|LBR|CON)\s*\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)

# "Member of Democratic State Committee (Senate District 4) (Vote for 4)"
# or "Member of Republican State Committee (Vote for 19)".
STATE_COMMITTEE_RE = re.compile(
    r"^Member of (?:the\s+)?(Democratic|Republican) State Committee"
    r"(?:\s*\((?:Senate District\s+)?(\d+)\))?\s*\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)

# Per-precinct committeeperson races and referendums — skip.
SKIP_OFFICE_KEYWORDS = ("Committeeperson", "Referendum", "Question")

# Y-coordinate ranges (in PDF points) for the layout.
OFFICE_HEADER_Y_RANGE = (30, 50)
PAGE_HEADER_Y_MAX = 30            # timestamp / "Page N of N" at top=22
ROTATED_HEADER_Y_MAX = 130        # candidate name + party token at y<130
PRECINCT_ROW_Y_MIN = 170          # skip column header rows and county total

# X-coordinate ranges for the right (candidate votes) column. Single-
# candidate pages have the candidate at x~542 and write-in/total at x~632/724.
# Multi-candidate pages have candidates at varying x positions.
RIGHT_COL_X_MIN = 399
PRECINCT_NAME_X_MAX = 545  # precinct name + split (e.g. "Upper Providence Mingo 2")
ROW_LABEL_X_RANGE = (455, 545)    # Election Day / Mail-in / etc.
VOTES_X_OFFSET_TOL = 25           # votes column within ±25px of candidate name

# Reversed column-header words (after char-reversal) to ignore when
# extracting candidate names.
COLUMN_HEADER_WORDS = {
    "Votes", "Write-in", "Write-In", "Total", "Cast", "Times",
    "Registered", "Voters", "Unresolved",
}

PARTY_TOKENS = {"(DEM)", "(REP)", "(NON)", "(GP)", "(GRN)", "(LBR)", "(CON)"}
PARTY_NORMALIZE = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}

ROW_LABEL_WORDS = {"Election", "Day", "Mail-in", "Mail", "Provisional", "Total"}


def _reverse_text(s: str) -> str:
    return s[::-1]


def _is_int(s: str) -> bool:
    try:
        int(s.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def _extract_office_header(words: list[dict]) -> tuple[str, str, str] | None:
    """Return (office, district, party) from horizontal header at y~36."""
    header_words = [
        w for w in words
        if OFFICE_HEADER_Y_RANGE[0] <= w['top'] <= OFFICE_HEADER_Y_RANGE[1]
        and 10 < w['x0'] < 690
    ]
    if not header_words:
        return None
    header_words.sort(key=lambda w: w['x0'])
    line = " ".join(w['text'] for w in header_words)
    if any(k in line for k in SKIP_OFFICE_KEYWORDS):
        return ("", "", "")  # signal skip
    m = OFFICE_HEADER_RE.match(line)
    if m:
        office_raw = m.group(1).strip()
        party = m.group(2).upper()
        if party == "NON":
            party = "NON"
        return _normalize_office(office_raw, party)
    sm = STATE_COMMITTEE_RE.match(line)
    if sm:
        party = PARTY_NORMALIZE.get(sm.group(1).upper(), "")
        district = str(int(sm.group(2))) if sm.group(2) else ""
        return ("Member of " + sm.group(1).title() + " State Committee",
                district, party)
    return None


def _normalize_office(raw: str, party: str) -> tuple[str, str, str]:
    upper = raw.upper()
    dm = DISTRICT_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_RE.sub("", upper).strip() if dm else upper
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "", party)
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "", party)
    return (raw.title(), district, party)


def _extract_candidates(words: list[dict]) -> list[tuple[str, str, float]]:
    """Return list of (candidate, party, votes_x) tuples. ``votes_x`` is
    the x-coordinate of the vote-count column for this candidate.

    Each candidate column has a party token ``)XXX(`` at the bottom. Name
    parts are above the party token, at x within tolerance of the party
    token's x.
    """
    # The "Precinct" column header (at x~399) marks the start of the
    # precinct-data block. Rotated candidate-header words must be ABOVE
    # that line; otherwise we'd pull in vote counts from the first
    # precinct row (which on continuation pages can be at y<130).
    precinct_header_y = next(
        (w['top'] for w in words
         if w['text'] == 'Precinct' and 30 < w['top'] < 150
         and 380 < w['x0'] < 410),
        None,
    )
    if precinct_header_y is None:
        return []
    # If an office header is present at y~36 (horizontal text with
    # "(Vote for N)" or "State Committee"), exclude y<=50 to avoid
    # pulling office-header words into the candidate cluster.
    has_office_header = any(
        30 <= w['top'] <= 50 and 10 < w['x0'] < 690
        and '(' in w['text']
        for w in words
    )
    y_min = 55 if has_office_header else PAGE_HEADER_Y_MAX
    rot = [
        w for w in words
        if y_min < w['top'] < precinct_header_y - 2
        and 10 < w['x0'] < 700
    ]
    if not rot:
        return []
    # Identify party tokens — reversed text matches ``\([A-Z]{3}\)``.
    party_tokens = []
    for w in rot:
        text = _reverse_text(w['text'])
        if re.fullmatch(r"\([A-Z]{3}\)", text):
            party_tokens.append((w['x0'], w['top'], text))
    if not party_tokens:
        return []
    candidates = []
    for px, py, ptext in party_tokens:
        # Name words: same x-cluster (within 30px), anywhere in the
        # rotated-header y-range. The (DEM) token sits BETWEEN the
        # last and first name on some pages, so don't filter by y
        # relative to the party token.
        cluster = [
            w for w in rot
            if abs(w['x0'] - px) <= 30
            and _reverse_text(w['text']) not in PARTY_TOKENS
        ]
        cluster.sort(key=lambda w: w['top'])
        name_parts = []
        for w in cluster:
            text = _reverse_text(w['text'])
            if text in COLUMN_HEADER_WORDS:
                continue
            name_parts.append(text)
        if not name_parts:
            continue
        # Display order top-to-bottom: Last, [Middle...], First.
        # Fully reverse to get First, [Middle...], Last.
        if len(name_parts) >= 2:
            name_parts = name_parts[::-1]
        candidate = _finalize_candidate(" ".join(name_parts))
        party = ptext.strip("()").upper()
        # Votes column x: roughly aligned with candidate name x.
        votes_x = px
        candidates.append((candidate, party, votes_x))
    return candidates


def _extract_precinct_blocks(
    words: list[dict],
    candidate_columns: list[tuple[str, str, float]],
) -> list[dict]:
    """Return one block per precinct on this page.

    Each block is ``{"precinct": <name>, "candidates": {votes_x: {row_label: votes_int}}}``.
    """
    if not candidate_columns:
        return []
    # Group words by y-coordinate (within tolerance of 3 pts).
    lines: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        if w['top'] < PRECINCT_ROW_Y_MIN:
            continue
        lines[round(w['top'] / 3) * 3].append(w)

    blocks: list[dict] = []
    current: dict | None = None
    votes_xs = [c[2] for c in candidate_columns]

    for y in sorted(lines.keys()):
        ws = sorted(lines[y], key=lambda w: w['x0'])
        # Find precinct name words on right column (x in [399, 545)) with
        # no row-label words on this line.
        right_name_words = [
            w for w in ws
            if RIGHT_COL_X_MIN <= w['x0'] < PRECINCT_NAME_X_MAX
            and w['text'] not in ROW_LABEL_WORDS
        ]
        row_label = next(
            (w for w in ws
             if ROW_LABEL_X_RANGE[0] <= w['x0'] < ROW_LABEL_X_RANGE[1]
             and w['text'] in ROW_LABEL_WORDS),
            None,
        )
        if right_name_words and row_label is None:
            right_text = " ".join(w['text'] for w in right_name_words)
            # Skip contest-level summary section headers ("Cumulative")
            # and county-total lines — they're not precinct names.
            if any(w['text'] in ("Cumulative", "County") for w in right_name_words):
                if current:
                    blocks.append(current)
                    current = None
                continue
            # If the previous precinct block has no data rows yet, this
            # is a continuation of the precinct name (the name wrapped
            # across two lines, e.g. "Upper Providence Mont" + "Clare").
            if current and not any(
                rows for rows in current["candidates"].values()
            ):
                current["precinct"] = current["precinct"] + " " + right_text
                continue
            if current:
                blocks.append(current)
            current = {"precinct": right_text, "candidates": {}}
            for vx in votes_xs:
                current["candidates"][vx] = {}
            continue
        if row_label is None or current is None:
            continue
        # Skip contest-level summary rows ("Cumulative - Total" and
        # "County - Total" / "Montgomery County - Total") — they're not
        # precinct data and would overwrite the real precinct's Total.
        right_name_texts = " ".join(w['text'] for w in right_name_words)
        if any(w['text'] in ("Cumulative", "County") for w in right_name_words):
            continue
        # Normalize row label.
        if row_label['text'] == "Election":
            label = "Election Day"
        elif row_label['text'] == "Mail":
            label = "Mail-in"
        else:
            label = row_label['text']
        # Find vote counts for each candidate column. The vote-count
        # column is roughly aligned with the candidate's party token x,
        # but can be up to 20px to the left (e.g. token at x=558, votes
        # at x=544). Match to the closest candidate column.
        for w in ws:
            if w['x0'] < 400:
                continue
            if not _is_int(w['text']):
                continue
            val = int(w['text'].replace(",", ""))
            best_vx = None
            best_dx = 999
            for vx in votes_xs:
                dx = abs(w['x0'] - vx)
                if dx < best_dx:
                    best_dx = dx
                    best_vx = vx
            if best_vx is not None and best_dx <= 25:
                current["candidates"][best_vx][label] = val

    if current:
        blocks.append(current)
    return blocks


def parse_montgomery_pdf(pdf_path: Path) -> list[dict]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    current_office = ""
    current_district = ""
    current_party = ""

    for page in pdf.pages:
        pp = page._page
        words = pp.extract_words()
        # Update office from horizontal header if present.
        office_hdr = _extract_office_header(words)
        if office_hdr:
            current_office, current_district, current_party = office_hdr
        # Skip pages with no active office (initial pages, or after a
        # committeeperson/referendum skip signal).
        if not current_office:
            continue
        # Extract candidates from rotated header.
        candidates = _extract_candidates(words)
        if not candidates:
            continue
        # Extract precinct blocks.
        blocks = _extract_precinct_blocks(words, candidates)
        for blk in blocks:
            for cand_name, cand_party, votes_x in candidates:
                r = blk["candidates"].get(votes_x, {})
                total = r.get("Total", "")
                if total == "" and not any(r.values()):
                    continue
                ed = r.get("Election Day", "")
                mail = r.get("Mail-in", "")
                prov = r.get("Provisional", "")
                party = current_party or cand_party
                rows.append({
                    "county": "Montgomery",
                    "precinct": blk["precinct"],
                    "office": current_office,
                    "district": current_district,
                    "party": party,
                    "candidate": cand_name,
                    "votes": total,
                    "election_day": ed,
                    "provisional": prov,
                    "absentee": mail,
                })
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PRIMARY_FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in PRIMARY_FIELDNAMES})


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows = parse_montgomery_pdf(pdf_path)
    write_csv(rows, out_path)
    precincts = len({r["precinct"] for r in rows})
    offices = len({(r["office"], r["district"]) for r in rows})
    print(
        f"Wrote {len(rows)} rows across {offices} contests / "
        f"{precincts} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)