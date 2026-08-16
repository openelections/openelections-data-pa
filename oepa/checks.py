"""Post-parse verification helpers used by `oepa parse --strict`.

Two tiers, matched to what's actually available:

- ``ballots_cast_sanity``: works on ANY parser's output CSV, no engine
  changes needed. Flags a precinct/office whose summed candidate votes
  exceed Ballots Cast x max(vote_for, 1) -- a cheap, universal smoke test
  that catches gross overcounting bugs (e.g. a row duplicated, or a
  multi-precinct total misattributed to one precinct).
- ``sovc_geo`` counties (Wayne, Lycoming) additionally support reconciling
  against each contest's own printed "Total" line via
  ``sovc_geo_np.check_printed_totals`` -- a stronger check, but only
  available where the engine captures that line (see that module's
  docstring for why the other families don't yet).
"""

from __future__ import annotations

import csv


def ballots_cast_sanity(csv_path, tolerance=0):
    """Read a written output CSV and flag precinct/office combinations
    whose summed candidate votes exceed Ballots Cast x vote_for.
    Returns a list of mismatch dicts; empty means nothing flagged.
    """
    with open(csv_path, newline='') as f:
        rows = list(csv.DictReader(f))

    if not rows or 'precinct' not in rows[0] or 'office' not in rows[0]:
        return []  # not a precinct-level file (e.g. county-level LLM output)

    ballots_cast = {}
    for row in rows:
        if row.get('office', '') == '' and row.get('candidate') == 'Ballots Cast':
            try:
                ballots_cast[row['precinct']] = int((row.get('votes') or '0').replace(',', ''))
            except ValueError:
                pass

    sums = {}
    for row in rows:
        office = row.get('office', '')
        if office == '':
            continue  # Registered Voters / Ballots Cast metadata rows
        precinct = row.get('precinct', '')
        key = (precinct, office)
        try:
            votes = int((row.get('votes') or '0').replace(',', ''))
        except ValueError:
            continue
        vote_for = row.get('vote_for') or '1'
        try:
            vote_for = max(int(vote_for), 1)
        except ValueError:
            vote_for = 1
        acc = sums.setdefault(key, {'votes': 0, 'vote_for': vote_for})
        acc['votes'] += votes
        acc['vote_for'] = max(acc['vote_for'], vote_for)

    mismatches = []
    for (precinct, office), acc in sums.items():
        cap = ballots_cast.get(precinct)
        if cap is None:
            continue
        limit = cap * acc['vote_for'] + tolerance
        if acc['votes'] > limit:
            mismatches.append({
                'precinct': precinct, 'office': office,
                'summed_votes': acc['votes'], 'ballots_cast': cap, 'vote_for': acc['vote_for'],
            })
    return mismatches


def run_checks(csv_path):
    """Run all available checks on a freshly-written output CSV.
    Returns (mismatches, summary_line)."""
    mismatches = ballots_cast_sanity(csv_path)
    summary = f"verification: ballots-cast sanity check, {len(mismatches)} precinct/office pairs exceed capacity"
    return mismatches, summary
