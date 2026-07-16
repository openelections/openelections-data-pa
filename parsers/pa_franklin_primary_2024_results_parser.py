#!/usr/bin/env python3
"""
Parse Franklin County PA 2024 Primary precinct results.

Source: Franklin PA PrecinctDetail.pdf
(Electionware primary; candidate rows are 2-column: ``<name> <total> <pct>%``
with no election-day / mail / provisional breakdown.)

Usage:
    python parsers/pa_franklin_primary_2024_results_parser.py <input.pdf> <output.csv>

Reuses the 2025 Franklin general config (office normalizers, precinct
prettifier, local-office handling) via the generic adapter, and overrides
the vote-tail regex to match the 2-column primary format with no
breakdown columns.
"""

import re

import pa_electionware_primary_2024 as adapter
from electionware_primary_np import PrimaryConfig, run_cli


# 2-column vote tail. The 2025 Franklin line_preprocessor strips the
# trailing " N.NN%" vote% token before this regex runs, so candidate rows
# arrive as "<name> <total>" — a single integer tail, no breakdown columns.
TWO_COL_VOTE_RE = re.compile(
    r"^(.*?)\s+(\d[\d,]*)$"
)


PCT_RE = re.compile(r"\s+\d+\.\d+%")
COUNTY_HEADER_RE = re.compile(r"^April 23, 2024\s+FRANKLIN COUNTY\s*$")


class FranklinPrimaryPreprocessor:
    """Stateful line preprocessor for the 2024 Franklin primary.

    1. Strips the trailing `` N.NN%`` vote% token (the 2024 primary PDF is
       2-column: total + vote%, no method breakdown).
    2. Blanks the line immediately following a ``April 23, 2024 FRANKLIN
       COUNTY`` county header — that line is the per-page precinct-name echo
       (e.g. ``ANTRIM 2``) which would otherwise match the 2-column vote-tail
       regex and be emitted as a bogus candidate row.
    """

    def __init__(self):
        self._after_county_header = False

    def __call__(self, line: str) -> str:
        line = PCT_RE.sub("", line)
        # STATISTICS marks the start of a precinct's data section; reset the
        # echo-tracker so any stale flag from the previous block can't blank
        # a real line in this one.
        if line.strip().upper().startswith("STATISTICS"):
            self._after_county_header = False
            return line
        if self._after_county_header:
            self._after_county_header = False
            return ""
        if COUNTY_HEADER_RE.match(line):
            self._after_county_header = True
        return line


def build_config() -> PrimaryConfig:
    base = adapter.load_config("Franklin")
    return PrimaryConfig(
        county=base.county,
        skip_prefixes=base.skip_prefixes,
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
        line_preprocessor=FranklinPrimaryPreprocessor(),
        precinct_block_extractor=None,  # 2024 primary has Statistics markers
        fallback_title_case=base.fallback_title_case,
        party_optional=base.party_optional,
        drop_term_token=base.drop_term_token,
        vote_tail_re=TWO_COL_VOTE_RE,
        vote_breakdown=False,
    )


if __name__ == "__main__":
    import sys
    config = build_config()
    run_cli(config, argv=[sys.argv[0], sys.argv[1], sys.argv[2]])