#!/usr/bin/env python3
"""
Parse Mercer County PA 2023 General (Municipal) Election precinct results.

Source: Mercer County Precinct Results 2023 General.pdf (Electionware
"Precinct Summary Results Report", 4 columns: TOTAL / Election Day /
Absentee / Provisional). Parsed from the pdftotext -layout extract.

Usage:
    python parsers/pa_mercer_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with the same
office tables as ``pa_mercer_general_2025_results_parser.py`` (reused via
the shared ``normalize_office``).

Mercer-2023-specific quirks:
  - The court-retention questions are UNNAMED in 2023 ("SUPERIOR COURT
    RETENTION ELECTION QUESTION" twice per precinct, "COURT OF COMMON
    PLEAS RETENTION ELECTION QUESTION" once). The two Superior Court
    questions are disambiguated by ballot order (occurrence 0/1) as
    "Superior Court Retention Election Question 1/2" — the source never
    names the judges.
  - MDJ headers duplicate the district number
    ("MAGISTERIAL DISTRICT JUDGE DISTRICT 35-02-02 35-02-02"); deduped.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    normalize_office,
)

# Reuse the 2025 Mercer wrapper's config (office tables, municipality
# normalizer, school-director handler, retention map).
from pa_mercer_general_2025_results_parser import (  # noqa: E402
    CONFIG as _NP_2025,
)
from pa_mercer_general_2025_results_parser import mercer_muni  # noqa: E402

_FALLBACKS_2023 = (
    ("COUNCIL", "Borough Council"),
    ("AUDITOR", "Township Auditor"),
)


def normalize(line: str):
    # 2023 headers use plain "COUNCIL <muni>" and "AUDITOR <muni>" prefixes
    # that the 2025 table lacks; boroughs get the Borough office name.
    for prefix, norm in _FALLBACKS_2023:
        if line.startswith(prefix + " ") or line == prefix:
            muni = mercer_muni(line[len(prefix):].strip())
            if prefix == "AUDITOR" and muni.lower().endswith("borough"):
                norm = "Borough Auditor"
            return (norm, muni)
    office, district = normalize_office(line, _NP_2025)
    # "MAGISTERIAL DISTRICT JUDGE DISTRICT 35-02-02 35-02-02" -> dedupe.
    if office == "Magisterial District Judge" and district:
        parts = district.split()
        if len(parts) > 1 and len(set(parts)) == 1:
            district = parts[0]
    return (office, district)


DISAMBIGUATE_ALL = True  # validate_helper.py: apply occurrence relabeling


def disambiguate(office: str, district: str, occurrence: int):
    """Several Mercer contests share one header within a precinct (two
    supervisor/auditor seats, two Superior Court retention questions,
    second School Director contests). The source never names them apart,
    so repeated (office, district) headers inside one precinct get a
    "(2)"/"(3)" occurrence suffix in ballot order. The county summary
    lists the same contests in the same order, so validate_helper.py
    applies the identical relabeling."""
    if occurrence > 0:
        return (f"{office} ({occurrence + 1})", district)
    return (office, district)


from electionware_txt import TxtConfig  # noqa: E402

CONFIG = TxtConfig(
    county="Mercer",
    normalize_office=normalize,
    prettify_precinct=lambda s: s,
    disambiguate=disambiguate,
    extra_parties=("CFS",),  # "CFS ADAM SAELER" (Lakeview School Director)
)

if __name__ == "__main__":
    from electionware_txt import run_cli

    run_cli(CONFIG)