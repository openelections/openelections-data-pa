from oepa import REPO_ROOT
from oepa.registry import PARSERS, FAMILY_ENGINES


def test_every_script_path_exists():
    missing = [e.script for e in PARSERS if e.script and not (REPO_ROOT / e.script).exists()]
    assert not missing, f"Registered scripts missing on disk: {missing}"


def test_every_family_engine_script_exists():
    missing = [eng.script for eng in FAMILY_ENGINES.values() if not (REPO_ROOT / eng.script).exists()]
    assert not missing, f"Family engine scripts missing on disk: {missing}"


def test_no_duplicate_county_election_level_family():
    seen = set()
    dupes = []
    for e in PARSERS:
        key = (e.county, e.election, e.level, e.family)
        if key in seen:
            dupes.append(key)
        seen.add(key)
    assert not dupes, f"Duplicate registry entries: {dupes}"


def test_non_invocable_entries_have_usage():
    for e in PARSERS:
        if not e.invocable:
            assert e.usage, f"{e.county}/{e.family} is non-invocable but has no usage string"
