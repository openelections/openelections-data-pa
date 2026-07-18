"""Shared Claude-based extraction core for the three LLM-assisted PA parsers:

  - pa_bradford_llm_parser.py           (mode="text",  level="county")
  - pa_county_llm_attachment_parser.py  (mode="image", level="county")
  - pa_precinct_llm_attachment_parser.py(mode="image", level="precinct")

These share PDF page extraction, the ``llm`` library invocation + JSON
parsing, CSV writing, and CLI argument handling (``--county``,
``--test-page``, positional pdf/output paths, filename-based county
auto-detection). Kept deliberately SEPARATE per (mode, level): the exact
wording of each extraction prompt, since prompt wording is the highest-risk
thing to alter here (the model's output quality is sensitive to it) and each
of the three already-in-production prompts differs in more than parameter
substitution -- the precinct/image prompt adds a precinct field, a
vote-type-breakdown instruction, Registered-Voters/Ballots-Cast metadata
rows, and a federal/state office-normalization map that the county/image and
text prompts don't have.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile


def extract_pdf_text(pdf_path):
    """Extract all text from PDF pages (mode="text")."""
    import pdfplumber

    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text:
                pages_text.append({'page_num': page_num, 'text': text})
    return pages_text


def render_pdf_pages(pdf_path):
    """Render each PDF page to a temp PNG file (mode="image")."""
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, 1):
            image = page.to_image(resolution=200)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                image.save(tmp.name, format="PNG")
                pages.append({"page_num": page_num, "image_path": tmp.name})
    return pages


def cleanup_images(page_images):
    for page in page_images:
        path = page.get("image_path")
        if path and os.path.exists(path):
            os.remove(path)


def detect_county_from_filename(pdf_path):
    """Try to detect county name from PDF filename."""
    filename = os.path.basename(pdf_path).lower()
    match = re.search(r'([a-z]+)\s*(?:county|pa|final|summary)', filename)
    if match:
        return match.group(1).strip().title()
    return None


def _prompt_text_county(page_text, page_num, county_name):
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


def _prompt_image_county(page_num, county_name):
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


def _prompt_image_precinct(page_num, county_name):
    return f"""Extract precinct-level election results from this {county_name} County, PA election report page.

The attached image is page {page_num} of the PDF. Please extract ALL races, candidates, and ballot questions.

