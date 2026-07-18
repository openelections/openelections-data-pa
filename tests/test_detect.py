from pathlib import Path

import pytest

from oepa.detect import detect_format, extract_text
from oepa.signatures import SIGNATURES

FIXTURES = Path(__file__).parent / "fixtures"


def _detect_text_file(name: str):
    """detect_format() only knows how to read PDFs and a few text-ish
    extensions directly; route our .txt fixtures through the same scoring
    path detect_format() uses for PDFs."""
    text = (FIXTURES / name).read_text()
    from oepa.detect import DetectionResult, FamilyScore

    ranked = []
    for sig in SIGNATURES:
        score, matched = sig.score(text)
        ranked.append(FamilyScore(sig.family, score, matched, sig.suggested_engine, sig.description))
    ranked.sort(key=lambda r: r.score, reverse=True)
    return DetectionResult(input_path=name, ranked=ranked, variant_hints={})


def test_sovc_geo_fixture_scores_highest_for_sovc_geo():
    result = _detect_text_file("sovc_geo.txt")
    assert result.family == "sovc_geo"
    assert result.confidence > 0.7


def test_sovc_crosstab_fixture_scores_highest_for_sovc_crosstab():
    result = _detect_text_file("sovc_crosstab.txt")
    assert result.family == "sovc_crosstab"
    assert result.confidence > 0.8


def test_sovc_geo_and_crosstab_dont_cross_match():
    geo = _detect_text_file("sovc_geo.txt")
    crosstab = _detect_text_file("sovc_crosstab.txt")
    geo_scores = {r.family: r.score for r in geo.ranked}
    crosstab_scores = {r.family: r.score for r in crosstab.ranked}
    assert geo_scores["sovc_crosstab"] < geo_scores["sovc_geo"]
    assert crosstab_scores["sovc_geo"] < crosstab_scores["sovc_crosstab"]


def test_electionware_county_fixture_beats_electionware_np():
    # Single Statistics section + county header line => county-level report,
    # not a per-precinct one, even though both share the Statistics marker.
    result = _detect_text_file("electionware_county.txt")
    assert result.family == "electionware_county"


def test_electionware_np_and_regex_are_textually_indistinguishable():
    # electionware_regex parsers (Indiana, Lackawanna) parse the *same*
    # Electionware precinct-summary text shape as electionware_np -- the
    # difference is which engine happens to have been written for that
    # county, not a text-level signal. Both should score well here.
    for fixture in ("electionware_np.txt", "electionware_regex.txt"):
        result = _detect_text_file(fixture)
        scores = {r.family: r.score for r in result.ranked}
        assert scores["electionware_np"] > 0.4
        assert scores["electionware_regex"] > 0.4
        # And neither should be confused for the geo/crosstab families.
        assert scores["sovc_geo"] < scores["electionware_np"]
        assert scores["sovc_crosstab"] < scores["electionware_np"]


def test_el30_shortcut(tmp_path):
    html = tmp_path / "beaver.html"
    html.write_text("Precinct 1  .  .  DEM John Doe (DEM).   .  100   50   30   20")
    result = detect_format(str(html))
    assert result.family == "el30"
    assert result.confidence >= 0.9


def test_clarity_shortcut_by_extension(tmp_path):
    xml = tmp_path / "detail.xml"
    xml.write_text("<ElectionResult></ElectionResult>")
    result = detect_format(str(xml))
    assert result.family == "clarity"
