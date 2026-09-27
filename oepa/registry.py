"""Static registry mapping counties/families to their parser implementations.

This is a hand-maintained table, not a decorator-based auto-discovery system:
most of the ~60 parser scripts in ``parsers/`` are one-off scripts whose only
entry point is an ``argv``-driven ``main()``, so importing all of them at CLI
startup would be slow and fragile. Instead each entry records enough to either
shell out to the existing script (``script``, preserving its exact argv
contract) or call an in-process engine (``runner``, for families migrated
onto a shared config-driven engine with structured verification output).

When adding a new county parser, add one ``ParserEntry`` here.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from oepa import PARSERS_DIR, REPO_ROOT

GENERAL_2025 = "20251104__pa__general"


@dataclass(frozen=True)
class ParserEntry:
    county: str
    election: str
    level: str  # "precinct" | "county" | "other"
    family: str
    script: Optional[str] = None  # repo-relative path, e.g. "parsers/pa_wayne_general_2025_results_parser.py"
    runner: Optional[str] = None  # "module:factory" for in-process families (set once migrated)
    extra_args: tuple = ()
    invocable: bool = True  # False for legacy scripts with hardcoded I/O paths (no argv contract)
    usage: str = ""  # shown when invocable is False, or when argv shape isn't (input, output)
    notes: str = ""
    supports_strict: bool = False  # script's own run_cli understands a trailing --strict flag


@dataclass(frozen=True)
class FamilyEngine:
    """A generic, multi-county engine invoked directly with a county name.

    Unlike ParserEntry these aren't tied to one county; the CLI can route a
    detected PDF to one of these via ``oepa parse --family X --county Y``.
    """

    family: str
    level: str
    script: str
    county_arg_style: str  # "positional" | "flag" | "none"
    usage: str
    notes: str = ""


# --- electionware_precinct_np family: 21 counties on the mature shared engine ---
# All share the ElectionwareConfig/run_cli pattern in electionware_precinct_np.py.
_ELECTIONWARE_NP_COUNTIES = [
    "beaver", "berks", "blair", "cameron", "centre", "chester", "clearfield", "elk",
    "franklin", "huntingdon", "juniata", "lawrence", "lebanon", "mercer",
    "mifflin", "montour", "northampton", "northumberland", "potter",
    "snyder", "tioga", "washington",
]

# --- sovc_geo family: natural_pdf regex parsers over "Statement of Votes Cast
# by Geography"-style reports. Wayne and Lycoming are near-clones (Phase 3
# migrated both onto the shared parsers/sovc_geo_np.py engine, config-driven
# by SovcGeoConfig). Fulton is a structural outlier -- keyword-based office
# detection, party-coded candidates with continuation lines, no vote_for or
# write-in accumulation -- and was deliberately NOT folded into the shared
# engine: with no source PDF checked into the repo to golden-test against,
# forcing it into the abstraction would risk silently changing its output.
_SOVC_GEO_COUNTIES = ["wayne", "lycoming"]

# --- electionware_regex family: natural_pdf hand-rolled regexes over
# Electionware precinct summaries. Phase 3 migrated both onto the shared
# parsers/electionware_regex_np.py engine (ElectionwareRegexConfig).
_ELECTIONWARE_REGEX_COUNTIES = ["indiana", "lackawanna"]

# --- sovc_crosstab family: pdfplumber parsers over rotated/reversed
# "Statement of Votes Cast" crosstabs. Phase 4 migrated both onto the shared
# parsers/sovc_crosstab_pp.py engine (SovcCrosstabConfig; vote_type_rows
# selects Jefferson's Election Day/Mail-In/Provisional/Total sub-row shape
# vs Bedford's one-row-per-precinct shape).
_SOVC_CROSSTAB_COUNTIES = ["bedford", "jefferson"]

PARSERS: list[ParserEntry] = []

for _county in _ELECTIONWARE_NP_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=GENERAL_2025,
        level="precinct",
        family="electionware_np",
        script=f"parsers/pa_{_county}_general_2025_results_parser.py",
    ))

for _county in _SOVC_GEO_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=GENERAL_2025,
        level="precinct",
        family="sovc_geo",
        script=f"parsers/pa_{_county}_general_2025_results_parser.py",
        notes="Config wrapper over parsers/sovc_geo_np.py (SovcGeoConfig)",
        supports_strict=True,  # engine captures printed per-contest Total lines (see sovc_geo_np.check_printed_totals)
    ))

# Fulton: sovc_geo-shaped report, but structurally different enough that it
# stays a standalone script rather than a sovc_geo_np.py config (see comment
# on _SOVC_GEO_COUNTIES above).
PARSERS.append(ParserEntry(
    county="fulton",
    election=GENERAL_2025,
    level="precinct",
    family="sovc_geo",
    script="parsers/pa_fulton_general_2025_results_parser.py",
    notes="Standalone; not migrated onto sovc_geo_np.py (structural outlier, see registry.py comment)",
))

for _county in _ELECTIONWARE_REGEX_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=GENERAL_2025,
        level="precinct",
        family="electionware_regex",
        script=f"parsers/pa_{_county}_general_2025_results_parser.py",
        notes="Config wrapper over parsers/electionware_regex_np.py (ElectionwareRegexConfig)",
    ))

for _county in _SOVC_CROSSTAB_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=GENERAL_2025,
        level="precinct",
        family="sovc_crosstab",
        script=f"parsers/pa_{_county}_general_2025_results_parser.py",
        notes="Config wrapper over parsers/sovc_crosstab_pp.py (SovcCrosstabConfig)",
    ))

# --- one-off parsers: working, not scheduled for migration; registered so
# the CLI can route to them and `oepa list` surfaces them. ---

# --- 2023 general: per-county custom parsers over the official county source
# files (formats vary: Electionware summaries, Dominion, SOVC, Clarity XML,
# HTML/xlsx exports, OCR'd scans). All take (input, output) argv; input is the
# extracted text/PDF from openelections-sources-pa 2023/general. Berks is
# parsed by an ad-hoc script not preserved under the standard name (its 2023
# outputs are committed), so it is not registered here. ---
_E2023 = "20231107__pa__general"
_2023_COUNTIES = [
    "adams", "allegheny", "armstrong", "beaver", "bedford", "blair",
    "bradford", "bucks", "butler", "cambria", "cameron", "carbon", "centre",
    "chester", "clarion", "clearfield", "clinton", "crawford", "cumberland",
    "delaware", "elk", "erie", "fayette", "forest", "franklin", "fulton",
    "greene",
    "huntingdon", "indiana", "jefferson", "juniata", "lackawanna",
    "lancaster", "lawrence", "lebanon", "lehigh", "luzerne", "lycoming",
    "mercer", "mifflin", "monroe",
    "montgomery", "montour", "northampton", "northumberland", "perry",
    "philadelphia", "pike", "potter", "schuylkill", "snyder", "somerset",
    "susquehanna", "sullivan", "tioga", "union", "venango", "warren",
    "washington", "wayne", "westmoreland", "wyoming", "york",
]
for _county in _2023_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=_E2023,
        level="precinct",
        family="custom_2023",
        script=f"parsers/pa_{_county}_general_2023_results_parser.py",
    ))
# Forest consumes the PaddleOCR-VL text extract, not the raw PDF; it yields a
# precinct-level file (aggregated to county level by work2023/agg_county.py).
for _e in list(PARSERS):
    if _e.election == _E2023 and _e.county == "forest":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="forest", election=_E2023, level="precinct",
            family="ocr_custom",
            script="parsers/pa_forest_general_2023_results_parser.py",
            usage="python parsers/pa_forest_general_2023_results_parser.py work2023/txt/Forest__ocr.txt <out.csv> (OCR text from work2023/ocr.py, not the raw PDF)",
        ))
    if _e.election == _E2023 and _e.county == "sullivan":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="sullivan", election=_E2023, level="county",
            family="ocr_custom",
            script="parsers/pa_sullivan_general_2023_results_parser.py",
            usage="python parsers/pa_sullivan_general_2023_results_parser.py work2023/txt/Sullivan__ocr.txt <out.csv> (OCR text from work2023/ocr.py, not the raw PDF; county-level only — the Statement of Votes Cast reports countywide totals with no per-precinct breakdown)",
        ))
    if _e.election == _E2023 and _e.county == "fulton":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="fulton", election=_E2023, level="county",
            family="ocr_custom",
            script="parsers/pa_fulton_general_2023_results_parser.py",
            usage="python parsers/pa_fulton_general_2023_results_parser.py work2023/txt/Fulton__ocr.txt <out.csv> (OCR text from work2023/ocr.py, not the raw PDF; county-level only — the source carries no per-precinct data)",
        ))

# --- 2023 primary: per-county custom parsers over the official county source
# files (same mix as the 2023 general block above; sources under
# openelections-sources-pa 2023/primary). Seven counties (clarion, clinton,
# fayette, huntingdon, indiana, luzerne, westmoreland) have county-level
# sources only — no precinct file exists for them. Crawford and wyoming parse
# PaddleOCR-VL text extracts rather than the raw PDFs. ---
_E2023P = "20230516__pa__primary"
_2023P_COUNTY_ONLY = [
    "clarion", "clinton", "fayette", "huntingdon", "indiana", "luzerne",
    "westmoreland",
]
for _county in _2023P_COUNTY_ONLY:
    PARSERS.append(ParserEntry(
        county=_county,
        election=_E2023P,
        level="county",
        family="custom_2023",
        script=f"parsers/pa_{_county}_primary_2023_results_parser.py",
    ))
_2023P_COUNTIES = [
    "adams", "allegheny", "beaver", "berks", "blair", "carbon", "centre",
    "chester", "clearfield", "crawford", "erie", "jefferson", "juniata",
    "lackawanna", "lawrence", "lebanon", "lycoming", "mercer", "tioga",
    "union", "venango", "washington", "wayne", "wyoming",
]
for _county in _2023P_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=_E2023P,
        level="county",
        family="custom_2023",
        script=f"parsers/pa_{_county}_primary_2023_results_parser.py",
    ))
    PARSERS.append(ParserEntry(
        county=_county,
        election=_E2023P,
        level="precinct",
        family="custom_2023",
        script=f"parsers/pa_{_county}_primary_2023_results_parser.py",
    ))
# Crawford's sources are PaddleOCR-VL OCR text (some county totals
# vision-transcribed where OCR stubbed), not the raw PDFs.
for _e in list(PARSERS):
    if _e.election == _E2023P and _e.county == "crawford" and _e.level == "county":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="crawford", election=_E2023P, level="county",
            family="ocr_custom",
            script="parsers/pa_crawford_primary_2023_results_parser.py",
            usage="python parsers/pa_crawford_primary_2023_results_parser.py work2023p/txt/Crawford__Rep_County_Results_2023_Primary__ocr.txt <out.csv> (OCR text; see work2023p/validate/crawford.md)",
        ))
    if _e.election == _E2023P and _e.county == "crawford" and _e.level == "precinct":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="crawford", election=_E2023P, level="precinct",
            family="ocr_custom",
            script="parsers/pa_crawford_primary_2023_results_parser.py",
            usage="python parsers/pa_crawford_primary_2023_results_parser.py work2023p/txt/Crawford__Rep_Local_Results_2023_Primary__ocr.txt <out.csv> (OCR text; see work2023p/validate/crawford.md)",
        ))
    if _e.election == _E2023P and _e.county == "wyoming" and _e.level == "precinct":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="wyoming", election=_E2023P, level="precinct",
            family="ocr_custom",
            script="parsers/pa_wyoming_primary_2023_results_parser.py",
            usage="python parsers/pa_wyoming_primary_2023_results_parser.py work2023p/txt/Wyoming__Precinct_Tally_2023_Primary__ocr.txt <out.csv> (OCR text; precinct-level source only, county rows aggregated; see work2023p/validate/wyoming.md)",
        ))

PARSERS.extend([
    ParserEntry("bradford", GENERAL_2025, "precinct", "pdfplumber_custom",
                script="parsers/pa_bradford_general_2025_results_parser.py"),
    ParserEntry("bucks", GENERAL_2025, "precinct", "pdfplumber_custom",
                script="parsers/pa_bucks_general_2025_results_parser.py"),
    ParserEntry("philadelphia", GENERAL_2025, "precinct", "pandas_custom",
                script="parsers/pa_philadelphia_general_2025_results_parser.py"),
    ParserEntry("carbon", GENERAL_2025, "precinct", "regex_custom",
                script="parsers/pa_carbon_general_2025_results_parser.py"),
    ParserEntry("carbon", GENERAL_2025, "county", "regex_custom",
                script="parsers/pa_carbon_county_2025_results_parser.py"),
    ParserEntry("wyoming", GENERAL_2025, "precinct", "text_subprocess",
                script="parsers/pa_wyoming_general_2025_results_parser.py",
                notes="Shells out to pdftotext internally"),
    ParserEntry("wyoming", GENERAL_2025, "county", "regex_custom",
                script="parsers/pa_wyoming_general_2025_county_parser.py"),
    ParserEntry("perry", GENERAL_2025, "precinct", "text_subprocess",
                script="parsers/pa_perry_general_2025_precinct_parser.py",
                notes="Shells out to pdftotext internally"),
    ParserEntry("crawford", GENERAL_2025, "precinct", "pandas_custom",
                script="parsers/pa_crawford_general_2025_results_parser.py"),
    ParserEntry("lancaster", GENERAL_2025, "precinct", "text_subprocess",
                script="parsers/pa_lancaster_general_2025_results_parser.py",
                notes="Shells out to pdftotext internally"),
    ParserEntry("warren", GENERAL_2025, "precinct", "text_subprocess",
                script="parsers/pa_warren_general_2025_results_parser.py",
                notes="Shells out to pdftotext internally"),
])

# --- 2022 general: precinct-level parsers for the 24 counties added after the
# original 2022/counties/ set (sources under openelections-sources-pa
# 2022/general). Formats span Electionware summaries, Dominion SOVC variants
# (some OCR'd scans), Hart eSlate results books, SOVC crosstabs and
# SpreadsheetML/.xlsx exports. Sources live outside the repo; clarion, wayne
# and wyoming parse PaddleOCR-VL text extracts, fayette a pdftotext -layout
# extract. ---
_E2022 = "20221108__pa__general"
_2022_COUNTIES = [
    "adams", "allegheny", "armstrong", "bedford", "bradford", "carbon",
    "columbia", "crawford", "erie", "fayette", "forest", "fulton",
    "lebanon", "lycoming", "mercer", "montgomery", "potter", "sullivan",
    "union", "venango", "warren", "wayne", "wyoming",
]
for _county in _2022_COUNTIES:
    PARSERS.append(ParserEntry(
        county=_county,
        election=_E2022,
        level="precinct",
        family="custom_2022",
        script=f"parsers/pa_{_county}_general_2022_results_parser.py",
    ))
# clarion/wayne/wyoming consume PaddleOCR-VL text extracts (scanned sources).
for _e in list(PARSERS):
    if _e.election == _E2022 and _e.county in ("clarion", "wayne", "wyoming"):
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county=_e.county, election=_E2022, level="precinct",
            family="ocr_custom",
            script=f"parsers/pa_{_e.county}_general_2022_results_parser.py",
            usage=f"python parsers/pa_{_e.county}_general_2022_results_parser.py <PaddleOCR text> <out.csv> (OCR text via work2023p/ocr_api.py, not the raw PDF)",
        ))
# clarion has no base entry (not in _2022_COUNTIES above); add it directly.
PARSERS.append(ParserEntry(
    county="clarion", election=_E2022, level="precinct",
    family="ocr_custom",
    script="parsers/pa_clarion_general_2022_results_parser.py",
    usage="python parsers/pa_clarion_general_2022_results_parser.py <PaddleOCR text> <out.csv> (OCR text via work2023p/ocr_api.py, not the raw PDF)",
))
# fayette consumes the pdftotext -layout extract of its 2,645-page SOVC.
for _e in list(PARSERS):
    if _e.election == _E2022 and _e.county == "fayette":
        PARSERS.remove(_e)
        PARSERS.append(ParserEntry(
            county="fayette", election=_E2022, level="precinct",
            family="custom_2022",
            script="parsers/pa_fayette_general_2022_results_parser.py",
            usage="python parsers/pa_fayette_general_2022_results_parser.py <pdftotext -layout text> <out.csv> (text extract, not the raw PDF)",
        ))

# --- scrapers: fetch live results over HTTP rather than parsing a local PDF.
# `parse` can still shell out to them (they ignore the input-file argument),
# but they're conceptually different from PDF parsers.
PARSERS.extend([
    ParserEntry("dauphin", GENERAL_2025, "county", "scraper",
                script="parsers/pa_dauphin_general_2025_scraper.py",
                notes="Fetches results live from the Dauphin County site; input arg unused"),
    ParserEntry("dauphin", GENERAL_2025, "precinct", "scraper",
                script="parsers/pa_dauphin_general_2025_precinct_scraper.py",
                notes="Fetches results live from the Dauphin County site; input arg unused"),
    ParserEntry("philadelphia", GENERAL_2025, "other", "scraper",
                script="parsers/pa_philadelphia_general_2025_boardworkers_scraper.py",
                notes="Scrapes poll worker/board data, not election results"),
])

# --- legacy: historical, hardcoded-path scripts kept for archival purposes.
# Not part of this consolidation effort (user decision: leave untouched).
# Not invocable through the standard (input, output) argv contract.
PARSERS.extend([
    ParserEntry("greene", "20201103__pa__general", "precinct", "legacy",
                script="parsers/greene_parser.py", invocable=False,
                usage="python parsers/greene_parser.py  (no args; scrapes hardcoded precinct list/URL, writes to a fixed filename)"),
    ParserEntry("lehigh", "unknown", "precinct", "legacy",
                script="parsers/lehigh_parser.py", invocable=False,
                usage="python parsers/lehigh_parser.py <precinct|county> <input_csv> <output_csv> [county_name]",
                notes="argv shape differs from the (input, output) convention"),
    ParserEntry("monroe", "20250520__pa__primary", "precinct", "legacy",
                script="parsers/monroe_parser.py", invocable=False,
                usage="python parsers/monroe_parser.py  (no args; hardcoded URL and output filename)"),
    ParserEntry("unknown", "unknown", "precinct", "legacy",
                script="parsers/er_parser.py", invocable=False,
                usage="python parsers/er_parser.py  (no args; hardcoded output filename)"),
    ParserEntry("beaver", "20181106__pa__general", "precinct", "el30",
                script="parsers/el30_parser.py", invocable=False,
                usage="python parsers/el30_parser.py  (no args; hardcoded input/output filenames)"),
    ParserEntry("butler", "20181106__pa__general", "precinct", "el30",
                script="parsers/el30a_parser.py", invocable=False,
                usage="python parsers/el30a_parser.py  (no args; hardcoded input/output filenames)"),
    ParserEntry("westmoreland", "20181106__pa__general", "precinct", "el30",
                script="parsers/el30b_parser.py", invocable=False,
                usage="python parsers/el30b_parser.py  (no args; hardcoded input/output filenames)"),
    ParserEntry("unknown", "unknown", "other", "clarity",
                script="parsers/clarity_parser.py", invocable=False,
                usage="library of functions; no argv entry point, hardcoded example URL",
                notes="Appears to reference a non-PA (WV) example URL; likely a template/reference"),
    ParserEntry("unknown", "unknown", "other", "legacy",
                script="parsers/electionware_parser.py", invocable=False,
                usage="no __main__; imports via `parsers.pa_pdf_parser`, requires running from repo root as a package"),
    ParserEntry("tioga", "20200602__pa__primary", "precinct", "legacy",
                script="parsers/electionware2csv.py", invocable=False,
                usage="python parsers/electionware2csv.py  (no args; hardcoded input/output filenames)"),
])

# --- generic multi-county engines: not tied to a single county. Route via
# `oepa parse --family <family> --county <Name> input output`.
FAMILY_ENGINES: dict[str, FamilyEngine] = {
    "electionware_text_county": FamilyEngine(
        family="electionware_text_county", level="county",
        script="parsers/electionware_text_county.py",
        county_arg_style="positional",
        usage="python parsers/electionware_text_county.py <pdf_path> <output_csv> [county_name]",
    ),
    "electionware_text_precinct": FamilyEngine(
        family="electionware_text_precinct", level="precinct",
        script="parsers/electionware_text_precinct.py",
        county_arg_style="none",
        usage="python parsers/electionware_text_precinct.py <pdf_path> <output_csv>",
    ),
    "electionware_county": FamilyEngine(
        family="electionware_county", level="county",
        script="parsers/electionware_county.py",
        county_arg_style="none",
        usage="python parsers/electionware_county.py <pdf_path> <output_csv>",
    ),
    "electionware_precinct_legacy": FamilyEngine(
        family="electionware_precinct_legacy", level="precinct",
        script="parsers/electionware_precinct.py",
        county_arg_style="none",
        usage="python parsers/electionware_precinct.py <pdf_path> <output_csv>",
        notes="Superseded by electionware_precinct_np.py; kept for reference",
    ),
    "csv_converter": FamilyEngine(
        family="csv_converter", level="other",
        script="parsers/csv_converter.py",
        county_arg_style="positional",
        usage="python parsers/csv_converter.py <input_file> <output_file> <county>",
    ),
    # Phase 5 merged all three LLM parsers' shared extraction/CLI/CSV logic
    # into parsers/llm_pdf_extract.py; each script below is now a one-line
    # wrapper calling run_cli(mode=..., level=...). Prompt wording stays
    # distinct per (mode, level) rather than templated -- see that module's
    # docstring for why.
    "llm_text": FamilyEngine(
        family="llm_text", level="county",
        script="parsers/pa_bradford_llm_parser.py",
        county_arg_style="flag",
        usage="python parsers/pa_bradford_llm_parser.py <pdf> [output] --county NAME [--test-page N]",
        notes="Text-extraction + Claude; county-level output; wraps llm_pdf_extract.run_cli(mode='text', level='county')",
    ),
    "llm_image_county": FamilyEngine(
        family="llm_image_county", level="county",
        script="parsers/pa_county_llm_attachment_parser.py",
        county_arg_style="flag",
        usage="python parsers/pa_county_llm_attachment_parser.py <pdf> [output] --county NAME [--test-page N]",
        notes="Image-attachment + Claude; county-level output; wraps llm_pdf_extract.run_cli(mode='image', level='county')",
    ),
    "llm_image_precinct": FamilyEngine(
        family="llm_image_precinct", level="precinct",
        script="parsers/pa_precinct_llm_attachment_parser.py",
        county_arg_style="flag",
        usage="python parsers/pa_precinct_llm_attachment_parser.py <pdf> [output] --county NAME [--test-page N]",
        notes="Image-attachment + Claude; precinct-level output; wraps llm_pdf_extract.run_cli(mode='image', level='precinct')",
    ),
}


class RegistryError(Exception):
    pass


def find_entries(county: Optional[str] = None, election: Optional[str] = None,
                  family: Optional[str] = None, level: Optional[str] = None) -> list[ParserEntry]:
    results = PARSERS
    if county:
        results = [e for e in results if e.county.lower() == county.lower()]
    if election:
        results = [e for e in results if e.election == election]
    if family:
        results = [e for e in results if e.family == family]
    if level:
        results = [e for e in results if e.level == level]
    return results


def run_entry(entry: ParserEntry, input_path: str, output_path: str, extra_args: tuple = ()) -> int:
    """Invoke a registry entry, returning its exit code.

    Migrated families with a ``runner`` are called in-process (not yet used
    in Phase 1 -- every entry above is subprocess pass-through so behavior is
    byte-identical to running the script directly).
    """
    if not entry.invocable:
        raise RegistryError(
            f"{entry.county}/{entry.family} is not invocable through the standard "
            f"(input, output) CLI contract.\nUsage: {entry.usage}\n{entry.notes}".strip()
        )
    if entry.runner:
        module_name, _, factory_name = entry.runner.partition(":")
        sys.path.insert(0, str(PARSERS_DIR))
        import importlib
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        rows, report = factory().run(input_path, output_path)
        return 0
    if entry.script:
        script_path = REPO_ROOT / entry.script
        cmd = [sys.executable, str(script_path), input_path, output_path, *entry.extra_args, *extra_args]
        result = subprocess.run(cmd)
        return result.returncode
    raise RegistryError(f"Registry entry for {entry.county} has neither script nor runner")


def run_family_engine(engine: FamilyEngine, input_path: str, output_path: str,
                       county: Optional[str] = None, extra_args: tuple = ()) -> int:
    script_path = REPO_ROOT / engine.script
    cmd = [sys.executable, str(script_path), input_path]
    if output_path:
        cmd.append(output_path)
    if engine.county_arg_style == "positional" and county:
        cmd.append(county)
    elif engine.county_arg_style == "flag" and county:
        cmd.extend(["--county", county])
    cmd.extend(extra_args)
    result = subprocess.run(cmd)
    return result.returncode
