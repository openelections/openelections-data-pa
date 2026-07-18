"""Tests for the shared llm_pdf_extract module: config/fieldname selection
per (mode, level), county auto-detection from filename, and that each
prompt still contains the county-specific instructions it had before the
merge (precinct's office-normalization map and metadata-row instructions,
which the county/text prompts don't have).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parsers"))

from llm_pdf_extract import (  # noqa: E402
    _config_for,
    detect_county_from_filename,
    _prompt_text_county,
    _prompt_image_county,
    _prompt_image_precinct,
)


def test_text_county_fieldnames_have_no_vote_type_breakdown():
    config = _config_for("text", "county")
    assert config["fieldnames"] == ["county", "office", "district", "party", "candidate", "votes"]


def test_image_county_fieldnames_include_vote_types_but_no_precinct():
    config = _config_for("image", "county")
    assert "precinct" not in config["fieldnames"]
    assert "election_day" in config["fieldnames"]


def test_image_precinct_fieldnames_include_precinct_and_vote_types():
    config = _config_for("image", "precinct")
    assert config["fieldnames"][:2] == ["county", "precinct"]
    assert "election_day" in config["fieldnames"]


def test_unknown_mode_level_raises():
    import pytest
    with pytest.raises(ValueError):
        _config_for("text", "precinct")  # no text+precinct parser exists


def test_detect_county_from_filename():
    assert detect_county_from_filename("Bradford County PA Final Results.pdf") == "Bradford"
    assert detect_county_from_filename("forest pa summary.pdf") == "Forest"
    # Regex requires whitespace before the marker word, not arbitrary
    # separators -- an underscore-joined filename won't match.
    assert detect_county_from_filename("forest_pa_summary.pdf") is None


def test_precinct_prompt_has_office_normalization_map_others_dont():
    precinct_prompt = _prompt_image_precinct(1, "Forest")
    county_prompt = _prompt_image_county(1, "Forest")
    text_prompt = _prompt_text_county("some text", 1, "Forest")

    assert "PRESIDENTIAL ELECTORS -> President" in precinct_prompt
    assert "PRESIDENTIAL ELECTORS -> President" not in county_prompt
    assert "PRESIDENTIAL ELECTORS -> President" not in text_prompt


def test_precinct_prompt_asks_for_registered_voters_metadata_rows():
    precinct_prompt = _prompt_image_precinct(1, "Forest")
    assert 'office="Registered Voters"' in precinct_prompt
    county_prompt = _prompt_image_county(1, "Forest")
    assert 'office="Registered Voters"' not in county_prompt


def test_all_prompts_share_the_retention_yesno_convention():
    for prompt in (
        _prompt_text_county("txt", 1, "Forest"),
        _prompt_image_county(1, "Forest"),
        _prompt_image_precinct(1, "Forest"),
    ):
        assert 'party="YES", candidate=""' in prompt


def test_text_prompt_embeds_page_text_others_dont_have_a_text_block():
    text_prompt = _prompt_text_county("UNIQUE_MARKER_TEXT", 1, "Forest")
    assert "UNIQUE_MARKER_TEXT" in text_prompt
    image_prompt = _prompt_image_county(1, "Forest")
    assert "TEXT:" not in image_prompt
