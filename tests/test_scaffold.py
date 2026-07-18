import ast

from oepa.scaffold import scaffold_parser, _TEMPLATE_FOR_FAMILY
from oepa import PARSERS_DIR

FIXTURES = {
    "electionware_np": "tests/fixtures/electionware_np.txt",
    "sovc_geo": "tests/fixtures/sovc_geo.txt",
    "electionware_regex": "tests/fixtures/electionware_regex.txt",
    "sovc_crosstab": "tests/fixtures/sovc_crosstab.txt",
}


def _cleanup(path):
    if path.exists():
        path.unlink()


def test_every_scaffoldable_family_renders_valid_python():
    for family, fixture in FIXTURES.items():
        county = f"scafftest_{family}"
        output_path = PARSERS_DIR / f"pa_{county}_general_2025_results_parser.py"
        _cleanup(output_path)
        try:
            rc = scaffold_parser(fixture, county=county, family=family)
            assert rc == 0, f"{family} scaffold failed"
            assert output_path.exists()
            source = output_path.read_text()
            ast.parse(source)  # raises SyntaxError if the template didn't render cleanly
        finally:
            _cleanup(output_path)


def test_refuses_to_overwrite_existing_file():
    county = "scafftest_overwrite"
    output_path = PARSERS_DIR / f"pa_{county}_general_2025_results_parser.py"
    _cleanup(output_path)
    try:
        rc1 = scaffold_parser(FIXTURES["electionware_np"], county=county, family="electionware_np")
        assert rc1 == 0
        rc2 = scaffold_parser(FIXTURES["electionware_np"], county=county, family="electionware_np")
        assert rc2 == 1
    finally:
        _cleanup(output_path)


def test_unknown_family_errors_cleanly():
    rc = scaffold_parser(FIXTURES["electionware_np"], county="nope", family="not_a_real_family")
    assert rc == 1


def test_auto_detect_family_when_not_specified():
    county = "scafftest_autodetect"
    output_path = PARSERS_DIR / f"pa_{county}_general_2025_results_parser.py"
    _cleanup(output_path)
    try:
        rc = scaffold_parser(FIXTURES["sovc_crosstab"], county=county, family=None)
        assert rc == 0
        assert output_path.exists()
    finally:
        _cleanup(output_path)
