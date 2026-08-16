"""Format signatures used by `oepa detect` to fingerprint a source PDF/text.

Each signature is a set of weighted text markers seen on the first few pages
of a report. Score = (sum of weights matched) / (sum of all weights). These
are heuristics tuned against the actual regexes/constants in the reference
parser for each family (see the family docstring for the source file) --
they are meant to get a human to the right existing parser or scaffold
quickly, not to be a certain classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Marker:
    pattern: re.Pattern
    weight: float
    label: str = ""


@dataclass(frozen=True)
class FormatSignature:
    family: str
    markers: tuple  # tuple[Marker, ...]
    min_score: float
    suggested_engine: str
    description: str = ""

    def score(self, text: str) -> tuple:
        """Return (score 0..1, matched labels)."""
        total_weight = sum(m.weight for m in self.markers)
        if total_weight == 0:
            return 0.0, []
        matched = [m for m in self.markers if m.pattern.search(text)]
        matched_weight = sum(m.weight for m in matched)
        labels = [m.label or m.pattern.pattern for m in matched]
        return matched_weight / total_weight, labels


def _m(pattern: str, weight: float, label: str = "", flags=re.MULTILINE) -> Marker:
    return Marker(re.compile(pattern, flags), weight, label)


# electionware_np: the 21-county shared engine in electionware_precinct_np.py.
# Precinct boundary marker "Statistics"/"STATISTICS" repeats once per precinct;
# "Registered Voters - Total" and "Write-In Totals" are captured statistics rows.
SIG_ELECTIONWARE_NP = FormatSignature(
    family="electionware_np",
    markers=(
        _m(r"^(Statistics|STATISTICS)\s*$", 3.0, "Statistics marker (own line)"),
        _m(r"Registered Voters - Total", 2.0),
        _m(r"^Vote For \d+", 1.5, "Vote For N"),
        _m(r"Write-In Totals", 1.0),
        _m(r"Precinct Summary|Summary Results Report", 1.5),
    ),
    min_score=0.5,
    suggested_engine="parsers/electionware_precinct_np.py",
    description="Electionware precinct summary (natural_pdf); repeats per precinct",
)

# electionware_county: same Electionware software, but a single county-wide
# summary report (Statistics section appears once, not once per precinct).
SIG_ELECTIONWARE_COUNTY = FormatSignature(
    family="electionware_county",
    markers=(
        _m(r"^(Statistics|STATISTICS)\s*$", 2.0),
        _m(r"Registered Voters - Total", 2.0),
        _m(r"[A-Z][A-Z ]+ COUNTY,\s*PENNSYLVANIA", 2.0, "county header line"),
    ),
    min_score=0.5,
    suggested_engine="parsers/electionware_county.py",
    description="Electionware county-level summary (pdfplumber); single Statistics section",
)

# sovc_geo: "Statement of Votes Cast by Geography"-style natural_pdf parsers
# (Wayne, Lycoming, Fulton). Precinct header line "Precinct X" or "N of M
# registered voters"; contest header "(Vote for N)"; data rows end in
# "TOTAL PCT% ED MI PR".
SIG_SOVC_GEO = FormatSignature(
    family="sovc_geo",
    markers=(
        _m(r"^Precinct\s+.+$", 2.0, "Precinct <name> line"),
        _m(r"\(Vote for\s+\d+\)", 2.0),
        _m(r"registered voters,\s*turnout\s*[\d.]+%", 2.0),
        _m(r"Statement of Votes Cast", 1.0),
        _m(r"\d+\s+of\s+[\d,]+\s+registered voters", 1.5, "N of M registered voters (Fulton variant)"),
    ),
    min_score=0.45,
    suggested_engine="parsers/pa_wayne_general_2025_results_parser.py (sovc_geo family)",
    description="natural_pdf regex parser over per-precinct SOVC-by-geography reports",
)

# electionware_regex: hand-rolled natural_pdf regex parsers over Electionware
# precinct summaries (Indiana, Lackawanna) -- looks like electionware_np but
# is parsed with standalone regexes rather than the shared config engine.
SIG_ELECTIONWARE_REGEX = FormatSignature(
    family="electionware_regex",
    markers=(
        _m(r"^(Statistics|STATISTICS)\s*$", 2.0),
        _m(r"^Vote For \d+", 1.5),
        _m(r"Write-In Totals", 1.0),
        _m(r"Voter Turnout", 1.0),
    ),
    min_score=0.4,
    suggested_engine="parsers/pa_indiana_general_2025_results_parser.py (electionware_regex family)",
    description="natural_pdf hand-rolled regex parser, Electionware precinct summary look-alike",
)

# sovc_crosstab: pdfplumber parsers over rotated-header "Statement of Votes
# Cast" crosstabs (Bedford, Jefferson). Rotated text decodes to reversed
# strings, so reversed party codes like ")MED(" / ")PER(" show up raw.
SIG_SOVC_CROSSTAB = FormatSignature(
    family="sovc_crosstab",
    markers=(
        _m(r"Statement of Votes Cast", 2.0),
        _m(r"\)(MED|PER|DNI|BIL|RG|GRN|WOR)\(", 3.0, "reversed party code, e.g. )MED("),
        _m(r"(Election Day|Mail-In|Provisional|Total)\n.*(Election Day|Mail-In|Provisional|Total)",
           1.5, "stacked vote-type row labels", flags=re.MULTILINE | re.DOTALL),
    ),
    min_score=0.4,
    suggested_engine="parsers/pa_bedford_general_2025_results_parser.py (sovc_crosstab family)",
    description="pdfplumber parser over rotated-header SOVC crosstab (precincts x candidates)",
)

# electionware_text: pdftotext -layout based two-column-interleaved reports.
SIG_ELECTIONWARE_TEXT = FormatSignature(
    family="electionware_text",
    markers=(
        _m(r"^(Statistics|STATISTICS)\s*$", 1.5),
        _m(r"Registered Voters - Total", 1.5),
        _m(r"Ballots Cast Blank", 1.0),
    ),
    min_score=0.4,
    suggested_engine="parsers/electionware_text_county.py or electionware_text_precinct.py",
    description="pdftotext -layout Electionware report; use when natural_pdf extraction looks garbled",
)

SIGNATURES = (
    SIG_SOVC_CROSSTAB,      # check crosstab before sovc_geo -- both mention "Statement of Votes Cast"
    SIG_SOVC_GEO,
    SIG_ELECTIONWARE_NP,
    SIG_ELECTIONWARE_REGEX,
    SIG_ELECTIONWARE_COUNTY,
    SIG_ELECTIONWARE_TEXT,
)
