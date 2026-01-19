#!/usr/bin/env python3
"""
Perry County, PA 2025 General Election Precinct Results Parser

Parses "Statement of Votes Cast by Geography" PDFs that list precinct-level
results with ED/MI/PR breakdowns.

Usage:
    python pa_perry_general_2025_precinct_parser.py <input_pdf> <output_csv> [county]
"""

import csv
import re
import sys
import subprocess


def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using pdftotext -layout."""
    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def clean_number(value):
    if value is None:
        return ""
    return value.replace(",", "").strip()


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
            # Handle hyphenated words
            subparts = part.split("-")
            titled_subparts = []
            for sub in subparts:
                if re.fullmatch(r"[A-Z]{1,4}", sub):
                    titled_subparts.append(sub)
                else:
                    titled_subparts.append(sub.capitalize())
            titled.append("-".join(titled_subparts))
    return "".join(titled)


def normalize_office(office_text):
    office = re.sub(r"\s+", " ", office_text.strip())
    upper = office.upper()

    retention_map = {
        "DONOHUE": "Supreme Court Retention Election Question - Donohue",
        "DOUGHERTY": "Supreme Court Retention Election Question - Dougherty",
        "WECHT": "Supreme Court Retention Election Question - Wecht",
        "BECK DUBOW": "Superior Court Retention Election Question - Beck Dubow",
        "WOJCIK": "Commonwealth Court Retention Election Question - Wojcik",
    }

    if upper.startswith("RETENTION "):
        name = office[10:].strip()
        mapped = retention_map.get(name.upper())
        return mapped if mapped else f"Judicial Retention Question - {smart_title(name)}"

    if upper == "COUNTY SHERIFF":
        office = "Sheriff"

    office = re.sub(r"\b(\d+)\s+YEAR\b", r"\1 Year Term", office, flags=re.IGNORECASE)
    return smart_title(office)


def extract_district(office_text):
    match = re.search(r"(\d+-\d+-\d+|REG\s+([IVX]+|\d+)|(\d+)(?:ST|ND|RD|TH)\s+WARD)", office_text, re.IGNORECASE)
    if not match:
        return ""
    if match.group(2):
        return f"Region {match.group(2).upper()}"
    if match.group(3):
        return match.group(3)
    return match.group(1)


def split_precinct_prefix(office_text):
    keywords = [
        "JUDGE OF THE SUPERIOR COURT",
        "JUDGE OF THE COMMONWEALTH COURT",
        "JUDGE OF THE COURT OF COMMON PLEAS",
        "MAGISTERIAL DISTRICT JUDGE",
        "DISTRICT JUDGE",
        "COUNTY SHERIFF",
        "SHERIFF",
        "CORONER",
        "PROTHONOTARY",
        "DISTRICT ATTORNEY",
        "REGISTER OF WILLS",
        "RECORDER OF DEEDS",
        "COMMISSIONER",
        "TREASURER",
        "SUPERVISOR",
        "AUDITOR",
        "TAX COLLECTOR",
        "CONSTABLE",
        "JUDGE OF ELECTION",
        "INSPECTOR OF ELECTION",
        "SCHOOL DIRECTOR",
        "COUNCIL",
        "MAYOR",
        "RETENTION",
    ]
    upper = office_text.upper()
    best = None
    for keyword in keywords:
        idx = upper.find(keyword)
        if idx > 0 and (best is None or idx < best[0]):
            best = (idx, keyword)
    if best:
        idx = best[0]
        precinct_prefix = office_text[:idx].strip()
        office_part = office_text[idx:].strip()
        return precinct_prefix, office_part
    return None, office_text


def is_local_office(office_text):
    local_keywords = [
        "SUPERVISOR",
        "AUDITOR",
        "TAX COLLECTOR",
        "CONSTABLE",
        "JUDGE OF ELECTION",
        "INSPECTOR OF ELECTION",
        "COUNCIL",
        "MAYOR",
        "SCHOOL DIRECTOR",
    ]
    upper = office_text.upper()
    return any(keyword in upper for keyword in local_keywords)


def parse_results(text, county_name=None):
    results = []
    lines = text.split("\n")

    county = county_name
    current_precinct = None
    current_office = None
    current_district = ""
    in_contest = False
    precinct_stats_added = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            continue

        if "Statement of Votes Cast by Geography" in stripped:
            continue
        if stripped.startswith("All Precincts"):
            continue
        if stripped.startswith("Choice") and "Votes" in stripped:
            continue
        if stripped.startswith("Total Ballots Cast:"):
            continue
        if stripped.startswith("Page:"):
            continue

        if county is None:
            county_match = re.search(r"([A-Za-z]+)\s+County", stripped)
            if county_match:
                county = county_match.group(1).title()

        precinct_match = re.match(r"^Precinct\s+(.+)$", stripped, re.IGNORECASE)
        if precinct_match:
            current_precinct = smart_title(precinct_match.group(1))
            precinct_stats_added = False
            in_contest = False
            continue

        stats_match = re.search(r"([\d,]+)\s+ballots.*?,\s*([\d,]+)\s+registered\s+voters", stripped, re.IGNORECASE)
        if stats_match and current_precinct and not precinct_stats_added:
            ballots_cast = clean_number(stats_match.group(1))
            registered_voters = clean_number(stats_match.group(2))

            results.append({
                "county": county,
                "precinct": current_precinct,
                "office": "Registered Voters",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": registered_voters,
                "election_day": "",
                "mail": "",
                "provisional": "",
            })
            results.append({
                "county": county,
                "precinct": current_precinct,
                "office": "Ballots Cast",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": ballots_cast,
                "election_day": "",
                "mail": "",
                "provisional": "",
            })
            precinct_stats_added = True
            continue

        office_match = re.match(r"^(.*)\(Vote for \d+\)\s*$", stripped)
        if office_match:
            office_text = office_match.group(1).strip()
            prefix, office_text = split_precinct_prefix(office_text)

            if prefix:
                if current_precinct is None or prefix.upper() not in current_precinct.upper():
                    current_precinct = smart_title(prefix)
                if is_local_office(office_text):
                    office_text = f"{prefix} {office_text}"
            current_office = normalize_office(office_text)
            current_district = extract_district(office_text)
            in_contest = True
            continue

        if not (in_contest and current_office and current_precinct):
            continue

        candidate_match = re.match(
            r"^\s*([A-Za-z][A-Za-z0-9\s\.,'\/\-]+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$",
            line,
        )
        if not candidate_match:
            continue

        candidate = candidate_match.group(1).strip()
        if candidate.lower() in {"total", "overvotes", "undervotes"}:
            continue

        votes = clean_number(candidate_match.group(2))
        ed_votes = clean_number(candidate_match.group(3))
        mail_votes = clean_number(candidate_match.group(4))
        prov_votes = clean_number(candidate_match.group(5))

        results.append({
            "county": county,
            "precinct": current_precinct,
            "office": current_office,
            "district": current_district,
            "party": "",
            "candidate": smart_title(candidate),
            "votes": votes,
            "election_day": ed_votes,
            "mail": mail_votes,
            "provisional": prov_votes,
        })

    return results


def write_csv(results, output_path):
    fieldnames = [
        "county",
        "precinct",
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
        print("Usage: python pa_perry_general_2025_precinct_parser.py <input_pdf> <output_csv> [county]")
        sys.exit(1)

    input_pdf = sys.argv[1]
    output_csv = sys.argv[2]
    county = sys.argv[3] if len(sys.argv) > 3 else None

    print(f"Extracting text from {input_pdf}...")
    text = extract_text_from_pdf(input_pdf)

    print("Parsing results...")
    results = parse_results(text, county)

    print(f"Writing {len(results)} records to {output_csv}...")
    write_csv(results, output_csv)

    print("Done!")


if __name__ == "__main__":
    main()