For each candidate/choice, provide:
- county (the county name, e.g., "{county_name}")
- precinct (the precinct/municipality/ward name as shown on the page, e.g., "BLAIR 1", "DOVER TWP 2", "WARD 1")
- office (the position being elected or ballot question name, INCLUDING the local entity name for local offices separated by space, e.g., "SHERIFF", "AUDITOR BEAVER TWP", "SUPERVISOR CLEVELAND TWP", "SCHOOL DIRECTOR BENTON AREA", "COUNCIL MEMBER BERWICK BOROUGH", "SUPREME COURT RETENTION ELECTION QUESTION")
- district (only for numbered/lettered districts like "REGION I", "REGION II", etc. Leave empty otherwise)
- party (REP, DEM, WI for write-in, YES for ballot question yes votes, NO for ballot question no votes, or empty)
- candidate (the person's name, "Write-in" for write-in votes, or leave empty for ballot questions)
- votes (numeric value only, no commas)
- election_day (numeric value only, no commas; empty if not available)
- mail (numeric value only, no commas; empty if not available)
- provisional (numeric value only, no commas; empty if not available)

If the report breaks votes out by type, populate election_day, mail, and provisional with those values for each candidate/choice.
If only total votes are shown, put the total in votes and leave election_day, mail, and provisional empty.

Also extract precinct-level metadata rows as offices:
- office="Registered Voters" with candidate empty and votes set to the registered voter count
- office="Ballots Cast" with candidate empty and votes set to ballots cast

Important: Court retention questions use YES/NO for the party field instead of candidate names. For example:
- SUPREME COURT RETENTION ELECTION QUESTION with YES=8,560 votes becomes party="YES", candidate=""
- SUPREME COURT RETENTION ELECTION QUESTION with NO=7,581 votes becomes party="NO", candidate=""

Normalize federal/state offices using this map (exact matches):
- PRESIDENTIAL ELECTORS -> President
- UNITED STATES SENATOR -> U.S. Senate
- REPRESENTATIVE IN CONGRESS -> U.S. House
- SENATOR IN THE GENERAL ASSEMBLY -> State Senate
- REPRESENTATIVE IN THE GENERAL ASSEMBLY -> State House

Return the data as a JSON array of objects. Example format:
[
    {{"county": "{county_name}", "precinct": "DOVER TWP 2", "office": "SHERIFF", "district": "", "party": "REP", "candidate": "John Doe", "votes": "10756", "election_day": "8123", "mail": "2500", "provisional": "133"}},
    {{"county": "{county_name}", "precinct": "BENTON BOROUGH", "office": "SCHOOL DIRECTOR BENTON AREA", "district": "REGION I", "party": "REP", "candidate": "Michael Vogt", "votes": "285", "election_day": "200", "mail": "80", "provisional": "5"}},
    {{"county": "{county_name}", "precinct": "BEAVER TWP", "office": "AUDITOR BEAVER TWP", "district": "", "party": "WI", "candidate": "Lee Rupert", "votes": "4", "election_day": "4", "mail": "", "provisional": ""}},
    {{"county": "{county_name}", "precinct": "BERWICK BOROUGH", "office": "COUNCIL MEMBER BERWICK BOROUGH", "district": "", "party": "", "candidate": "Teresa Troiani", "votes": "1013", "election_day": "", "mail": "", "provisional": ""}},
    {{"county": "{county_name}", "precinct": "BRIAR CREEK", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "YES", "candidate": "", "votes": "8560", "election_day": "5400", "mail": "3000", "provisional": "160"}},
    {{"county": "{county_name}", "precinct": "BRIAR CREEK", "office": "SUPREME COURT RETENTION ELECTION QUESTION", "district": "", "party": "NO", "candidate": "", "votes": "7581", "election_day": "4800", "mail": "2700", "provisional": "81"}}
]

Skip header/footer text like "Election Summary Report", "{county_name.upper()} COUNTY", page numbers, dates, etc.
Skip metadata like "Number of Precincts", "Registered Voters", "Total Votes" labels, etc.

Return ONLY the JSON array, no other text."""


def _config_for(mode, level):
    if mode == "text" and level == "county":
        return {
            "schema_fields": "county, office, district, party, candidate, votes",
            "fieldnames": ["county", "office", "district", "party", "candidate", "votes"],
            "build_prompt": lambda page, county: _prompt_text_county(page["text"], page["page_num"], county),
        }
    if mode == "image" and level == "county":
        return {
            "schema_fields": "county, office, district, party, candidate, votes, election_day, mail, provisional",
            "fieldnames": ["county", "office", "district", "party", "candidate", "votes",
                           "election_day", "mail", "provisional"],
            "build_prompt": lambda page, county: _prompt_image_county(page["page_num"], county),
        }
    if mode == "image" and level == "precinct":
        return {
            "schema_fields": "county, precinct, office, district, party, candidate, votes, election_day, mail, provisional",
            "fieldnames": ["county", "precinct", "office", "district", "party", "candidate", "votes",
                           "election_day", "mail", "provisional"],
            "build_prompt": lambda page, county: _prompt_image_precinct(page["page_num"], county),
        }
    raise ValueError(f"No configuration for mode={mode!r} level={level!r}")


def _call_anthropic_direct(prompt, model_name, image_path=None):
    """Call a Claude model directly via the anthropic SDK, for model ids not
    yet registered in the installed llm-anthropic plugin (e.g. a model newer
    than the plugin's static model list)."""
    import base64

    import anthropic
    import llm

    key = llm.get_key(None, "anthropic", "ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=key)

    content = [{"type": "text", "text": prompt}]
    if image_path:
        with open(image_path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()
        content.insert(0, {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    response = client.messages.create(
        model=model_name,
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


def extract_with_llm(pages, county_name, mode, level, model_name="claude-haiku-4.5"):
    """Run extraction over ``pages`` (from extract_pdf_text or render_pdf_pages)."""
    import llm

    config = _config_for(mode, level)
    all_results = []

    model = None
    schema = None
    use_direct = False
    try:
        model = llm.get_model(model_name)
        schema = llm.schema_dsl(config["schema_fields"], multi=True)
    except llm.UnknownModelError:
        # Not registered in the installed llm-anthropic plugin; fall back to
        # calling the Anthropic API directly with the raw model id.
        use_direct = True

    for page_data in pages:
        page_num = page_data["page_num"]
        print(f"Processing page {page_num}...")
        prompt = config["build_prompt"](page_data, county_name)

        try:
            if use_direct:
                response_text = _call_anthropic_direct(
                    prompt,
                    model_name,
                    image_path=page_data["image_path"] if mode == "image" else None,
                )
            else:
                kwargs = {"schema": schema}
                if mode == "image":
                    kwargs["attachments"] = [llm.Attachment(path=page_data["image_path"], type="image/png")]
                response = model.prompt(prompt, **kwargs)
                response_text = response.text()

            try:
                response_json = json.loads(response_text)
            except json.JSONDecodeError:
                match = re.search(r"\[.*\]", response_text, re.DOTALL)
                if not match:
                    raise
                response_json = json.loads(match.group(0))

            page_results = response_json.get("items", []) if isinstance(response_json, dict) else response_json
            all_results.extend(page_results)
            print(f"  Extracted {len(page_results)} results")
        except json.JSONDecodeError as e:
            print(f"  Warning: Could not parse JSON response for page {page_num}: {e}")
            print(f"  Response: {response_text[:200]}...")
        except Exception as e:
            print(f"  Error processing page {page_num}: {e}")
            continue

    return all_results


def write_csv(results, output_path, mode, level):
    fieldnames = _config_for(mode, level)["fieldnames"]
    with open(output_path, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nWrote {len(results)} results to {output_path}")


def run_cli(mode, level, argv=None, default_output_suffix="2025_general.csv"):
    """Shared CLI entry point for all three LLM parser scripts.

    ``mode``: "text" or "image". ``level``: "county" or "precinct".
    """
    argv = argv if argv is not None else sys.argv[1:]
    prog = sys.argv[0]

    if len(argv) < 1:
        print(f"Usage: python {prog} <pdf_path> [output_csv] [--county COUNTY_NAME] [--test-page PAGE_NUM]")
        print("\nRequires llm library configured with API keys.")
        print("--county: Specify county name (auto-detected from filename if not provided)")
        print("--test-page: Test extraction on a specific page number.")
        print("--model: Claude model id to use (default: claude-haiku-4.5).")
        sys.exit(1)

    pdf_path = argv[0]
    output_path = argv[1] if len(argv) > 1 and not argv[1].startswith('--') else None

    county_name = None
    if '--county' in argv:
        county_idx = argv.index('--county')
        if county_idx + 1 < len(argv):
            county_name = argv[county_idx + 1]

    if not county_name:
        county_name = detect_county_from_filename(pdf_path)
        if county_name:
            print(f"Auto-detected county: {county_name}")
        else:
            print("Error: Could not detect county name from filename. Use --county to specify.")
            sys.exit(1)

    if not output_path:
        output_path = f"{county_name.lower()}_{default_output_suffix}"

    test_page = None
    if '--test-page' in argv:
        test_idx = argv.index('--test-page')
        if test_idx + 1 < len(argv):
            try:
                test_page = int(argv[test_idx + 1])
            except ValueError:
                print("Error: --test-page requires a page number")
                sys.exit(1)

    model_name = "claude-haiku-4.5"
    if '--model' in argv:
        model_idx = argv.index('--model')
        if model_idx + 1 < len(argv):
            model_name = argv[model_idx + 1]

    if mode == "text":
        print(f"Extracting text from {pdf_path}...")
        pages = extract_pdf_text(pdf_path)
        print(f"Found {len(pages)} pages with text")
    else:
        print(f"Rendering page images from {pdf_path}...")
        pages = render_pdf_pages(pdf_path)
        print(f"Rendered {len(pages)} pages")

    try:
        if test_page:
            if test_page < 1 or test_page > len(pages):
                print(f"Error: Page {test_page} not found (available pages: 1-{len(pages)})")
                sys.exit(1)

            page_data = [pages[test_page - 1]]
            print(f"\n=== TESTING PAGE {test_page} ===")
            if mode == "text":
                print(f"Page text preview:\n{page_data[0]['text'][:500]}...\n")
            results = extract_with_llm(page_data, county_name, mode, level, model_name=model_name)
            print(f"\nExtracted {len(results)} results from page {test_page}:")
            for result in results:
                if level == "precinct":
                    print(f"  {result.get('precinct',''):20} | {result.get('office',''):30} | "
                          f"{result.get('candidate',''):25} | {result.get('party',''):3} | {result.get('votes','')}")
                else:
                    print(f"  {result.get('office',''):30} | {result.get('candidate',''):25} | "
                          f"{result.get('party',''):3} | {result.get('votes','')}")
            return

        print(f"\nExtracting election results...")
        results = extract_with_llm(pages, county_name, mode, level, model_name=model_name)
        print(f"\nTotal candidate results: {len(results)}")
        write_csv(results, output_path, mode, level)
        print("Done!")
    finally:
        if mode == "image":
            cleanup_images(pages)
