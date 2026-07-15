"""Behavioral tests for the shared sovc_geo_np engine (Wayne/Lycoming).

These exercise the line-processing state machine directly against small
text fixtures modeled on each county's real report shape, since no source
PDFs are checked into the repo. They pin down the two variance points
(two-line vs one-line contest headers, Overvotes/Undervotes handling) that
the migration from two standalone scripts into one config-driven engine had
to preserve exactly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))

from sovc_geo_np import SovcGeoConfig, parse_text, check_printed_totals  # noqa: E402

WAYNE_TEXT = """
Statement of Votes Cast
WAYNE COUNTY
Choice Votes Vote
Precinct BERLIN TOWNSHIP #1
PRESIDENT OF THE UNITED STATES (Vote for 1)
383 ballots (X), 932 registered voters, turnout 41.09%
KAMALA HARRIS 200 53.5% 180 15 5
DONALD TRUMP 180 46.5% 160 15 5
Write-in 3 0.8% 2 1 0
Total 383 100.0% 342 31 10
Overvotes 0 0.0% 0 0 0
Undervotes 0 0.0% 0 0 0
JUDGE RETENTION (Vote for 1)
383 ballots (X), 932 registered voters, turnout 41.09%
Yes 200 60.0% 180 15 5
No 133 40.0% 120 10 3
Total 333 100.0% 300 25 8
"""

WAYNE_CONFIG = SovcGeoConfig(
    county="Wayne",
    skip_prefixes=("Statement of Votes Cast", "WAYNE COUNTY", "All Precincts",
                    "Total Ballots Cast:", "35 precincts reported", "Choice Votes Vote"),
    contest_re=__import__("re").compile(r'^(.+?)\s+\(Vote for\s+(\d+)\)$'),
    ballots_re=__import__("re").compile(
        r'^(\d[\d,]*)\s+ballots\s+\(.*?\),\s+([\d,]+)\s+registered voters,\s+turnout\s+([\d.]+)%$'
    ),
    skip_over_undervotes=True,
)

LYCOMING_TEXT = """
Official Results by Precinct
Lycoming County
Choice Votes Vote
Precinct Anthony Township
PRESIDENT OF THE UNITED STATES (Vote for 1), 649 registered voters, turnout 41.45%
KAMALA HARRIS 130 48.1% 110 15 5
DONALD TRUMP 140 51.9% 120 15 5
Write-in 0 0.0% 0 0 0
Total 270 100.0% 230 30 10
"""

LYCOMING_CONFIG = SovcGeoConfig(
    county="Lycoming",
    skip_prefixes=("Official Results by Precinct", "November 4, 2025", "Lycoming County",
                    "All Precincts", "Total Ballots Cast:", "80 precincts reported", "Choice Votes Vote"),
    contest_re=__import__("re").compile(
        r'^(.+?)\s+\(Vote for\s+(\d+)\),\s+([\d,]+)\s+registered voters,\s+turnout\s+([\d.]+)%$'
    ),
    ballots_re=None,
    skip_over_undervotes=False,
)


def test_wayne_two_line_header_reads_ballots_directly():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    stats = {r["office"]: r["votes"] for r in results if r["office"] in ("Registered Voters", "Ballots Cast")}
    assert stats["Registered Voters"] == "932"
    assert stats["Ballots Cast"] == "383"  # read directly from the ballots line, not derived


def test_wayne_stats_emitted_once_per_precinct_not_per_contest():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    stats_rows = [r for r in results if r["office"] in ("Registered Voters", "Ballots Cast")]
    assert len(stats_rows) == 2  # one Registered Voters + one Ballots Cast, despite two contests


def test_wayne_overvotes_undervotes_skipped():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    candidates = {r["candidate"] for r in results}
    assert "Overvotes" not in candidates
    assert "Undervotes" not in candidates


def test_wayne_writein_accumulated_and_flushed_on_total():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    writeins = [r for r in results if r["candidate"] == "Write-in"]
    assert len(writeins) == 1
    assert writeins[0]["office"] == "PRESIDENT OF THE UNITED STATES"
    assert writeins[0]["votes"] == "3"
    assert writeins[0]["election_day"] == "2"


def test_wayne_yesno_rows_use_office_and_vote_for():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    yes_row = next(r for r in results if r["candidate"] == "Yes")
    assert yes_row["office"] == "JUDGE RETENTION"
    assert yes_row["vote_for"] == "1"
    assert yes_row["votes"] == "200"


def test_lycoming_one_line_header_derives_ballots_cast():
    results, _ = parse_text(LYCOMING_TEXT, LYCOMING_CONFIG)
    stats = {r["office"]: r["votes"] for r in results if r["office"] in ("Registered Voters", "Ballots Cast")}
    # round(649 * 41.45 / 100) == 269
    assert stats["Registered Voters"] == "649"
    assert stats["Ballots Cast"] == "269"


def test_lycoming_candidate_rows_correct():
    results, _ = parse_text(LYCOMING_TEXT, LYCOMING_CONFIG)
    harris = next(r for r in results if r["candidate"] == "KAMALA HARRIS")
    assert harris["votes"] == "130"
    assert harris["election_day"] == "110"
    assert harris["mail"] == "15"
    assert harris["provisional"] == "5"


def test_lycoming_precinct_name_uses_title_case_as_written():
    results, _ = parse_text(LYCOMING_TEXT, LYCOMING_CONFIG)
    assert all(r["precinct"] == "Anthony Township" for r in results)


def test_both_configs_produce_same_fieldset_shape():
    wayne, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    lycoming, _ = parse_text(LYCOMING_TEXT, LYCOMING_CONFIG)
    expected_keys = {'county', 'precinct', 'office', 'district', 'party',
                      'candidate', 'vote_for', 'votes', 'election_day', 'mail', 'provisional'}
    for row in wayne + lycoming:
        assert set(row.keys()) == expected_keys


def test_printed_totals_captured_for_both_contests():
    _, printed_totals = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    assert printed_totals[("BERLIN TOWNSHIP #1", "PRESIDENT OF THE UNITED STATES")]["votes"] == "383"
    assert printed_totals[("BERLIN TOWNSHIP #1", "JUDGE RETENTION")]["votes"] == "333"


def test_check_printed_totals_no_mismatches_when_report_reconciles():
    results, printed_totals = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    mismatches = check_printed_totals(results, printed_totals)
    assert mismatches == []


def test_check_printed_totals_detects_undercount():
    results, printed_totals = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    # Simulate a parsing bug: one candidate's votes silently dropped.
    broken = [r for r in results if not (r["candidate"] == "DONALD TRUMP")]
    mismatches = check_printed_totals(broken, printed_totals)
    assert len(mismatches) >= 1
    assert any(m["office"] == "PRESIDENT OF THE UNITED STATES" and m["field"] == "votes" for m in mismatches)


def test_check_printed_totals_skips_contests_without_a_printed_total():
    results, _ = parse_text(WAYNE_TEXT, WAYNE_CONFIG)
    mismatches = check_printed_totals(results, printed_totals={})
    assert mismatches == []
