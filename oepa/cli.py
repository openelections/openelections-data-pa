"""oepa: unified CLI for openelections-data-pa parsers.

Commands:
    oepa list     [--family F] [--election E] [--level L]
    oepa parse    input.pdf output.csv --county C [--election E] [--strict]
    oepa parse    input.pdf output.csv --family F [--county C] [--level L]
    oepa detect   input.pdf [--pages N] [--json]
    oepa verify   election_prefix county [--directory D] [--tolerance T] [--web] [--output F]
    oepa verify   election_prefix --all [--directory D] [--tolerance T]
    oepa scaffold input.pdf --county C [--family F]
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import io
import os
import sys
from pathlib import Path

from oepa import PARSERS_DIR, REPO_ROOT
from oepa.registry import (
    PARSERS,
    FAMILY_ENGINES,
    RegistryError,
    find_entries,
    run_entry,
    run_family_engine,
)


def _cmd_list(args: argparse.Namespace) -> int:
    entries = find_entries(county=args.county, election=args.election,
                            family=args.family, level=args.level)
    if not entries and not args.family:
        print("No matching per-county entries.")
    else:
        print(f"{'county':<15} {'election':<24} {'level':<10} {'family':<20} {'invocable':<10} script/notes")
        print("-" * 110)
        for e in sorted(entries, key=lambda x: (x.family, x.county)):
            tail = e.script or e.runner or ""
            if not e.invocable:
                tail = f"{tail}  [{e.usage}]"
            print(f"{e.county:<15} {e.election:<24} {e.level:<10} {e.family:<20} {str(e.invocable):<10} {tail}")

    if not args.county:
        print()
        print("Family engines (generic, multi-county; invoke with --family NAME --county C):")
        for name, eng in sorted(FAMILY_ENGINES.items()):
            if args.family and name != args.family:
                continue
            print(f"  {name:<26} level={eng.level:<10} {eng.script}")
    return 0


def _run_post_parse_checks(output_path, strict) -> int:
    """Run the universal ballots-cast sanity check against a freshly-written
    output CSV. Advisory by default (prints, doesn't fail the command) --
    counties whose CSV has no vote_for column (several one-off pdfplumber
    parsers) can show benign flags on legitimate multi-seat races, since the
    check then can't tell "N candidates x ballots_cast" from a real
    overcount. --strict promotes any flag to a failing exit code regardless.
    """
    from oepa.checks import run_checks

    if not os.path.exists(output_path):
        return 0
    try:
        mismatches, summary = run_checks(output_path)
    except Exception as exc:
        print(f"verification: skipped ({exc})", file=sys.stderr)
        return 0

    print(summary)
    if mismatches:
        for m in mismatches[:10]:
            print(f"  FLAG {m['precinct']} / {m['office']}: summed={m['summed_votes']} "
                  f"ballots_cast={m['ballots_cast']} vote_for={m['vote_for']}", file=sys.stderr)
        if len(mismatches) > 10:
            print(f"  ... and {len(mismatches) - 10} more", file=sys.stderr)
        if strict:
            return 1
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    if args.family and args.family in FAMILY_ENGINES:
        engine = FAMILY_ENGINES[args.family]
        try:
            rc = run_family_engine(engine, args.input, args.output, county=args.county,
                                    extra_args=tuple(args.extra))
        except Exception as exc:  # surface subprocess/setup errors clearly
            print(f"Error running family engine {args.family}: {exc}", file=sys.stderr)
            return 3
        if rc != 0:
            return rc
        return _run_post_parse_checks(args.output, args.strict)

    if not args.county:
        print("Error: --county is required unless --family names a family engine.", file=sys.stderr)
        print("Run `oepa list` to see registered counties/families, or `oepa detect <pdf>` first.",
              file=sys.stderr)
        return 2

    entries = find_entries(county=args.county, family=args.family, level=args.level)
    if len(entries) > 1 and args.election:
        narrowed = [e for e in entries if e.election == args.election]
        if narrowed:
            entries = narrowed
    if not entries:
        print(f"No registered parser for county={args.county!r} "
              f"election={args.election!r} family={args.family!r} level={args.level!r}", file=sys.stderr)
        return 2
    if len(entries) > 1:
        print(f"Multiple parsers match county={args.county!r}; disambiguate with --election/--level/--family:",
              file=sys.stderr)
        for e in entries:
            print(f"  election={e.election} level={e.level} family={e.family}", file=sys.stderr)
        return 2

    entry = entries[0]
    extra_args = tuple(args.extra)
    if args.strict and entry.supports_strict:
        extra_args = extra_args + ('--strict',)
    try:
        rc = run_entry(entry, args.input, args.output, extra_args=extra_args)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if rc != 0:
        return rc
    return _run_post_parse_checks(args.output, args.strict)


def _cmd_verify(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO_ROOT))
    from precinct_results import compare_county_to_precinct_totals

    output_format = "web" if args.web else "cli"

    if args.all:
        directory = args.directory
        pattern = os.path.join(directory, f"{args.election_prefix}__*__county.csv")
        counties = []
        for path in sorted(glob.glob(pattern)):
            base = os.path.basename(path)
            middle = base[len(args.election_prefix) + 2 : -len("__county.csv")]
            counties.append(middle)
        if not counties:
            print(f"No county files found matching {pattern}", file=sys.stderr)
            return 2

        failures = []
        for county in counties:
            precinct_path = os.path.join(directory, f"{args.election_prefix}__{county}__precinct.csv")
            if not os.path.exists(precinct_path):
                print(f"{county:<20} SKIP (no precinct file)")
                continue
            try:
                # compare_county_to_precinct_totals() always prints a full report
                # when output_format='cli', regardless of verbose; suppress it here
                # so --all can show a compact pass/fail line per county instead.
                with contextlib.redirect_stdout(io.StringIO()):
                    results = compare_county_to_precinct_totals(
                        election_prefix=args.election_prefix, county_name=county,
                        directory=directory, tolerance=args.tolerance, verbose=False,
                        output_format=output_format,
                    )
                diffs = results["summary"]["total_differences"]
                status = "OK" if diffs == 0 else f"FAIL ({diffs} differences)"
                print(f"{county:<20} {status}")
                if diffs != 0:
                    failures.append(county)
            except Exception as exc:
                print(f"{county:<20} ERROR ({exc})")
                failures.append(county)
        print()
        print(f"{len(counties) - len(failures)}/{len(counties)} counties match.")
        return 1 if failures else 0

    if not args.county:
        print("Error: county is required unless --all is given.", file=sys.stderr)
        return 2

    try:
        results = compare_county_to_precinct_totals(
            election_prefix=args.election_prefix, county_name=args.county,
            directory=args.directory, tolerance=args.tolerance, verbose=not args.quiet,
            output_format=output_format, output_file=args.output,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3

    if results["summary"]["total_differences"] == 0:
        if not args.quiet:
            print("\n✓ SUCCESS: County and precinct totals match")
        return 0
    if not args.quiet:
        print(f"\n✗ FAILED: {results['summary']['total_differences']} differences found")
    return 1


def _cmd_detect(args: argparse.Namespace) -> int:
    from oepa.detect import detect_format

    result = detect_format(args.input, pages=args.pages)
    if args.json:
        import json
        print(json.dumps(result.to_dict(), indent=2))
    else:
        result.print_report()
    return 0 if result.confidence >= result.min_confidence else 1


def _cmd_scaffold(args: argparse.Namespace) -> int:
    from oepa.scaffold import scaffold_parser

    return scaffold_parser(args.input, county=args.county, family=args.family, pages=args.pages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="oepa", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List registered parsers and family engines")
    p_list.add_argument("--county")
    p_list.add_argument("--election")
    p_list.add_argument("--family")
    p_list.add_argument("--level")
    p_list.set_defaults(func=_cmd_list)

    p_parse = sub.add_parser("parse", help="Parse a PDF/input file using a registered or named-family parser")
    p_parse.add_argument("input")
    p_parse.add_argument("output")
    p_parse.add_argument("--county")
    p_parse.add_argument("--election", default=None,
                          help="Disambiguate when a county has multiple registered elections "
                               "(default: use the entry if only one exists)")
    p_parse.add_argument("--family")
    p_parse.add_argument("--level")
    p_parse.add_argument("--strict", action="store_true",
                          help="Exit non-zero if the post-parse ballots-cast sanity check flags anything; "
                               "for wayne/lycoming also enables the engine's own printed-total reconciliation")
    p_parse.add_argument("extra", nargs="*", help="Extra args forwarded to the underlying script")
    p_parse.set_defaults(func=_cmd_parse)

    p_verify = sub.add_parser("verify", help="Compare county-level totals to aggregated precinct totals")
    p_verify.add_argument("election_prefix")
    p_verify.add_argument("county", nargs="?")
    p_verify.add_argument("-d", "--directory", default=".")
    p_verify.add_argument("-t", "--tolerance", type=float, default=0.0)
    p_verify.add_argument("--web", action="store_true")
    p_verify.add_argument("-o", "--output")
    p_verify.add_argument("--quiet", action="store_true")
    p_verify.add_argument("--all", action="store_true", help="Check every county with paired county/precinct files")
    p_verify.set_defaults(func=_cmd_verify)

    p_detect = sub.add_parser("detect", help="Fingerprint a PDF's source format")
    p_detect.add_argument("input")
    p_detect.add_argument("--pages", type=int, default=3)
    p_detect.add_argument("--json", action="store_true")
    p_detect.set_defaults(func=_cmd_detect)

    p_scaffold = sub.add_parser("scaffold", help="Generate a starter county parser config")
    p_scaffold.add_argument("input")
    p_scaffold.add_argument("--county", required=True)
    p_scaffold.add_argument("--family")
    p_scaffold.add_argument("--pages", type=int, default=3)
    p_scaffold.set_defaults(func=_cmd_scaffold)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
