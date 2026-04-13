"""Parser for Lancaster County PA 2025 Municipal Election precinct results.

Reads the "Precinct Results by Group" sheet from the county workbook. That
sheet is a long-format table with one row per (precinct, office, candidate,
voting group), with columns:

    Precinct | Office Name | Contest ID | Ballot Name | Choice ID | Party |
    Group    | Total

`Group` is one of "Election Day Voting", "Mail Voting", "Provisional Voting".
`Ballot Name` may be a candidate name or one of the meta labels
"Ballots Cast", "Over Votes", "Under Votes", "Write-In", "SCATTERED".

This parser pivots the data to the OpenElections wide format, collapsing the
three voting groups into `election_day`, `mail`, and `provisional` columns and
combining "Write-In" and "SCATTERED" rows into a single "Write-ins" row per
contest/precinct. Over/Under votes and the per-contest "Ballots Cast" rows
(which are zeroed in the source file) are dropped.

Usage:
    uv run python parsers/pa_lancaster_precinct_2025_results_parser.py \
        "<input.xlsx>" 2025/counties/20251104__pa__general__lancaster__precinct.csv
"""

import csv
import re
import sys
from collections import OrderedDict

import openpyxl


COUNTY = "Lancaster"
SHEET = "Precinct Results by Group"

PARTY_MAP = {
    "Democratic Party": "DEM",
    "Republican Party": "REP",
    "Libertarian Party": "LIB",
    "Green Party": "GRN",
    "Independent Party": "IND",
    "Liberal": "LBR",
    "No Party": "",
}

GROUP_KEY = {
    "Election Day Voting": "election_day",
    "Mail Voting": "mail",
    "Provisional Voting": "provisional",
}

META_SKIP = {"Ballots Cast", "Over Votes", "Under Votes"}
WRITEIN_LABELS = {"Write-In", "SCATTERED"}

SMALL_WORDS = {"of", "the", "and", "for", "a", "an", "in", "on", "to"}

ROMAN_TO_INT = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5",
                "VI": "6", "VII": "7", "VIII": "8", "IX": "9", "X": "10"}


def extract_district(office):
    """Pull a district identifier out of an office name and return
    (cleaned_office, district). Handles several Lancaster-specific patterns:

    - "MAGISTERIAL DISTRICT JUDGE 02-2-04" -> district "02-2-04"
    - "COUNCIL 1ST WARD ELIZABETHTOWN"     -> district "1"
    - "COUNCIL EAST WARD MOUNT JOY"        -> district "East"
    - "SCHOOL DIRECTOR OCTORARA - REGION II" -> district "2"
    """
    s = office

    # Magisterial district judge codes like "02-2-04".
    m = re.search(r"MAGISTERIAL DISTRICT JUDGE\s+(\d+(?:-\d+)+)", s, re.IGNORECASE)
    if m:
        code = m.group(1)
        s = (s[:m.start(1)] + s[m.end(1):]).strip()
        s = re.sub(r"\s{2,}", " ", s).rstrip(" -")
        return s, code

    # School director region: "... - REGION <ROMAN>" (optionally followed by
    # " - 2 Yr" or similar term suffix).
    m = re.search(
        r"\s*-\s*REGION\s+([IVXLCDM]+|\d+)\b",
        s, re.IGNORECASE,
    )
    if m:
        token = m.group(1).upper()
        district = ROMAN_TO_INT.get(token, token)
        s = s[:m.start()] + s[m.end():]
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s, district

    # Numeric ward: "... <N>(ST|ND|RD|TH) WARD ...".
    m = re.search(r"\b(\d+)(ST|ND|RD|TH)\s+WARD\b", s, re.IGNORECASE)
    if m:
        district = m.group(1).lstrip("0") or m.group(1)
        s = s[:m.start()] + s[m.end():]
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s, district

    # Named ward: "... <NAME> WARD ..." where NAME is a single capitalized word.
    m = re.search(r"\b([A-Z][A-Z]+)\s+WARD\b", s)
    if m and m.group(1).upper() not in {"THE"}:
        district = m.group(1).capitalize()
        s = s[:m.start()] + s[m.end():]
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s, district

    return s, ""


def _title_word(word):
    m = re.match(r"^(\d+)(ST|ND|RD|TH)$", word, re.IGNORECASE)
    if m:
        return m.group(1) + m.group(2).lower()
    if word.isupper() or word.islower():
        return word.capitalize()
    return word


def titleize(raw):
    s = re.sub(r"\s+", " ", raw).strip()
    out = []
    for i, w in enumerate(s.split(" ")):
        lw = w.lower()
        if i > 0 and lw in SMALL_WORDS:
            out.append(lw)
        else:
            out.append(_title_word(w))
    return " ".join(out)


def to_int(value):
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def main(xlsx_path, output_csv):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb[SHEET]

    # (precinct, office) -> ordered {(party, candidate): {breakdown: total}}
    contests = OrderedDict()
    precinct_order = []
    office_order = {}  # precinct -> ordered list of offices

    for row in ws.iter_rows(min_row=2, values_only=True):
        if row is None:
            continue
        precinct, office, _contest_id, ballot, _choice, party, group, total = row[:8]
        if precinct is None or office is None:
            continue
        precinct = str(precinct).strip()
        office = str(office).strip()
        if not ballot:
            continue
        ballot = str(ballot).strip()
        if ballot in META_SKIP:
            continue

        if ballot in WRITEIN_LABELS:
            candidate = "Write-ins"
            party_code = ""
        else:
            candidate = ballot
            party_code = PARTY_MAP.get(party, party or "")

        group_col = GROUP_KEY.get(group)
        if group_col is None:
            continue

        key = (precinct, office)
        if key not in contests:
            contests[key] = OrderedDict()
            if precinct not in office_order:
                office_order[precinct] = []
                precinct_order.append(precinct)
            office_order[precinct].append(office)

        cand_key = (party_code, candidate)
        rec = contests[key].get(cand_key)
        if rec is None:
            rec = {"election_day": 0, "mail": 0, "provisional": 0}
            contests[key][cand_key] = rec
        rec[group_col] += to_int(total)

    fieldnames = [
        "county", "precinct", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional", "vote_for",
    ]

    with open(output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for precinct in precinct_order:
            for office in office_order[precinct]:
                cand_map = contests[(precinct, office)]
                office_stripped, district = extract_district(office)
                office_clean = titleize(office_stripped)
                for (party_code, candidate), rec in cand_map.items():
                    votes = rec["election_day"] + rec["mail"] + rec["provisional"]
                    cand_clean = candidate if candidate == "Write-ins" else titleize(candidate)
                    w.writerow({
                        "county": COUNTY,
                        "precinct": precinct,
                        "office": office_clean,
                        "district": district,
                        "party": party_code,
                        "candidate": cand_clean,
                        "votes": votes,
                        "election_day": rec["election_day"],
                        "mail": rec["mail"],
                        "provisional": rec["provisional"],
                        "vote_for": 1,
                    })


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: pa_lancaster_precinct_2025_results_parser.py <input.xlsx> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
