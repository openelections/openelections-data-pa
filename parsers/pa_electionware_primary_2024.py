#!/usr/bin/env python3
"""
Generic 2024 PA primary precinct parser for Electionware counties.

Reuses each county's existing 2025-general ``ElectionwareConfig`` (office
normalizers, precinct prettifier, local-office handling) and adapts only the
report-level ``skip_prefixes`` for the primary ("Primary Election" instead
of "Municipal Election", "April 23, 2024" instead of "November 4, 2025").
The party-on-office-header parsing is handled by the shared
``electionware_primary_np`` engine.

Usage:
    python parsers/pa_electionware_primary_2024.py <County> <input.pdf> <output.csv>

where <County> matches the name suffix of an existing
``parsers/pa_<county_lower>_general_2025_results_parser.py`` module
(e.g. ``Franklin``, ``Huntingdon``, ``Lawrence``).

Counties whose 2024 primary source PDF does not match the Electionware
primary format will fail at the block-extraction step; those should be
handled by a dedicated parser instead.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

from electionware_primary_np import PrimaryConfig, run_cli


PRIMARY_DATE_VARIANTS = (
    "April 23, 2024",
    "April 23 2024",
    "04/23/2024",
    "4/23/2024",
)


def _adapt_skip_prefixes(skip_prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Translate 2025-general skip_prefixes to 2024-primary equivalents.

    The Electionware report header lines are shape-identical across years;
    only the election name and date differ. We substitute those tokens
    conservatively and add a few primary-specific header lines.
    """
    adapted: list[str] = []
    for p in skip_prefixes:
        # Election name.
        p2 = p.replace("Municipal Election", "Primary Election")
        # Date tokens (cover the common 2025-general date strings).
        for old_date in (
            "November 4, 2025",
            "November 4 2025",
            "11/04/2025",
            "11/4/2025",
        ):
            p2 = p2.replace(old_date, "April 23, 2024")
        adapted.append(p2)
    # Always include the canonical primary header lines (dedup).
    canonical = (
        "Summary Results Report OFFICIAL RESULTS",
        "Summary Results Report UNOFFICIAL RESULTS",
        "Primary Election",
        "Precinct Summary - ",
        "Report generated with Electionware",
        "Election Provisional",
        "TOTAL Mail Votes",
        "Day Votes",
        "Voter Turnout - Total",
        "Vote For ",
        "Total Votes Cast",
        "Contest Totals",
    )
    for c in canonical:
        if c not in adapted:
            adapted.append(c)
    return tuple(adapted)


def load_config(county: str) -> PrimaryConfig:
    module_name = f"pa_{county.lower()}_general_2025_results_parser"
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        sys.exit(
            f"No 2025-general parser module found for county {county!r}: {exc}"
        )
    base = getattr(mod, "CONFIG", None)
    if base is None:
        sys.exit(f"{module_name} does not expose a CONFIG object")
    # Rebuild as a PrimaryConfig with adapted skip_prefixes. The county
    # header suffix is reused as-is (it is just the county name).
    return PrimaryConfig(
        county=base.county,
        skip_prefixes=_adapt_skip_prefixes(base.skip_prefixes),
        county_header_suffix=base.county_header_suffix,
        exact_offices=dict(base.exact_offices),
        local_offices=list(base.local_offices),
        local_office_orientation=base.local_office_orientation,
        retention_style=base.retention_style,
        title_case_retention_tail=base.title_case_retention_tail,
        include_common_pleas=base.include_common_pleas,
        include_magisterial=base.include_magisterial,
        municipality_normalizer=base.municipality_normalizer,
        school_director_handler=base.school_director_handler,
        extra_office_handlers=list(base.extra_office_handlers),
        prettify_precinct=base.prettify_precinct,
        line_preprocessor=base.line_preprocessor,
        precinct_block_extractor=base.precinct_block_extractor,
        fallback_title_case=base.fallback_title_case,
        party_optional=base.party_optional,
        drop_term_token=base.drop_term_token,
    )


if __name__ == "__main__":
    argv = sys.argv
    use_standard_extractor = False
    filtered = [argv[0]]
    for a in argv[1:]:
        if a == "--standard-extractor":
            use_standard_extractor = True
        else:
            filtered.append(a)
    if len(filtered) != 4:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} [--standard-extractor] <County> <input.pdf> <output.csv>")
    county = filtered[1]
    pdf_path = filtered[2]
    out_path = filtered[3]
    config = load_config(county)
    if use_standard_extractor:
        config.precinct_block_extractor = None
    run_cli(config, argv=[filtered[0], pdf_path, out_path])