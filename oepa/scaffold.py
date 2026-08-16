"""Generate a starter county parser config from a template, using
`oepa detect`'s output to pre-fill what it safely can (county name, harvested
skip-prefix candidates). Everything else is left as a TODO for a human to
verify against the actual PDF -- these templates are a starting point, not
a finished parser.
"""

from __future__ import annotations

import importlib.resources
import string
import sys
from pathlib import Path

from oepa import PARSERS_DIR

_TEMPLATE_FOR_FAMILY = {
    "electionware_np": "electionware_np_config.py.tmpl",
    "sovc_geo": "sovc_geo_config.py.tmpl",
    "electionware_regex": "electionware_regex_config.py.tmpl",
    "sovc_crosstab": "sovc_crosstab_config.py.tmpl",
}


def _load_template(family: str) -> string.Template:
    filename = _TEMPLATE_FOR_FAMILY.get(family)
    if not filename:
        raise ValueError(
            f"No scaffold template for family {family!r}. "
            f"Scaffoldable families: {sorted(_TEMPLATE_FOR_FAMILY)}"
        )
    text = importlib.resources.files("oepa").joinpath("templates", filename).read_text()
    return string.Template(text)


def _format_skip_prefixes(candidates) -> str:
    if not candidates:
        return "    # TODO: no repeated header lines harvested -- fill these in by hand\n    ''"
    lines = [f"    {c!r}," for c in candidates[:10]]
    return "\n".join(lines)


def scaffold_parser(input_path: str, county: str, family: str = None, pages: int = 3) -> int:
    from oepa.detect import detect_format

    county_slug = county.strip().lower().replace(' ', '_')
    county_title = county.strip().title()

    detection = None
    skip_prefixes = []
    if family is None or True:  # always detect, to harvest skip-prefix candidates
        try:
            detection = detect_format(input_path, pages=pages)
            skip_prefixes = detection.variant_hints.get("skip_prefix_candidates", [])
        except Exception as exc:
            print(f"Warning: detection failed ({exc}); proceeding with a blank template.", file=sys.stderr)

    if family is None:
        if detection is None or detection.confidence < detection.min_confidence:
            print("Error: could not confidently detect a format family; pass --family explicitly.",
                  file=sys.stderr)
            print("Scaffoldable families: " + ", ".join(sorted(_TEMPLATE_FOR_FAMILY)), file=sys.stderr)
            return 1
        family = detection.family

    try:
        template = _load_template(family)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output_filename = f"pa_{county_slug}_general_2025_results_parser.py"
    output_path = PARSERS_DIR / output_filename

    if output_path.exists():
        print(f"Error: {output_path} already exists; refusing to overwrite.", file=sys.stderr)
        print("Delete it first or choose a different --county spelling if this is intentional.",
              file=sys.stderr)
        return 1

    rendered = template.substitute(
        county_title=county_title,
        county_header_suffix=county_title.upper() + " COUNTY",
        election_title="2025 General",
        input_basename=Path(input_path).name,
        output_filename=output_filename,
        skip_prefixes=_format_skip_prefixes(skip_prefixes),
    )

    output_path.write_text(rendered)
    print(f"Wrote {output_path}")
    print()
    print("Next steps:")
    print(f"  1. Open {output_path} and resolve every TODO by comparing to the source PDF.")
    print(f"  2. Test-run: uv run python {output_path} {input_path!r} /tmp/{county_slug}_test.csv")
    print(f"  3. Once it looks right, add this to oepa/registry.py's PARSERS list:")
    print(f"       ParserEntry(county={county_slug!r}, election=GENERAL_2025, level=\"precinct\",")
    print(f"                   family={family!r}, script=\"parsers/{output_filename}\"),")
    return 0
