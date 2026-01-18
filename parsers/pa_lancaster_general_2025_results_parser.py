#!/usr/bin/env python3
"""
Parser for Lancaster County, PA 2025 General Election Results.
Converts county-level PDF summary report to OpenElections CSV format.

Input: PDF file (Summary Results Report)
Output: CSV file with county-level results

Usage:
    python pa_lancaster_general_2025_results_parser.py input.pdf output.csv
"""

import csv
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple


class LancasterCountyParser:
    """Parse Lancaster County election summary report."""

    def __init__(self):
        self.county = "Lancaster"
        self.results = []
        self.current_office = None
        self.current_district = ""

    def extract_text_from_pdf(self, input_pdf: str) -> str:
        """Extract text from PDF using pdftotext -layout."""
        result = subprocess.run(
            ["pdftotext", "-layout", input_pdf, "-"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout

    def parse_text(self, text: str):
        """Parse the extracted text content."""
        registered_voters = self.extract_registered_voters(text)
        if registered_voters is not None:
            self.add_simple_row("Registered Voters", registered_voters)

        ballots_cast = self.extract_ballots_cast(text)
        if ballots_cast is not None:
            self.add_simple_row("Ballots Cast", ballots_cast)

        lines = self.normalize_lines(text)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if self.is_office_header(line):
                office, district = self.parse_office_header(line)
                self.current_office = office
                self.current_district = district
                continue

            if self.is_ignored_line(line):
                continue

            if not self.current_office:
                continue

            parsed = self.parse_candidate_line(line)
            if not parsed:
                continue

            candidate, party, totals = parsed
            if candidate in {"Total Votes Cast", "Total Ballots Cast"}:
                continue

            self.add_result(candidate, party, totals)

    def normalize_lines(self, text: str) -> List[str]:
        """Normalize text lines and merge wrapped headers."""
        raw_lines = text.split("\n")
        merged_lines = []
        i = 0
        while i < len(raw_lines):
            line = raw_lines[i].rstrip()
            next_line = raw_lines[i + 1].strip() if i + 1 < len(raw_lines) else ""

            # Merge broken header lines like "Summary Re" + "sults Report"
            if re.search(r"\bRe$", line) and next_line.startswith("sults Report"):
                merged_lines.append(f"{line.strip()}{next_line}")
                i += 2
                continue

            # Merge wrapped retention/question headers split across lines
            if (
                re.search(r"Retention Election|Election Question", line, re.IGNORECASE)
                and next_line
                and not next_line.startswith("Precincts Reported")
            ):
                merged_lines.append(f"{line.strip()} {next_line}")
                i += 2
                continue

            merged_lines.append(line)
            i += 1

        return merged_lines

    def is_office_header(self, line: str) -> bool:
        """Detect office header lines (uppercase office names)."""
        if not line:
            return False

        if re.search(r"Retention Election|Retention Election Question|Election Question", line, re.IGNORECASE):
            return True

        if not line.isupper():
            return False

        if re.search(r"\d", line):
            office_keywords = [
                "JUDGE", "DISTRICT", "WARD", "SCHOOL", "TOWNSHIP", "BOROUGH",
                "CITY", "COUNTY", "RECORDER", "CONTROLLER", "TREASURER",
                "COMMISSIONER", "SUPERVISOR", "MAYOR", "DIRECTOR", "CONSTABLE",
                "AUDITOR", "TAX", "ELECTION", "PROTHONOTARY", "CLERK", "SHERIFF",
                "SURVEYOR"
            ]
            if not any(keyword in line for keyword in office_keywords):
                return False

        if line in {"STATISTICS"}:
            return False

        if "SUMMARY RESULTS REPORT" in line:
            return False

        if "GENERAL ELECTION" in line:
            return False

        if line.startswith("LANCASTER COUNTY"):
            return False

        if line.startswith("NOVEMBER"):
            return False

        if line.startswith("PRECINCTS REPORTED"):
            return False

        if line.startswith("TOTAL"):
            return False

        return True

    def parse_office_header(self, line: str) -> Tuple[str, str]:
        """Parse office header line and extract district if present."""
        office_text = self.to_title_case(line)
        district = ""

        mag_match = re.search(r"Magisterial District Judge\s+(\d+-\d+-\d+)", office_text, re.IGNORECASE)
        if mag_match:
            district = mag_match.group(1)
            office_text = re.sub(r"\s+\d+-\d+-\d+", "", office_text)

        return office_text.strip(), district

    def is_ignored_line(self, line: str) -> bool:
        """Skip headers and metadata lines."""
        if line.startswith("Precincts Reported"):
            return True
        if line.startswith("2025 General Election"):
            return True
        if line.startswith("Last Updated"):
            return True
        if line.startswith("Statistics"):
            return True
        if line.startswith("Votes Cast"):
            return True
        if line.startswith("Ballots Cast"):
            return True
        if line.startswith("Voter Registration"):
            return True
        if line.startswith("Voter Turnout"):
            return True
        if line.startswith("TOTAL") and "Election Day" in line:
            return True
        if line.startswith("TOTAL") and "Mail Voting" in line:
            return True
        return False

    def parse_candidate_line(self, line: str) -> Optional[Tuple[str, str, Dict[str, int]]]:
        """Parse a candidate/result line into name, party, and vote totals."""
        parts = re.split(r"\s{2,}", line)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            return None

        # Collect last 4 numeric columns
        numeric = []
        idx = len(parts) - 1
        while idx >= 0 and len(numeric) < 4:
            if re.fullmatch(r"[\d,]+", parts[idx]):
                numeric.append(int(parts[idx].replace(",", "")))
                idx -= 1
            else:
                break

        if len(numeric) != 4:
            return None

        numeric = list(reversed(numeric))
        candidate_label = " ".join(parts[:idx + 1]).strip()
        if not candidate_label:
            return None

        candidate, party = self.parse_candidate_and_party(candidate_label)

        totals = {
            "votes": numeric[0],
            "election_day": numeric[1],
            "mail": numeric[2],
            "provisional": numeric[3]
        }

        return candidate, party, totals

    def parse_candidate_and_party(self, label: str) -> Tuple[str, str]:
        """Extract candidate name and party code from label."""
        if "(" in label and ")" in label:
            name = label.split("(")[0].strip()
            party_text = label[label.find("(") + 1:label.rfind(")")].strip()
            party = self.normalize_party(party_text)
            return name, party

        return label.strip(), ""

    def normalize_party(self, party_text: str) -> str:
        """Normalize party text to party code."""
        party_text = party_text.lower()
        party_map = {
            "democratic party": "DEM",
            "republican party": "REP",
            "liberal": "LBR",
            "libertarian": "LIB",
            "green": "GRN",
            "independent": "IND",
            "constitution": "CST"
        }
        for key, val in party_map.items():
            if key in party_text:
                return val
        return ""

    def to_title_case(self, text: str) -> str:
        """Convert ALL CAPS office names to title case with minor fixes."""
        title = text.title()
        for word in [" Of ", " The ", " For ", " And "]:
            title = title.replace(word, word.lower())
        return title

    def add_simple_row(self, label: str, total: int):
        """Add a simple stats row with only total votes filled."""
        self.results.append({
            "county": self.county,
            "office": "",
            "district": "",
            "party": "",
            "candidate": label,
            "votes": total,
            "election_day": "",
            "mail": "",
            "provisional": ""
        })

    def add_result(self, candidate: str, party: str, totals: Dict[str, int]):
        """Add a parsed result to the results list."""
        row = {
            "county": self.county,
            "office": self.current_office,
            "district": self.current_district,
            "party": party,
            "candidate": candidate,
            "votes": totals["votes"],
            "election_day": totals["election_day"],
            "mail": totals["mail"],
            "provisional": totals["provisional"]
        }
        self.results.append(row)

    def extract_registered_voters(self, text: str) -> Optional[int]:
        """Extract registered voters total from statistics section."""
        match = re.search(r"Voter Registration\s+([\d,]+)", text)
        if match:
            return int(match.group(1).replace(",", ""))
        return None

    def extract_ballots_cast(self, text: str) -> Optional[int]:
        """Extract ballots cast total from statistics section."""
        match = re.search(r"Ballots Cast\s+([\d,]+)", text)
        if match:
            return int(match.group(1).replace(",", ""))
        return None

    def write_csv(self, output_file: str):
        """Write results to CSV file."""
        fieldnames = [
            "county", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional"
        ]

        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.results)

        print(f"Wrote {len(self.results)} rows to {output_file}")


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_lancaster_general_2025_results_parser.py input.pdf output.csv")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2]

    parser = LancasterCountyParser()
    text = parser.extract_text_from_pdf(input_file)
    parser.parse_text(text)
    parser.write_csv(output_file)


if __name__ == "__main__":
    main()
