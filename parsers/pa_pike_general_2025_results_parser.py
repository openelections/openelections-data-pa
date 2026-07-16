"""Parser for Pike County PA 2025 Municipal Election precinct results.

The source PDF is an HTML-rendered "Results per Precinct" report where all
text is rendered as vector curves (no extractable text). Each page contains
one or more SOVC cross-tab tables — one per contest — with precincts as rows
and candidates as columns. Tables may span across page boundaries.

This parser renders each page as a PNG and sends it to the Anthropic vision
API (Claude) for structured extraction, then converts the results to the
OpenElections CSV format.

Requires an ANTHROPIC_API_KEY environment variable, or the key stored via
`llm keys set anthropic`.

Usage:
    uv run python parsers/pa_pike_general_2025_results_parser.py \\
        "<input.pdf>" 2025/counties/20251104__pa__general__pike__precinct.csv
"""

import base64
import csv
import io
import json
import os
import re
import subprocess
import sys

import anthropic
import pdfplumber

COUNTY = "Pike"

FIELDNAMES = [
    "county", "precinct", "office", "district", "party", "candidate",
    "votes", "vote_for",
]

SMALL_WORDS = {"of", "the", "and", "for", "a", "an", "in", "on", "to"}

EXTRACTION_PROMPT = """Extract ALL contest tables from this election results page as JSON.

Return a JSON array of contests. Each contest object has:
- "office": string — the bold underlined contest header text verbatim (e.g. "JUDGE OF THE SUPERIOR COURT", "MATAMORAS BORO COUNCILMAN", "MATAMORAS EMS TAX QUESTION")
- "vote_for": integer from "(Vote for N)" in the header
- "candidates": array of {"name": string, "party": string}
  Parse from column headers: "Brandon Neuman - DEM" -> name="Brandon Neuman", party="DEM".
  "Write-in" column -> name="Write-in", party="".
  YES/NO columns (ballot questions) -> name="Yes"/"No", party="".
- "rows": array of {"precinct": string, "votes": [int, ...]} where votes array matches candidates array order.

IMPORTANT:
- Skip "Total" rows.
- If a table starts on a previous page and only its bottom rows appear at the TOP of this page (rows without a contest header above them on this page), return it as a contest object with office="CONTINUATION" and vote_for=0, with the candidates array left empty — just include the rows with precinct names and vote arrays.
- Include ALL precincts visible in every table.
- Output ONLY valid JSON, no markdown fences, no commentary."""


def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["uv", "run", "llm", "keys", "get", "anthropic"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def title_case(s):
    out = []
    for i, w in enumerate(s.split()):
        if i > 0 and w.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def extract_page(client, pdf, page_idx):
    """Render page as PNG, send to Claude, return parsed JSON contests."""
    page = pdf.pages[page_idx]
    img = page.to_image(resolution=200)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode()

    resp = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
    )
    raw = resp.content[0].text
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


def merge_continuations(all_pages_contests):
    """Merge CONTINUATION entries with the previous contest's data."""
    merged = []
    for page_contests in all_pages_contests:
        for contest in page_contests:
            if contest.get("office") == "CONTINUATION" and merged:
                # Append rows to the last real contest
                prev = merged[-1]
                for row in contest.get("rows", []):
                    # Use the candidate list from previous contest
                    prev["rows"].append(row)
            else:
                merged.append(contest)
    return merged


def contests_to_rows(contests):
    """Convert contest dicts to flat OpenElections rows."""
    rows = []
    for contest in contests:
        office_raw = contest["office"]
        vote_for = contest.get("vote_for", 1)
        candidates = contest.get("candidates", [])
        # Strip "(Vote for N)" that the LLM sometimes includes in the office
        office_stripped = re.sub(r"\s*\(vote\s+for\s+\d+\)\s*", "", office_raw, flags=re.IGNORECASE).strip()
        office_clean = title_case(office_stripped)

        for precinct_row in contest.get("rows", []):
            precinct = precinct_row["precinct"]
            votes = precinct_row["votes"]
            for i, cand in enumerate(candidates):
                if i >= len(votes):
                    break
                name = cand["name"]
                party = cand["party"]
                if name.lower() == "write-in":
                    name = "Write-ins"
                    party = ""
                rows.append({
                    "county": COUNTY,
                    "precinct": precinct,
                    "office": office_clean,
                    "district": "",
                    "party": party,
                    "candidate": name,
                    "votes": votes[i],
                    "vote_for": vote_for,
                })
    return rows


def main(pdf_path, output_csv):
    api_key = get_api_key()
    if not api_key:
        sys.exit(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY or run: "
            "uv run llm keys set anthropic"
        )

    client = anthropic.Anthropic(api_key=api_key)
    pdf = pdfplumber.open(pdf_path)
    n_pages = len(pdf.pages)
    print(f"Processing {n_pages} pages...")

    all_pages = []
    for i in range(n_pages):
        print(f"  Page {i + 1}/{n_pages}...")
        contests = extract_page(client, pdf, i)
        print(f"    {len(contests)} contest(s) extracted")
        all_pages.append(contests)

    merged = merge_continuations(all_pages)
    print(f"Merged into {len(merged)} contest(s)")

    rows = contests_to_rows(merged)

    with open(output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_csv}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: pa_pike_general_2025_results_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
