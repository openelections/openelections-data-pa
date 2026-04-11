#!/usr/bin/env python3
"""
Wyoming County, PA 2025 General Election County Results Parser

Parses county-level totals from text exports like:
"Wyoming PA 2025-Municipal-Totals-v2.txt".

Usage:
    python pa_wyoming_general_2025_county_parser.py <input_txt> <output_csv> [county]
"""

import csv
import re
import sys


PARTY_CODES = {
    "DEM/REP",
    "REP/DEM",
    "DEM",
    "REP",
    "LIB",
    "LRB",
    "IND",
    "GRE",
    "CST",
    "FWD",
    "NON",
    "NPA",
    "D",
    "R",
    "I",
}

RETENTION_MAP = {
    "DONOHUE": "Supreme Court Retention Election Question - Donohue",
    "DOUGHERTY": "Supreme Court Retention Election Question - Dougherty",
    "WECHT": "Supreme Court Retention Election Question - Wecht",
    "BECK DUBOW": "Superior Court Retention Election Question - Beck Dubow",
    "WOJCIK": "Commonwealth Court Retention Election Question - Wojcik",
}


def smart_title(text):
    parts = re.split(r"(\s+)", text.strip())
    titled = []
    for part in parts:
        if not part or part.isspace():
            titled.append(part)
            continue
        if re.fullmatch(r"[IVX]+", part):
            titled.append(part)
        elif re.fullmatch(r"[A-Z]{1,4}", part):
            titled.append(part)
        else:
            subparts = part.split("-")
            titled_subparts = []
            for sub in subparts:
                if re.fullmatch(r"[A-Z]{1,4}", sub):
                    titled_subparts.append(sub)
                else:
                    titled_subparts.append(sub.capitalize())
            titled.append("-".join(titled_subparts))
    return "".join(titled)


def normalize_office(text):
    office = re.sub(r"\s+", " ", text.strip())
    office = re.sub(r"\b(\d+)\s+YEAR\b", r"\1 Year Term", office, flags=re.IGNORECASE)
    return smart_title(office)


def extract_retention_office(lines):
    for line in lines:
        if line.strip().lower().startswith("shall ") and "retained" in line.lower():
            match = re.search(r"Shall\s+(.+?)\s+be\s+retained", line, re.IGNORECASE)
            if not match:
                return None
            name = match.group(1).strip()
            mapped = RETENTION_MAP.get(name.upper())
            if mapped:
                return mapped
            return f"Judicial Retention Question - {smart_title(name)}"
    return None


def extract_office(lines):
    retention_office = extract_retention_office(lines)
    if retention_office:
        return retention_office

    vote_idx = None
    for i, line in enumerate(lines):
        if "VOTE FOR" in line.upper():
            vote_idx = i
            break
    if vote_idx is None:
        return None

    start = 0
    for i in range(vote_idx - 1, -1, -1):
        if "MUNICIPAL ELECTION" in lines[i].upper():
            start = i + 1
            break

    office_lines = []
    for i in range(start, vote_idx + 1):
        line = lines[i].strip()
        if not line:
            continue
        if "MUNICIPAL ELECTION" in line.upper():
            continue
        if re.search(r"\bNovember\b|\bMay\b|\b\d{4}\b", line):
            continue
        office_lines.append(line)

    if not office_lines:
        return None

    office_text = " ".join(office_lines)
    office_text = re.sub(r"\s*-?\s*VOTE\s+FOR.*$", "", office_text, flags=re.IGNORECASE)
    office_text = office_text.strip(" -")
    return normalize_office(office_text)


def should_skip_header_line(line):
    upper = line.upper()
    if "MUNICIPAL ELECTION" in upper:
        return True
    if "NOVEMBER" in upper or "MAY" in upper:
        return True
    if "VOTE FOR" in upper:
        return True
    if "CANDIDATES" in upper or "NAME" in upper:
        return True
    return False


def split_candidate_party(token):
    token = token.strip()
    if not token:
        return None, None

    upper = token.upper()
    if "SCATTERED" in upper or "WRITE" in upper:
        return "Write-in", ""
    if upper in {"YES", "NO"}:
        return upper.title(), ""

    if ";" in token:
        name_part, party_part = token.split(";", 1)
        party = party_part.strip().upper()
        name = smart_title(name_part.strip())
        return name, party if party in PARTY_CODES else ""

    match = re.match(r"^(.*?)(?:\s+-\s+|\s+)([A-Z/]+)\s*$", token)
    if match and match.group(2).upper() in PARTY_CODES:
        name = smart_title(match.group(1).strip())
        return name, match.group(2).upper()

    return smart_title(token), ""


def extract_candidates(lines):
    cand_idx = None
    for i, line in enumerate(lines):
        if re.search(r"Candidates\s+Name|CANDIDATES\b", line, re.IGNORECASE):
            cand_idx = i
            break
    if cand_idx is None:
        return None, None

    header_lines = lines[:cand_idx]
    candidates = []
    seen = set()

    for line in header_lines:
        if should_skip_header_line(line):
            continue
        parts = re.split(r"\s{2,}", line.strip())
        for part in parts:
            token = part.strip()
            if not token:
                continue
            name, party = split_candidate_party(token)
            if not name:
                continue
            key = (name, party)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"candidate": name, "party": party})

    return candidates, cand_idx


def extract_totals(lines, candidate_count):
    for line in lines:
        if re.match(r"^\s*TOTALS?\b", line, re.IGNORECASE):
            numbers = re.findall(r"\d[\d,]*", line)
            totals = [int(n.replace(",", "")) for n in numbers]
            if len(totals) >= candidate_count:
                return totals[:candidate_count]
    return None


def parse_text(text, county_name=None):
    county = county_name or "Wyoming"
    results = []

    sections = [s for s in text.split("\f") if s.strip()]
    for section in sections:
        lines = [line.rstrip() for line in section.splitlines() if line.strip()]
        if not lines:
            continue

        office = extract_office(lines)
        if not office:
            continue

        candidates, cand_idx = extract_candidates(lines)
        if not candidates:
            continue

        totals = extract_totals(lines, len(candidates))
        if not totals:
            continue

        for candidate, votes in zip(candidates, totals):
            results.append({
                "county": county,
                "office": office,
                "district": "",
                "party": candidate["party"],
                "candidate": candidate["candidate"],
                "votes": votes,
                "election_day": "",
                "mail": "",
                "provisional": "",
            })

    return results


def write_csv(results, output_path):
    fieldnames = [
        "county",
        "office",
        "district",
        "party",
        "candidate",
        "votes",
        "election_day",
        "mail",
        "provisional",
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)


def main():
    if len(sys.argv) < 3:
        print("Usage: python pa_wyoming_general_2025_county_parser.py <input_txt> <output_csv> [county]")
        sys.exit(1)

    input_txt = sys.argv[1]
    output_csv = sys.argv[2]
    county = sys.argv[3] if len(sys.argv) > 3 else None

    with open(input_txt, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    results = parse_text(text, county)

    write_csv(results, output_csv)
    print(f"Wrote {len(results)} records to {output_csv}")


if __name__ == "__main__":
    main()
