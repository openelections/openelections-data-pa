"""Behavioral tests for the shared electionware_regex_np engine
(Indiana/Lackawanna), pinning down the variance points the migration from
two standalone scripts had to preserve: PCT% column presence, party code
list, '%'-line skip scope, Write-In Totals dedup, and Yes/No casing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))

from electionware_regex_np import ElectionwareRegexConfig, process_pages  # noqa: E402

INDIANA_CONFIG = ElectionwareRegexConfig(
    county="Indiana",
    county_marker="Indiana County",
    precinct_header_exclude_prefixes=("Statistics", "TOTAL", "OFFICIAL"),
    skip_prefixes=(
        'Summary Results Report', '2025 Municipal Election', 'November 4, 2025',
        'Precinct Summary', 'Report generated', 'OFFICIAL RESULTS',
        'TOTAL', 'Day', 'Statistics', 'Voter Turnout', 'Write-In:',
        'Not Assigned', 'Total Votes Cast', 'Overvotes', 'Undervotes',
        'Contest Totals',
    ),
    office_keywords=('JUDGE', 'COURT', 'SCHOOL DIRECTOR', 'RETENTION'),
    party_codes=("DEM", "REP", "LBR", "LIB", "GRE", "IND", "NF", "CON", "D/R"),
    has_pct_column=False,
    percent_skip=lambda line: '%' in line,
    dedup_write_in_totals=True,
    yesno_uppercase_candidate=False,
    yesno_pattern=r'(Yes|No)',
)

INDIANA_PAGE_1 = """
Summary Results Report OFFICIAL RESULTS
2025 Municipal Election
November 4, 2025 Indiana County
Armagh
Statistics
Registered Voters - Total 47
Ballots Cast - Total 19 17 2 0
JUDGE OF THE SUPERIOR COURT
Vote For 1
DEM Christine Donohue 10 8 1 1
REP John Smith 9 8 1 0
Write-In Totals 0 0 0 0
Total Votes Cast 19 16 2 1
""".strip().split("\n")

# A second page for the same precinct: Write-In Totals repeats (e.g. a
# reprinted page); Indiana should not emit a duplicate row for it.
INDIANA_PAGE_2 = """
Summary Results Report OFFICIAL RESULTS
2025 Municipal Election
November 4, 2025 Indiana County
Armagh
JUDGE OF THE SUPERIOR COURT
Vote For 1
Write-In Totals 0 0 0 0
""".strip().split("\n")


def test_indiana_stats_and_candidates():
    results = process_pages([INDIANA_PAGE_1], INDIANA_CONFIG)
    stats = {r["office"]: r for r in results if r["office"] in ("Registered Voters", "Ballots Cast")}
    assert stats["Registered Voters"]["votes"] == "47"
    assert stats["Ballots Cast"]["votes"] == "19"
    assert stats["Ballots Cast"]["election_day"] == "17"

    donohue = next(r for r in results if r["candidate"] == "Christine Donohue")
    assert donohue["party"] == "DEM"
    assert donohue["votes"] == "10"
    assert donohue["election_day"] == "8"


def test_indiana_percent_lines_fully_skipped():
    # Indiana's blanket '%'-skip means a stray percentage-bearing line never
    # becomes a data row, unlike Lackawanna where '%' is part of every row.
    results = process_pages([INDIANA_PAGE_1 + ["Voter Turnout - Total 40.4%"]], INDIANA_CONFIG)
    assert not any("40.4" in str(r.values()) for r in results)


def test_indiana_dedups_write_in_totals_across_pages():
    results = process_pages([INDIANA_PAGE_1, INDIANA_PAGE_2], INDIANA_CONFIG)
    write_ins = [r for r in results if r["candidate"] == "Write-In Totals"]
    assert len(write_ins) == 1


LACKAWANNA_CONFIG = ElectionwareRegexConfig(
    county="Lackawanna",
    county_marker="Lackawanna County",
    precinct_header_exclude_prefixes=("Statistics", "TOTAL", "CERTIFIED"),
    skip_prefixes=(
        'Summary Results Report', 'MUNICIPAL ELECTION', 'November 4, 2025',
        'Precinct Summary', 'Report generated', 'CERTIFIED RESULTS',
        'TOTAL', 'Day', 'Statistics', 'Voter Turnout', 'Write-In:',
        'Not Assigned', 'Total Votes Cast', 'Overvotes', 'Undervotes',
        'Contest Totals', 'LACKAWANNA PRECINCT',
    ),
    office_keywords=('JUDGE', 'COURT', 'SCHOOL DIRECTOR', 'RETENTION'),
    party_codes=("DEM", "REP", "LBR", "LIB", "GRE", "IND", "NF", "CON", "D/R", "DNR", "AAI"),
    has_pct_column=True,
    percent_skip=lambda line: '%' in line and ('Turnout' in line or 'VOTE' in line),
    extra_skip=lambda line: (line.startswith('TOTAL') and 'VOTE' in line) or line.strip() in ('Day', 'Votes', 'Day Votes'),
    dedup_write_in_totals=False,
    yesno_uppercase_candidate=True,
    yesno_pattern=r'(YES|Yes|NO|No)',
)

LACKAWANNA_PAGE_1 = """
Summary Results Report CERTIFIED RESULTS
MUNICIPAL ELECTION
November 4, 2025 Lackawanna County
Archbald W-01 P-01
Statistics
Registered Voters - Total 1505
Ballots Cast - Total 740 581 157 2
SCHOOL DIRECTOR RETENTION QUESTION
Vote For 1
Yes 300 60.2% 250 45 5
No 198 39.8% 170 20 8
Write-In Totals 2 0.4% 1 1 0
""".strip().split("\n")


def test_lackawanna_candidate_row_with_pct_column():
    results = process_pages([LACKAWANNA_PAGE_1], LACKAWANNA_CONFIG)
    yes_row = next(r for r in results if r["candidate"] == "YES")
    assert yes_row["votes"] == "300"
    assert yes_row["election_day"] == "250"
    assert yes_row["mail"] == "45"
    assert yes_row["provisional"] == "5"


def test_lackawanna_yesno_always_uppercased():
    results = process_pages([LACKAWANNA_PAGE_1], LACKAWANNA_CONFIG)
    candidates = {r["candidate"] for r in results if r["office"] != ""}
    assert "YES" in candidates and "NO" in candidates
    assert "Yes" not in candidates and "No" not in candidates


def test_lackawanna_percent_line_not_fully_skipped_since_data_has_pct():
    # If Lackawanna used Indiana's blanket '%' skip it would eat every
    # candidate row (they all contain a percentage); confirm it doesn't.
    results = process_pages([LACKAWANNA_PAGE_1], LACKAWANNA_CONFIG)
    assert any(r["candidate"] == "YES" for r in results)


def test_lackawanna_writein_totals_not_deduped():
    # Unlike Indiana, Lackawanna doesn't dedup -- a repeated Write-In Totals
    # line on a second page should emit a second row.
    results = process_pages([LACKAWANNA_PAGE_1, LACKAWANNA_PAGE_1], LACKAWANNA_CONFIG)
    write_ins = [r for r in results if r["candidate"] == "Write-In Totals"]
    assert len(write_ins) == 2
