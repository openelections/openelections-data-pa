#!/usr/bin/env python3
"""LLM-based parser for PA county election PDFs using page image attachments.

Sends each PDF page as an image attachment to the model instead of extracting text.
"""

import pdfplumber
import llm
import csv
import sys
import os
import json
import tempfile
from typing import List, Dict, Optional


def render_pdf_pages(pdf_path: str) -> List[Dict[str, str]]:
    """Render each PDF page to an image file and return list with page_num and path."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            # Render at higher resolution for better OCR by the model
            image = page.to_image(resolution=200)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                image.save(tmp.name, format="PNG")
                pages.append({"page_num": page_num, "image_path": tmp.name})
    return pages


def create_extraction_prompt(page_num: int, county_name: str) -> str:
    """Create prompt for model to extract election results from a page image."""
    return f"""Extract election results from this {county_name} County, PA election report page.

The attached image is page {page_num} of the PDF. Please extract ALL races, candidates, and ballot questions.

For each candidate/choice, provide:
- county (the county name, e.g., "{county_name}")
- office (the position being elected or ballot question name, INCLUDING the local entity name for local offices separated by space, e.g., "SHERIFF", "AUDITOR BEAVER TWP", "SUPERVISOR CLEVELAND TWP", "SCHOOL DIRECTOR BENTON AREA", "COUNCIL MEMBER BERWICK BOROUGH", "SUPREME COURT RETENTION ELECTION QUESTION", "SUPERIOR COURT RETENTION ELECTION QUESTION", "COMMONWEALTH COURT RETENTION ELECTION QUESTION")
- district (only for numbered/lettered districts like "REGION I", "REGION II", etc. Leave empty otherwise)
- party (REP, DEM, WI for write-in, YES for ballot question yes votes, NO for ballot question no votes, or empty)
- candidate (the person's name, "Write-in" for write-in votes, or leave empty for ballot questions)
- votes (numeric value only, no commas)
- election_day (numeric value only, no commas; empty if not available)
- mail (numeric value only, no commas; empty if not available)
- provisional (numeric value only, no commas; empty if not available)

Important: Court retention questions use YES/NO for the party field instead of candidate names. For example:
- SUPREME COURT RETENTION ELECTION QUESTION with YES=8,560 votes becomes party="YES", candidate=""
- SUPREME COURT RETENTION ELECTION QUESTION with NO=7,581 votes becomes party="NO", candidate=""

Return the data as a JSON array of objects. Example format:
[
    {{"county": "{county_name}", "office": "SHERIFF", "district": "", "party": "REP", "candidate": "John Doe", "votes": "10756", "election_day": "8123", "mail": "2500", "provisional": "133"}},
    {{"county": "{county_name}", "office": "SCHOOL DIRECTOR BENTON AREA", "district": "REGION I", "party": "REP", "candidate": "Michael Vogt", "votes": "285", "election_day": "200", "mail": "80", "provisional": "5"}},
    {{"county": "{county_name}", "office": "AUDITOR BEAVER TWP", "district": "", "party": "WI", "candidate": "Lee Rupert", "votes": "4", "election_day": "4", "mail": "", "provisional": ""}},
    {{"county": "{county_name}", "office": "COUNCIL MEMBER BERWICK BOROUGH", "district": "", "party": "", "candidate": "Teresa Troiani", "votes": "1013", "election_day": "", "mail": "", "provisional": ""}},
    {{"county": "{county_name}", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "YES", "candidate": "", "votes": "8560", "election_day": "5400", "mail": "3000", "provisional": "160"}},
    {{"county": "{county_name}", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "NO", "candidate": "", "votes": "7581", "election_day": "4800", "mail": "2700", "provisional": "81"}}
]

Skip header/footer text like "Election Summary Report", "{county_name.upper()} COUNTY", page numbers, dates, etc.
Skip metadata like "Number of Precincts", "Registered Voters", "Total Votes" labels, etc.

Return ONLY the JSON array, no other text."""


def extract_with_model(page_images: List[Dict[str, str]], county_name: str) -> List[Dict[str, str]]:
    """Use model via llm library to extract structured data from page images."""
    model = llm.get_model("claude-haiku-4.5")
    all_results: List[Dict[str, str]] = []

    schema = llm.schema_dsl(
        "county, office, district, party, candidate, votes, election_day, mail, provisional",
        multi=True
    )

    for page_data in page_images:
        page_num = page_data["page_num"]
        image_path = page_data["image_path"]

        print(f"Processing page {page_num}...")
        prompt = create_extraction_prompt(page_num, county_name)

        try:
            response = model.prompt(
                prompt,
                schema=schema,
                attachments=[llm.Attachment(path=image_path, type="image/png")]
            )
            response_text = response.text()

            try:
                response_json = json.loads(response_text)
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


def write_csv(results: List[Dict[str, str]], output_path: str) -> None:
    """Write results to CSV in OpenElections format."""
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

    with open(output_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nWrote {len(results)} results to {output_path}")


def detect_county_from_filename(pdf_path: str) -> Optional[str]:
    """Try to detect county name from PDF filename."""
    filename = os.path.basename(pdf_path).lower()
    match = __import__("re").search(r"([a-z]+)\s*(?:county|pa|final|summary)", filename)
    if match:
        return match.group(1).strip().title()
    return None


def cleanup_images(page_images: List[Dict[str, str]]) -> None:
    for page in page_images:
        path = page.get("image_path")
        if path and os.path.exists(path):
            os.remove(path)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python pa_county_llm_attachment_parser.py <pdf_path> [output_csv] [--county COUNTY_NAME] [--test-page PAGE_NUM]")
        print("\nRequires llm library configured with API keys.")
        print("--county: Specify county name (auto-detected from filename if not provided)")
        print("--test-page: Test extraction on a specific page number.")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else None

    county_name = None
    if '--county' in sys.argv:
        county_idx = sys.argv.index('--county')
        if county_idx + 1 < len(sys.argv):
            county_name = sys.argv[county_idx + 1]

    if not county_name:
        county_name = detect_county_from_filename(pdf_path)
        if county_name:
            print(f"Auto-detected county: {county_name}")
        else:
            print("Error: Could not detect county name from filename. Use --county to specify.")
            sys.exit(1)

    if not output_path:
        output_path = f"{county_name.lower()}_2025_general.csv"

    test_page = None
    if '--test-page' in sys.argv:
        test_idx = sys.argv.index('--test-page')
        if test_idx + 1 < len(sys.argv):
            try:
                test_page = int(sys.argv[test_idx + 1])
            except ValueError:
                print("Error: --test-page requires a page number")
                sys.exit(1)

    print(f"Rendering page images from {pdf_path}...")
    page_images = render_pdf_pages(pdf_path)
    print(f"Rendered {len(page_images)} pages")

    try:
        if test_page:
            if test_page < 1 or test_page > len(page_images):
                print(f"Error: Page {test_page} not found (available pages: 1-{len(page_images)})")
                sys.exit(1)

            page_data = [page_images[test_page - 1]]
            print(f"\n=== TESTING PAGE {test_page} ===")
            results = extract_with_model(page_data, county_name)
            print(f"\nExtracted {len(results)} results from page {test_page}:")
            for result in results:
                print(f"  {result.get('office',''):30} | {result.get('candidate',''):25} | {result.get('party',''):3} | {result.get('votes','')}")
            return

        print("\nExtracting election results with model (page attachments)...")
        results = extract_with_model(page_images, county_name)
        print(f"\nTotal candidate results: {len(results)}")
        write_csv(results, output_path)
        print("Done!")
    finally:
        cleanup_images(page_images)


if __name__ == '__main__':
    main()
