#!/usr/bin/env python3
import subprocess
import re
import sys
from pathlib import Path

def convert_pdf_to_text(pdf_path):
    """Convert PDF to text using pdftotext with layout preservation."""
    try:
        subprocess.run(['pdftotext', '-layout', pdf_path], check=True)
        return str(Path(pdf_path).with_suffix('.txt'))
    except subprocess.CalledProcessError as e:
        print(f"Error converting PDF to text: {e}", file=sys.stderr)
        sys.exit(1)

def process_text_file(text_path):
    """Process the text file according to specified patterns and filters."""
    try:
        with open(text_path, 'r', encoding='utf-8') as file:
            content = file.read()

        # Pattern replacements
        replacements = [
            (r'\f', ''),                    # Remove form feeds
            (r'   ', '\t'),                 # Replace three spaces with tab
            (r'\t ', '\t'),                 # Remove space after tab
            (r'\t\t+', '\t'),               # Replace multiple tabs with single tab
            (r'\n\t', '\n'),                # Remove tab after newline
            (r'\n\n+', '\n'),               # Replace multiple newlines with single newline
            (r'\n ', '\n'),                 # Remove space after newline
            (r'\n ', '\n'),                 # Remove space after newline
            (r' - Total', ''),              # Remove extraneous text
            (r'Write-In Totals', 'Write-ins'), # Replace write-in totals
            (r'Overvotes', 'Over Votes'),    # Replace overvotes
            (r'Undervotes', 'Under Votes'),  # Replace undervotes
            (r'PRESIDENTIAL ELECTORS','PRESIDENT'), # Replace presidential electors
            (r'UNITED STATES SENATOR', 'U.S. SENATE'), # Replace U.S. Senator
            (r', President', ''),            # Remove extraneous text
            (r'Registered Voters', 'REGISTERED VOTERS'), # Replace registered voters
            (r'Ballots Cast', 'BALLOTS CAST'), # Replace ballots cast
            (r'Ballots Cast - Blank', 'BALLOTS CAST - BLANK'), # Replace ballots cast - blank
        ]

        # Apply all replacements
        for pattern, replacement in replacements:
            content = re.sub(pattern, replacement, content)

        # Lines to filter out
        filter_patterns = [
            r'^Voter Turnout.*$',
            r'^Total Votes Cast.*$',
            r'^Contest Totals.*$',
            r'^Summary Results Report.*$',
            r'^2024 General Election.*$',
            r'2024 General$',
            r'^General Election.*$',
            r'^2024 GENERAL ELECTION.*$',
            r'^2024 General Presidential Election$',
            r'^November 5, 2024.*$',
            r'^Statistics.*$',
            r'^STATISTICS.*$',
            r'^Vote For.*$',
            r'^Precinct Summary.*$',
            r'^Report generated with Electionware.*$',
            r'^TOTAL.*$',
            r'^Election	 Mail/Absen.*$',
            r'^Election	Mail/Absen.*$',
            r'^Day	/Mail.*$',
            r'^Day	Mail-In$',
            r'^Day	al.*$',
            r'^Day	tee.*$',
            r'^Day	 tee.*$',
            r'^Day	In/Absentee.*$',
            r'^Day	Votes.*$',
            r'DAY	L VOTE$',
            r'^DAY	AL VOTE$',
            r'^Day$',
            r'^Mail-.*$',
            r'^In/Absentee.*$',
            r'^Mail/Absent.*$',
            r'^Mail Provision$',
            r'^Votes al Votes$',
            r'^Election	 Absentee/$',
            r'ELECTION	 PROVISIONA$',
            r'ELECTION	 PROVISION$',
            r'^ee$',
            r'^SCATTERED.*$',
            r'^SOLIDARITY PARTY.*$',
            r'^REGISTERED VOTERS - .*$',
            r'^BALLOTS CAST - [A-Z]+.*$',
            r'^Election	 Mail-.*$',
            r'^- 12/09/2024.*$',

        ]

        # Filter out unwanted lines
        filtered_lines = []
        for line in content.split('\n'):
            if not any(re.match(pattern, line) for pattern in filter_patterns):
                filtered_lines.append(line)

        # Write processed content back to file
        with open(text_path, 'w', encoding='utf-8') as file:
            file.write('\n'.join(filtered_lines))

    except Exception as e:
        print(f"Error processing text file: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    if len(sys.argv) != 2:
        print("Usage: python pdf_parser.py <pdf_file>", file=sys.stderr)
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not Path(pdf_path).exists():
        print(f"Error: File '{pdf_path}' not found", file=sys.stderr)
        sys.exit(1)

    text_path = convert_pdf_to_text(pdf_path)
    process_text_file(text_path)
    print(f"Processing complete. Output saved to: {text_path}")

if __name__ == '__main__':
    main()