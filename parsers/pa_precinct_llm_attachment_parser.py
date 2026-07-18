#!/usr/bin/env python3
"""LLM-based parser for PA precinct election PDFs using page image attachments.

Sends each PDF page as an image attachment to the model instead of extracting text.
Produces precinct-level CSV output with a precinct column.

Uses the shared extraction core in ``llm_pdf_extract`` (also used by
pa_bradford_llm_parser.py and pa_county_llm_attachment_parser.py, which
produce county-level output).

Usage:
    python pa_precinct_llm_attachment_parser.py <pdf_path> [output_csv] [--county COUNTY_NAME] [--test-page PAGE_NUM]
"""

from llm_pdf_extract import run_cli

if __name__ == '__main__':
    run_cli(mode="image", level="precinct", default_output_suffix="2025_general_precinct.csv")
