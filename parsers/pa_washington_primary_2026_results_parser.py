#!/usr/bin/env python3
"""Parse Washington County PA 2026 Primary precinct results.

Source: Washington County 2026_Primary_Precinct_Summary_Official_add7761a3f.pdf
(Electionware precinct summary). Reuses the 2025 general parser's config via
``pa_electionware_primary_2026.load_config``, then post-processes the rows to
fill in district ordinals Washington's PDF omits on the U.S. House and State
Senate contest headers (the 2024 general CSV confirms Washington is entirely
within PA-14 and SD-46).

Usage:
    uv run python parsers/pa_washington_primary_2026_results_parser.py <input.pdf> <output.csv>
"""

import sys
from pathlib import Path

from electionware_primary_np import parse_primary_pdf, write_primary_csv
from pa_electionware_primary_2026 import load_config


# Washington's PDF drops the district ordinal on the U.S. House and State
# Senate contest headers. Hardcode based on the 2024 general CSV (PA-14,
# SD-46) and the 2026 candidate roster (Evan Snyder ran in SD-46).
DISTRICT_FIXES = {"U.S. House": "14", "State Senate": "46"}


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    config = load_config("Washington")
    rows, precinct_count = parse_primary_pdf(pdf_path, config)
    for r in rows:
        if not r.get("district") and r.get("office") in DISTRICT_FIXES:
            r["district"] = DISTRICT_FIXES[r["office"]]
    write_primary_csv(rows, out_path)
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )


if __name__ == "__main__":
    main(sys.argv)