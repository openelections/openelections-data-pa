#!/usr/bin/env python3
"""
Command-line utility to compare county-level summary files to aggregated precinct totals.

This tool verifies that county-level election summary files match the aggregated
totals from their corresponding precinct-level files.

Usage:
    python compare_county_precinct.py 20251104__pa__general adams
    python compare_county_precinct.py 20251104__pa__general adams --directory data/2025
    python compare_county_precinct.py 20251104__pa__general adams --web --output report.html
"""

import argparse
import sys
from precinct_results import compare_county_to_precinct_totals


def main():
    parser = argparse.ArgumentParser(
        description='Compare county-level summary files to aggregated precinct totals',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic comparison
  %(prog)s 20251104__pa__general adams

  # Specify custom directory
  %(prog)s 20251104__pa__general adams --directory data/2025

  # Generate HTML report
  %(prog)s 20251104__pa__general adams --web --output report.html

  # With tolerance for small rounding differences
  %(prog)s 20251104__pa__general adams --tolerance 5

  # Check multiple counties
  for county in adams allegheny; do
      %(prog)s 20251104__pa__general $county
  done
        """
    )

    parser.add_argument(
        'election_prefix',
        help='Election prefix (e.g., "20251104__pa__general")'
    )

    parser.add_argument(
        'county',
        help='County name (e.g., "adams")'
    )

    parser.add_argument(
        '-d', '--directory',
        default='.',
        help='Directory containing CSV files (default: current directory)'
    )

    parser.add_argument(
        '-t', '--tolerance',
        type=float,
        default=0.0,
        help='Numeric comparison tolerance (default: 0.0)'
    )

    parser.add_argument(
        '--web',
        action='store_true',
        help='Generate HTML web report instead of CLI output'
    )

    parser.add_argument(
        '-o', '--output',
        help='Output file path (for web report or text report)'
    )

    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Suppress verbose output, only show results'
    )

    args = parser.parse_args()

    # Determine output format
    if args.web:
        output_format = 'web'
    else:
        output_format = 'cli'

    try:
        # Run comparison
        results = compare_county_to_precinct_totals(
            election_prefix=args.election_prefix,
            county_name=args.county,
            directory=args.directory,
            tolerance=args.tolerance,
            verbose=not args.quiet,
            output_format=output_format,
            output_file=args.output
        )

        # Exit with appropriate code
        if results['summary']['total_differences'] == 0:
            if not args.quiet:
                print("\n✓ SUCCESS: County and precinct totals match")
            sys.exit(0)
        else:
            if not args.quiet:
                print(f"\n✗ FAILED: {results['summary']['total_differences']} differences found")
            sys.exit(1)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)


if __name__ == '__main__':
    main()
