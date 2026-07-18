"""Behavioral tests for the shared sovc_crosstab_pp engine (Bedford/Jefferson).

pdfplumber's extract_tables()/extract_text() return plain nested lists and
strings, so these construct synthetic table/page data by hand (reversed
column headers included) rather than needing a real PDF.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))

from sovc_crosstab_pp import (  # noqa: E402
    SovcCrosstabConfig,
    decode_header,
    parse_candidate_header,
    parse_contest_title,
    make_clean_votes,
    _parse_turnout_simple,
    _parse_candidate_table_simple,
    _parse_turnout_vote_types,
    _parse_candidate_table_vote_types,
    decode_candidates,
)


def reversed_header(display_text):
    """Build the raw rotated-text column header pdfplumber would extract
    for a given human-readable candidate label, e.g. "Brandon Neuman (DEM)"."""
    lines = display_text.split(' ')
    return '\n'.join(part[::-1] for part in reversed(lines))


def test_decode_header_roundtrip():
    # Preserves the original algorithm's exact (line-reversed, not
    # word-reversed) output; parse_candidate_header's regex-based party
    # extraction works regardless of whether "(DEM)" ends up leading or
    # trailing, so this word order doesn't affect final parsed output.
    raw = "namueN\nnodnarB\n)MED("
    assert decode_header(raw) == "(DEM) Brandon Neuman"


def test_parse_candidate_header_cross_filed_party():
    name, party = parse_candidate_header("Brandon Neuman (DEMREP)")
    assert name == "Brandon Neuman"
    assert party == "DEM/REP"


def test_parse_candidate_header_skips_times_cast():
    assert parse_candidate_header("Times Cast") == (None, None)
    assert parse_candidate_header("Registered Voters") == (None, None)


def test_parse_candidate_header_yesno():
    assert parse_candidate_header("yes") == ("Yes", "")


BEDFORD_CONFIG = SovcCrosstabConfig(
    county="Bedford",
    vote_type_rows=False,
    is_skip_row=lambda p: p in ("Bedford County", "Cumulative", "") or "Total" in p,
    turnout_max_pages=2,
)

JEFFERSON_CONFIG = SovcCrosstabConfig(
    county="Jefferson",
    vote_type_rows=True,
    is_skip_row=lambda p: p in ("Jefferson County", "Cumulative", "") or "Cumulative" in p
                          or ("Total" in p and "County" in p),
    treat_redacted_as_zero=True,
    contest_title_raw_lines=True,
    turnout_requires_registered_header=True,
    turnout_max_pages=7,
)


def test_bedford_turnout_simple_one_row_per_precinct():
    clean_votes = make_clean_votes(BEDFORD_CONFIG)
    table = [
        ["Precinct", "Registered", "Ballots"],
        ["East Ward", "1,047", "455"],
        ["West Ward", "900", "400"],
        ["Bedford County Total", "1,947", "855"],
    ]
    turnout = _parse_turnout_simple([[table]], BEDFORD_CONFIG, clean_votes)
    assert turnout["East Ward"] == ("1047", "455")
    assert "Bedford County Total" not in turnout  # skipped: contains "Total"


def test_bedford_candidate_table_simple_direct_votes():
    clean_votes = make_clean_votes(BEDFORD_CONFIG)
    header = ["Precinct", reversed_header("Brandon Neuman (DEM)"), reversed_header("John Smith (REP)")]
    table = [
        header,
        ["East Ward", "120", "100"],
    ]
    candidates = decode_candidates(header, BEDFORD_CONFIG)
    rows = _parse_candidate_table_simple(table, candidates, BEDFORD_CONFIG, clean_votes)
    by_name = {r["candidate"]: r for r in rows}
    assert by_name["Brandon Neuman"]["votes"] == "120"
    assert by_name["Brandon Neuman"]["party"] == "DEM"
    assert by_name["John Smith"]["votes"] == "100"


def test_bedford_contest_title_scans_first_six_nonempty_lines():
    # Bedford strips+filters blank lines before taking the first 6 -- a
    # "Vote for" line seven physical (but sixth non-empty) lines down is
    # still found.
    text = "\n".join(["", "", "JUDGE OF THE SUPERIOR COURT (Vote for 1)", "other"])
    result = parse_contest_title(text, BEDFORD_CONFIG)
    assert result == ("JUDGE OF THE SUPERIOR COURT", "", "1")


def test_jefferson_turnout_vote_types_uses_total_subrow():
    clean_votes = make_clean_votes(JEFFERSON_CONFIG)
    table = [
        ["Precinct", "Registered", "Ballots"],
        ["Barnett Township", "", ""],
        ["Election Day", "", "88"],
        ["Mail-In", "", "19"],
        ["Provisional", "", "0"],
        ["Total", "223", "107"],
    ]
    pdf_pages = [("no contest markers here", [table])]
    turnout = _parse_turnout_vote_types(pdf_pages, JEFFERSON_CONFIG, clean_votes)
    assert turnout["Barnett Township"]["registered_voters"] == "223"
    assert turnout["Barnett Township"]["ballots_cast"] == "107"
    assert turnout["Barnett Township"]["election_day"] == "88"
    assert turnout["Barnett Township"]["mail"] == "19"


def test_jefferson_turnout_stops_scanning_at_contest_page():
    clean_votes = make_clean_votes(JEFFERSON_CONFIG)
    table = [["Precinct", "Registered", "Ballots"], ["Barnett Township", "", ""],
              ["Total", "223", "107"]]
    contest_table = [["Precinct", "Registered", "Ballots"], ["Should Not Appear", "", ""],
                       ["Total", "999", "999"]]
    pdf_pages = [
        ("no contest markers here", [table]),
        ("JUDGE (Vote for 1)", [contest_table]),  # 'Vote for' triggers early stop
    ]
    turnout = _parse_turnout_vote_types(pdf_pages, JEFFERSON_CONFIG, clean_votes)
    assert "Should Not Appear" not in turnout


def test_jefferson_candidate_table_vote_types_emits_on_total():
    clean_votes = make_clean_votes(JEFFERSON_CONFIG)
    header = ["Precinct", reversed_header("Brandon Neuman (DEM)")]
    table = [
        header,
        ["Barnett Township", ""],
        ["Election Day", "80"],
        ["Mail-In", "15"],
        ["Provisional", "5"],
        ["Total", "100"],
    ]
    candidates = decode_candidates(header, JEFFERSON_CONFIG)
    state = {"name": None, "sub_data": {}}
    rows = _parse_candidate_table_vote_types(table, candidates, state, JEFFERSON_CONFIG, clean_votes)
    assert len(rows) == 1
    assert rows[0]["candidate"] == "Brandon Neuman"
    assert rows[0]["votes"] == "100"
    assert rows[0]["election_day"] == "80"
    assert rows[0]["mail"] == "15"
    assert rows[0]["provisional"] == "5"


def test_jefferson_redacted_values_become_zero():
    clean_votes = make_clean_votes(JEFFERSON_CONFIG)
    assert clean_votes("****") == "0"
    bedford_clean_votes = make_clean_votes(BEDFORD_CONFIG)
    assert bedford_clean_votes("****") == "****"  # Bedford has no redaction handling


def test_jefferson_precinct_state_persists_across_pages():
    # A precinct block can span a page boundary; state must carry the
    # in-progress sub_data across two _parse_candidate_table_vote_types calls.
    clean_votes = make_clean_votes(JEFFERSON_CONFIG)
    header = ["Precinct", reversed_header("Brandon Neuman (DEM)")]
    page1_table = [header, ["Barnett Township", ""], ["Election Day", "80"]]
    page2_table = [header, ["Mail-In", "15"], ["Provisional", "5"], ["Total", "100"]]

    candidates = decode_candidates(header, JEFFERSON_CONFIG)
    state = {"name": None, "sub_data": {}}
    rows1 = _parse_candidate_table_vote_types(page1_table, candidates, state, JEFFERSON_CONFIG, clean_votes)
    assert rows1 == []  # no Total seen yet
    rows2 = _parse_candidate_table_vote_types(page2_table, candidates, state, JEFFERSON_CONFIG, clean_votes)
    assert len(rows2) == 1
    assert rows2[0]["election_day"] == "80"
    assert rows2[0]["votes"] == "100"
