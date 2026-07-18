"""Behavioral tests for the Dauphin County precinct scraper.

The scraper walks three page types on elections.dauphincounty.gov:

  /?key=41                     -> index, links to one page per race
  /?key=41&race=GOVERNOR       -> race page, links to one /Races page per contest
  /Races?key=41&race=(D) ...   -> precinct results for a single contest

These tests drive the pure parse_* functions against saved fixtures of each,
so no network access is needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))

from pa_dauphin_general_2025_precinct_scraper import (  # noqa: E402
    parse_race_list,
    parse_contest_links,
    split_contest,
    parse_precinct_results,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text()


class TestParseRaceList:
    def test_finds_race_pages_for_the_configured_election(self):
        urls = parse_race_list(fixture("dauphin_index.html"))
        assert urls == [
            "https://elections.dauphincounty.gov/?key=41&race=BERRYSBURG",
            "https://elections.dauphincounty.gov/?key=41&race=GOVERNOR",
            "https://elections.dauphincounty.gov/?key=41&race=HD 103",
        ]

    def test_ignores_races_belonging_to_other_elections(self):
        # /?key=31&...&race=STATE JUDGES is a different election's race.
        urls = parse_race_list(fixture("dauphin_index.html"))
        assert not any("key=31" in u for u in urls)

    def test_ignores_links_without_a_race_param(self):
        urls = parse_race_list(fixture("dauphin_index.html"))
        assert not any(u.endswith("?key=41") for u in urls)


class TestParseContestLinks:
    def test_extracts_one_races_link_per_contest(self):
        links = parse_contest_links(fixture("dauphin_race.html"))
        assert [c["contest"] for c in links] == [
            "(D) GOVERNOR (DEM)",
            "(R) GOVERNOR (REP)",
            "(D) DEMOCRATIC COMMITTEEMAN - BERRYSBURG (DEM)",
        ]

    def test_builds_absolute_urls(self):
        links = parse_contest_links(fixture("dauphin_race.html"))
        assert links[0]["url"] == (
            "https://elections.dauphincounty.gov/Races?key=41&race=%28D%29%20GOVERNOR%20%28DEM%29"
        )

    def test_ignores_the_by_ballot_type_link(self):
        links = parse_contest_links(fixture("dauphin_race.html"))
        assert not any("PreceinctByBallot" in c["url"] for c in links)


class TestSplitContest:
    def test_strips_party_prefix_and_suffix(self):
        assert split_contest("(D) GOVERNOR (DEM)") == ("GOVERNOR", "DEM")
        assert split_contest("(R) LIEUTENANT GOVERNOR (REP)") == ("LIEUTENANT GOVERNOR", "REP")

    def test_keeps_district_bearing_office_text_intact(self):
        # normalize_office needs the "- HD103" tail to pull the district number.
        assert split_contest("(D) REPRESENTATIVE IN THE GENERAL ASSEMBLY - HD103 (DEM)") == (
            "REPRESENTATIVE IN THE GENERAL ASSEMBLY - HD103",
            "DEM",
        )

    def test_passes_through_unrecognized_shapes(self):
        assert split_contest("GOVERNOR") == ("GOVERNOR", "")


class TestParsePrecinctResults:
    def test_emits_one_row_per_precinct_candidate_pair(self):
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"), "GOVERNOR", "(D) GOVERNOR (DEM)"
        )
        assert len(rows) == 6  # 3 precincts x 2 candidates

    def test_maps_votes_to_the_right_candidate(self):
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"), "GOVERNOR", "(D) GOVERNOR (DEM)"
        )
        assert rows[0] == {
            "county": "Dauphin",
            "precinct": "CITY--1ST WARD, 1ST PRECINCT",
            "office": "Governor",
            "district": "",
            "party": "DEM",
            "candidate": "JOSH SHAPIRO",
            "votes": "86",
        }
        assert rows[1]["candidate"] == "WRITE-IN1"
        assert rows[1]["votes"] == "3"

    def test_does_not_double_count_nested_tables(self):
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"), "GOVERNOR", "(D) GOVERNOR (DEM)"
        )
        keys = [(r["precinct"], r["candidate"]) for r in rows]
        assert len(keys) == len(set(keys))

    def test_skips_the_municipality_rollup_tab(self):
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"), "GOVERNOR", "(D) GOVERNOR (DEM)"
        )
        assert "City of Harrisburg" not in [r["precinct"] for r in rows]
        # "Berrysburg Borough" is a municipality row; "BERRYSBURG BOROUGH" is a precinct.
        assert "Berrysburg Borough" not in [r["precinct"] for r in rows]
        assert "BERRYSBURG BOROUGH" in [r["precinct"] for r in rows]

    def test_keeps_zero_vote_rows(self):
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"), "GOVERNOR", "(D) GOVERNOR (DEM)"
        )
        zero = [r for r in rows if r["precinct"] == "BERRYSBURG BOROUGH"]
        assert [r["votes"] for r in zero] == ["0", "0"]

    def test_skips_contests_with_no_standardized_office(self):
        # normalize_office returns None for party committee races.
        rows = parse_precinct_results(
            fixture("dauphin_precincts.html"),
            "BERRYSBURG",
            "(D) DEMOCRATIC COMMITTEEMAN - BERRYSBURG (DEM)",
        )
        assert rows == []
