#!/usr/bin/env python3
"""LLM-based parser for PA county election PDF results
Uses llm library to extract structured data from PDF text
Supports any Pennsylvania county election report
"""

import pdfplumber
import llm
import csv
import sys
import os
import json


def extract_pdf_text(pdf_path):
    """Extract all text from PDF pages"""
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                pages_text.append({
                    'page_num': page_num,
                    'text': text
                })
    return pages_text


def create_extraction_prompt(page_text, page_num, county_name):
    """Create prompt for Claude to extract election results"""
    return f"""Extract election results from this {county_name} County, PA election report page.

The text below is from page {page_num} of a PDF with a two-column layout. Please extract ALL races, candidates, and ballot questions, parsing both columns.

For each candidate/choice, provide:
- county (the county name, e.g., "{county_name}")
- office (the position being elected or ballot question name, INCLUDING the local entity name for local offices separated by space, e.g., "SHERIFF", "AUDITOR BEAVER TWP", "SUPERVISOR CLEVELAND TWP", "SCHOOL DIRECTOR BENTON AREA", "COUNCIL MEMBER BERWICK BOROUGH", "SUPREME COURT RETENTION ELECTION QUESTION", "SUPERIOR COURT RETENTION ELECTION QUESTION", "COMMONWEALTH COURT RETENTION ELECTION QUESTION")
- district (only for numbered/lettered districts like "REGION I", "REGION II", etc. Leave empty otherwise)
- party (REP, DEM, WI for write-in, YES for ballot question yes votes, NO for ballot question no votes, or empty)
- candidate (the person's name, "Write-in" for write-in votes, or leave empty for ballot questions)
- votes (numeric value only, no commas)

Important: Court retention questions use YES/NO for the party field instead of candidate names. For example:
- SUPREME COURT RETENTION ELECTION QUESTION with YES=8,560 votes becomes party="YES", candidate=""
- SUPREME COURT RETENTION ELECTION QUESTION with NO=7,581 votes becomes party="NO", candidate=""

Return the data as a JSON array of objects. Example format:
[
  {{"county": "{county_name}", "office": "SHERIFF", "district": "", "party": "REP", "candidate": "John Doe", "votes": "10756"}},
  {{"county": "{county_name}", "office": "SCHOOL DIRECTOR BENTON AREA", "district": "REGION I", "party": "REP", "candidate": "Michael Vogt", "votes": "285"}},
  {{"county": "{county_name}", "office": "AUDITOR BEAVER TWP", "district": "", "party": "WI", "candidate": "Lee Rupert", "votes": "4"}},
  {{"county": "{county_name}", "office": "COUNCIL MEMBER BERWICK BOROUGH", "district": "", "party": "", "candidate": "Teresa Troiani", "votes": "1013"}},
  {{"county": "{county_name}", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "YES", "candidate": "", "votes": "8560"}},
  {{"county": "{county_name}", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "NO", "candidate": "", "votes": "7581"}}
]

Skip header/footer text like "Election Summary Report", "{county_name.upper()} COUNTY", page numbers, dates, etc.
Skip metadata like "Number of Precincts", "Registered Voters", "Total Votes" labels, etc.

TEXT:
{page_text}

Return ONLY the JSON array, no other text."""


def extract_with_claude(pages_text, county_name):
    """Use Claude API via llm library to extract structured data from pages"""
    model = llm.get_model("claude-haiku-4.5")
    all_results = []
    
    # Define schema for structured output
    schema = llm.schema_dsl(
        "county, office, district, party, candidate, votes",
        multi=True
    )

    for page_data in pages_text:
        page_num = page_data['page_num']
        text = page_data['text']

        print(f"Processing page {page_num}...")

        prompt = create_extraction_prompt(text, page_num, county_name)

        try:
            response = model.prompt(prompt, schema=schema)
            response_text = response.text()

            # Parse JSON response
            try:
                response_json = json.loads(response_text)
                # Extract items from the multi-item schema
                page_results = response_json.get("items", [])
                all_results.extend(page_results)
                print(f"  Extracted {len(page_results)} results")
            except json.JSONDecodeError as e:
                print(f"  Warning: Could not parse JSON response for page {page_num}: {e}")
                print(f"  Response: {response_text[:200]}...")

        except Exception as e:
            print(f"  Error processing page {page_num}: {e}")
            continue

    return all_results


def write_csv(results, output_path):
    """Write results to CSV in OpenElections format"""
    fieldnames = ['county', 'office', 'district', 'party', 'candidate', 'votes']

    with open(output_path, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} results to {output_path}")


def detect_county_from_filename(pdf_path):
    """Try to detect county name from PDF filename"""
    filename = os.path.basename(pdf_path).lower()
    # Look for common PA county patterns in filename
    import re
    match = re.search(r'([a-z]+)\s*(?:county|pa|final|summary)', filename)
    if match:
        return match.group(1).strip().title()
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python pa_bradford_llm_parser.py <pdf_path> [output_csv] [--county COUNTY_NAME] [--test-page PAGE_NUM]")
        print("\nRequires llm library configured with API keys.")
        print("--county: Specify county name (auto-detected from filename if not provided)")
        print("--test-page: Test extraction on a specific page number.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None
    
    # Check for --county flag
    county_name = None
    if '--county' in sys.argv:
        county_idx = sys.argv.index('--county')
        if county_idx + 1 < len(sys.argv):
            county_name = sys.argv[county_idx + 1]
    
    # Auto-detect county if not provided
    if not county_name:
        county_name = detect_county_from_filename(pdf_path)
        if county_name:
            print(f"Auto-detected county: {county_name}")
        else:
            print("Error: Could not detect county name from filename. Use --county to specify.")
            sys.exit(1)
    
    # Set default output path if not provided
    if not output_path:
        output_path = f"{county_name.lower()}_2025_general.csv"
    
    # Check for --test-page flag
    test_page = None
    if '--test-page' in sys.argv:
        test_idx = sys.argv.index('--test-page')
        if test_idx + 1 < len(sys.argv):
            try:
                test_page = int(sys.argv[test_idx + 1])
            except ValueError:
                print("Error: --test-page requires a page number")
                sys.exit(1)

    print(f"Extracting text from {pdf_path}...")
    pages_text = extract_pdf_text(pdf_path)
    print(f"Found {len(pages_text)} pages with text")

    if test_page:
        print(f"\n=== TESTING PAGE {test_page} ===")
        if test_page < 1 or test_page > len(pages_text):
            print(f"Error: Page {test_page} not found (available pages: 1-{len(pages_text)})")
            sys.exit(1)
        
        page_data = pages_text[test_page - 1]
        print(f"Page text preview:\n{page_data['text'][:500]}...\n")
        
        results = extract_with_claude([page_data], county_name)
        print(f"\nExtracted {len(results)} results from page {test_page}:")
        for result in results:
            print(f"  {result['office']:30} | {result['candidate']:25} | {result['party']:3} | {result['votes']}")
        return

    print("\nExtracting election results with Claude API...")
    results = extract_with_claude(pages_text, county_name)

    print(f"\nTotal candidate results: {len(results)}")

    write_csv(results, output_path)
    print("Done!")


if __name__ == '__main__':
    main()
