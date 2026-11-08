"""
Dauphin County 2026 General Primary (2026-05-19) precinct results parser.

Source (official, added 2026-11-07, replaces an earlier Jul 24 parse whose
source file is no longer available; the old file was of unknown vintage):
    ~/code/openelections-sources-pa/2026/primary/"Dauphin County 2026 Primary
    Precinct Detail.csv"
Vendor CSV, one row per candidate per precinct:
    county,precinct,office,party,candidate,machine_votes,mail_in_votes,
    provisional_votes,total_votes

Mapping:
- Office normalization: GOVERNOR -> Governor; LIEUTENANT GOVERNOR ->
  Lieutenant Governor; REPRESENTATIVE IN CONGRESS -> U.S. House (district 10,
  all Dauphin CD precincts are in CD-10, matching the DOS/anchor totals);
  REPRESENTATIVE IN THE GENERAL ASSEMBLY - HD### -> State House with district
  extracted; SENATOR IN THE GENERAL ASSEMBLY - SD## -> State Senate with
  district extracted.
- Local party-committee contests ("DEMOCRATIC/REPUBLICAN COMMITTEEMAN(-WOMAN)
  - <municipality>") are kept as printed (e.g. "Democratic Committeeman -
  Berrysburg"), matching local-committee rows in other 2026 county files
  (see 20260519__pa__primary__beaver__precinct.csv).
- Party already DEM/REP. WRITE-IN1 rows keep the source name (consistent with
  the prior Dauphin file and other 2026 files in this family).
- Breakdowns: machine_votes -> election_day; mail_in_votes -> mail;
  provisional_votes -> provisional. total_votes -> votes. No RV/BC rows in
  the source (none are invented).
- Precinct names: uppercased to preserve the naming used by the previous
  Dauphin file and the countywide file (all-caps style, 1:1 with source).

Verification: per-office/party/candidate countywide sums equal both the
previous precinct file and the state anchor 2026/20260519__pa__primary__county.csv
for all 9 shared contests (Governor, Lieutenant Governor, U.S. House 10,
State House 103/104/105/106/125, State Senate 34), election_day/mail/provisional
breakdown sums included. The new source additionally provides the vote
breakdowns and the local committee contests the old file lacked.

Not covered by the source (the old precinct file lacked them too, and the
DOS export carries no State Committee rows): the 17 countywide
"Member of Dem/Rep State Committee" candidates present in the anchor.
The committee rows here are LOCAL party-committee contests, not State Committee.

Usage:
    python pa_dauphin_primary_2026_results_parser.py [src out_precinct_csv]
    --county [out_county_csv]  (writes countywide sums; with no --county path
    it goes to the scratch default)
"""
import csv
import re
import sys

SRC_DEFAULT = ('/Users/dwillis/code/openelections-sources-pa/2026/primary/'
               'Dauphin County 2026 Primary Precinct Detail.csv')
OUT_DEFAULT = '/Users/dwillis/code/openelections-data-pa/work2026p/dauphin/dauphin_new.csv'
COUNTY_DEFAULT = '/Users/dwillis/code/openelections-data-pa/work2026p/dauphin/dauphin_new_county.csv'

OFFICE_MAP = {
    'GOVERNOR': 'Governor',
    'LIEUTENANT GOVERNOR': 'Lieutenant Governor',
    'REPRESENTATIVE IN CONGRESS': 'U.S. House',
}

# The source prints "REPRESENTATIVE IN CONGRESS" with no CD number; every
# Dauphin precinct is in CD-10 (matches prior file and state anchor).
US_HOUSE_DISTRICT = '10'

HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
          'votes', 'election_day', 'mail', 'provisional']

COUNTY_HEADER = ['county', 'office', 'district', 'party', 'candidate', 'votes',
                 'election_day', 'mail', 'provisional']

# countywide file contests: state/legislative races only (local committee
# contests are per-municipality and excluded from the countywide file,
# matching the prior file and other counties).
MAIN_OFFICES = ['Governor', 'Lieutenant Governor', 'U.S. House', 'State Senate',
                'State House']
COUNTY_ORDER = {'Governor': 0, 'Lieutenant Governor': 1, 'U.S. House': 2,
                'State Senate': 3, 'State House': 4}


def normalize_office(raw):
    off = raw.strip().upper()
    if off in OFFICE_MAP:
        dist = US_HOUSE_DISTRICT if off == 'REPRESENTATIVE IN CONGRESS' else ''
        return OFFICE_MAP[off], dist
    m = re.search(r'\bHD(\d+)', raw)
    if m:
        return 'State House', m.group(1)
    m = re.search(r'\bSD(\d+)', raw)
    if m:
        return 'State Senate', m.group(1)
    # local committee contests, e.g. "DEMOCRATIC COMMITTEEMAN - BERRYSBURG"
    text = raw.strip()
    text = (text.replace('DEMOCRATIC COMMITTEEMAN', 'Democratic Committeeman')
                .replace('DEMOCRATIC COMMITTEEWOMAN', 'Democratic Committeewoman')
                .replace('REPUBLICAN COMMITTEEMAN', 'Republican Committeeman')
                .replace('REPUBLICAN COMMITTEEWOMAN', 'Republican Committeewoman'))
    return text, ''


def main(src=SRC_DEFAULT, out=OUT_DEFAULT):
    with open(src, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    out_rows = []
    for r in rows:
        office, dist = normalize_office(r['office'])
        party = r['party'].strip().upper()
        precinct = r['precinct'].strip().upper()
        out_rows.append({
            'county': 'Dauphin',
            'precinct': precinct,
            'office': office,
            'district': dist,
            'party': party,
            'candidate': r['candidate'].strip(),
            'votes': int(r['total_votes']),
            'election_day': int(r['machine_votes']),
            'mail': int(r['mail_in_votes']),
            'provisional': int(r['provisional_votes']),
        })
    out_rows.sort(key=lambda x: (x['precinct'], x['office'], x['district'],
                                 x['party'], x['candidate']))
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(out_rows)
    print(f'wrote {len(out_rows)} rows to {out}')
    return out_rows


def write_county(out_rows, out):
    sums = {}
    for r in out_rows:
        if r['office'] not in COUNTY_ORDER:
            continue
        k = (r['office'], r['district'], r['party'], r['candidate'].upper())
        if k not in sums:
            sums[k] = [0, 0, 0, 0]
        sums[k][0] += r['votes']
        sums[k][1] += r['election_day']
        sums[k][2] += r['mail']
        sums[k][3] += r['provisional']
    keys = sorted(sums, key=lambda k: (COUNTY_ORDER[k[0]], k[1], k[2],
                                       ({'DEM': 0, 'REP': 1}.get(k[2], 2)), k[3]))
    with open(out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(COUNTY_HEADER)
        for k in keys:
            w.writerow(['Dauphin', k[0], k[1], k[2], k[3], *sums[k]])
    print(f'wrote {len(keys)} county rows to {out}')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--county']
    county_out = None
    if '--county' in sys.argv:
        i = sys.argv.index('--county')
        county_out = sys.argv[i + 1] if i + 1 < len(sys.argv) and sys.argv[i + 1] != '--county' else COUNTY_DEFAULT
    rows = main(*args[:2])
    if county_out:
        write_county(rows, county_out)
