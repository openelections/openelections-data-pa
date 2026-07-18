import csv

from oepa.checks import ballots_cast_sanity, run_checks


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_clean_precinct_no_mismatches(tmp_path):
    path = tmp_path / "clean.csv"
    fieldnames = ["county", "precinct", "office", "district", "party", "candidate", "vote_for", "votes"]
    rows = [
        {"county": "X", "precinct": "P1", "office": "", "candidate": "Registered Voters", "votes": "500"},
        {"county": "X", "precinct": "P1", "office": "", "candidate": "Ballots Cast", "votes": "300"},
        {"county": "X", "precinct": "P1", "office": "Sheriff", "candidate": "A", "vote_for": "1", "votes": "150"},
        {"county": "X", "precinct": "P1", "office": "Sheriff", "candidate": "B", "vote_for": "1", "votes": "140"},
    ]
    _write_csv(path, fieldnames, rows)
    assert ballots_cast_sanity(str(path)) == []


def test_overcounted_office_flagged(tmp_path):
    path = tmp_path / "broken.csv"
    fieldnames = ["county", "precinct", "office", "candidate", "vote_for", "votes"]
    rows = [
        {"county": "X", "precinct": "P1", "office": "", "candidate": "Ballots Cast", "votes": "300"},
        {"county": "X", "precinct": "P1", "office": "Sheriff", "candidate": "A", "vote_for": "1", "votes": "9000"},
    ]
    _write_csv(path, fieldnames, rows)
    mismatches = ballots_cast_sanity(str(path))
    assert len(mismatches) == 1
    assert mismatches[0]["precinct"] == "P1"


def test_multiseat_race_uses_vote_for_to_avoid_false_positive(tmp_path):
    path = tmp_path / "multiseat.csv"
    fieldnames = ["county", "precinct", "office", "candidate", "vote_for", "votes"]
    rows = [
        {"county": "X", "precinct": "P1", "office": "", "candidate": "Ballots Cast", "votes": "300"},
        {"county": "X", "precinct": "P1", "office": "School Director", "candidate": "A", "vote_for": "4", "votes": "280"},
        {"county": "X", "precinct": "P1", "office": "School Director", "candidate": "B", "vote_for": "4", "votes": "270"},
        {"county": "X", "precinct": "P1", "office": "School Director", "candidate": "C", "vote_for": "4", "votes": "260"},
        {"county": "X", "precinct": "P1", "office": "School Director", "candidate": "D", "vote_for": "4", "votes": "250"},
    ]
    _write_csv(path, fieldnames, rows)
    # sum=1060, cap=300*4=1200 -- within bounds thanks to vote_for
    assert ballots_cast_sanity(str(path)) == []


def test_missing_vote_for_column_defaults_to_one(tmp_path):
    path = tmp_path / "no_vote_for.csv"
    fieldnames = ["county", "precinct", "office", "candidate", "votes"]
    rows = [
        {"county": "X", "precinct": "P1", "office": "", "candidate": "Ballots Cast", "votes": "300"},
        {"county": "X", "precinct": "P1", "office": "Sheriff", "candidate": "A", "votes": "9000"},
    ]
    _write_csv(path, fieldnames, rows)
    assert len(ballots_cast_sanity(str(path))) == 1


def test_county_level_file_without_precinct_column_is_skipped(tmp_path):
    path = tmp_path / "county_level.csv"
    fieldnames = ["county", "office", "candidate", "votes"]
    rows = [{"county": "X", "office": "Sheriff", "candidate": "A", "votes": "9000"}]
    _write_csv(path, fieldnames, rows)
    assert ballots_cast_sanity(str(path)) == []


def test_run_checks_returns_summary_line(tmp_path):
    path = tmp_path / "clean.csv"
    fieldnames = ["county", "precinct", "office", "candidate", "vote_for", "votes"]
    rows = [{"county": "X", "precinct": "P1", "office": "", "candidate": "Ballots Cast", "votes": "300"}]
    _write_csv(path, fieldnames, rows)
    mismatches, summary = run_checks(str(path))
    assert mismatches == []
    assert "verification" in summary
