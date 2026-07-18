# oepa: unified parser CLI

`oepa` is a thin CLI + registry layered on top of this repo's ~60 parser
scripts. It doesn't replace any existing script's argv contract -- every
`python parsers/pa_X_..._parser.py input.pdf output.csv` invocation still
works exactly as before. What it adds:

- **`list`** -- see every registered county/family and how to invoke it.
- **`detect`** -- fingerprint a new source PDF and get pointed at the right
  existing parser, or a starter config if none exists yet.
- **`parse`** -- route to a registered parser by county name, or by family
  for the generic multi-county engines.
- **`verify`** -- compare a county's precinct file totals to its own
  county-level summary file (wraps `precinct_results.py`'s existing engine).
- **`scaffold`** -- generate a starter county config for one of the
  config-driven engine families.

Run everything with `uv run oepa <command>` (or `uv run python -m oepa <command>`).

## Commands

```bash
# What's registered?
uv run oepa list
uv run oepa list --family sovc_geo
uv run oepa list --county wayne

# What format is this new PDF?
uv run oepa detect "Forest County PA Nov 2025.pdf"

# Parse a county with a known config
uv run oepa parse input.pdf output.csv --county wayne
uv run oepa parse input.pdf output.csv --county wayne --strict   # fail if verification flags anything

# Parse via a generic multi-county engine
uv run oepa parse input.pdf output.csv --family llm_image_precinct --county Forest

# Compare a county's precinct totals to its county-level summary
uv run oepa verify 20251104__pa__general wayne --directory 2025/counties
uv run oepa verify 20251104__pa__general --all --directory 2025/counties   # every county at once

# Scaffold a new county config once you have a source PDF
uv run oepa scaffold input.pdf --county Forest
uv run oepa scaffold input.pdf --county Forest --family electionware_np   # skip auto-detection
```

## Format families

| family | engine | counties (2025 general) |
|---|---|---|
| `electionware_np` | `parsers/electionware_precinct_np.py` (config-driven, most mature) | berks, blair, cameron, centre, chester, clearfield, elk, franklin, huntingdon, juniata, lawrence, lebanon, mercer, mifflin, montour, northampton, northumberland, potter, snyder, tioga, washington |
| `sovc_geo` | `parsers/sovc_geo_np.py` | wayne, lycoming (config-driven); fulton (standalone -- see below) |
| `electionware_regex` | `parsers/electionware_regex_np.py` | indiana, lackawanna |
| `sovc_crosstab` | `parsers/sovc_crosstab_pp.py` | bedford, jefferson |
| `pdfplumber_custom` / `regex_custom` / `pandas_custom` / `text_subprocess` | standalone, one script per county | bradford, bucks, philadelphia, carbon, wyoming, perry, crawford, lancaster, warren |
| `scraper` | standalone, fetches live results over HTTP | dauphin, philadelphia (board workers) |
| `legacy` / `el30` / `clarity` | standalone, hardcoded I/O paths, not `oepa parse`-invocable | greene, lehigh, monroe, er_parser, el30/el30a/el30b, clarity_parser |

Generic, multi-county "family engines" (no dedicated county script; pass
`--county`): `electionware_text_county`, `electionware_text_precinct`,
`electionware_county`, `electionware_precinct_legacy`, `csv_converter`,
`llm_text`, `llm_image_county`, `llm_image_precinct`. See `oepa list` for
the exact invocation of each.

**Fulton is deliberately not merged into `sovc_geo_np.py`.** It shares the
"Statement of Votes Cast by Geography" report shape with Wayne/Lycoming but
uses keyword-based office detection and party-coded candidate lines with
continuation-line lookahead -- different enough that forcing it into the
shared engine (without a source PDF in the repo to golden-test against)
would risk silently changing its output. See `sovc_geo_np.py`'s module
docstring.

## Adding a new county

1. Get a source PDF and run `uv run oepa detect the.pdf`. It reports a
   best-guess family + confidence and, if no existing config matches,
   prints a ready-to-run `oepa scaffold` command.
2. `uv run oepa scaffold the.pdf --county Forest` writes
   `parsers/pa_forest_general_2025_results_parser.py` from a template with
   `TODO`s for the county-specific config. It refuses to overwrite an
   existing file.
3. Resolve every TODO by comparing parsed output to the source PDF page by
   page. Compare against `ELECTIONWARE_PARSER_DEVELOPMENT.md` for known
   Electionware format variants (retention question wording, school
   director formats, municipal office naming, etc.) if you're working in
   the `electionware_np` family.
4. Once it produces correct CSV output, add a `ParserEntry` to
   `oepa/registry.py`'s `PARSERS` list (the scaffold command prints one
   pre-filled) so `oepa list`/`parse`/`detect` know about it.
5. Run `uv run oepa verify <election_prefix> <county> -d <directory>` once
   both the precinct and county-level files exist, to catch aggregation
   mismatches before committing.

## Verification

Two independent checks, both best-effort:

- **`oepa verify`**: precinct-file totals aggregated up must match the
  county-level summary file. This is the strongest check available and
  works for any county with both files present.
- **`oepa parse --strict`**: a universal ballots-cast sanity check runs on
  every freshly-written CSV (no engine changes needed) -- flags any
  precinct/office whose summed votes exceed `ballots_cast x vote_for`. It's
  advisory by default; `--strict` turns a flag into a non-zero exit. Note:
  counties whose CSV has no `vote_for` column can show benign flags on
  legitimate multi-seat races, since the check can't then distinguish "N
  candidates each near the cap" from a real overcount.
  Wayne and Lycoming additionally reconcile parsed totals against each
  contest's own printed "Total" line from the source report (see
  `sovc_geo_np.check_printed_totals`) -- the other migrated families don't
  capture that line yet (it's currently discarded as a skip-prefix; adding
  capture there is future work, noted in each engine's module docstring).

Running `oepa verify --all` across all of 2025's paired county/precinct
files currently shows 30/67 counties matching -- the rest have either no
paired county-level file yet, or genuine aggregation discrepancies worth
investigating independently of this CLI work.
