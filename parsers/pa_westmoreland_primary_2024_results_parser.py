#!/usr/bin/env python3
"""Parse Westmoreland County PA 2024 General Primary (April 23, 2024)
precinct results.

Sources, in order:

1. "Westmoreland PA Official-Results-including-Write-ins-2024-Primary.pdf"
   (county Board of Elections "Summary Results Report", JasperReports,
   dated 05/13/2024).  This report is COUNTYWIDE ONLY: despite all 306 of
   306 precincts reporting, not a single per-precinct row or column exists
   in its 122 pages -- only grand totals per contest (with a full named
   write-in breakdown per contest).  It is therefore not usable as a
   precinct-level source.

2. PA Department of State certified bulk precinct-returns file for the
   2024 Primary ("prcres.txt"; ElectionReturns_2024_Primary_PrecinctReturns.txt
   from pa.gov .../voting-and-election-statistics/election-data, data
   extracted July 23, 2024, county code 65 = Westmoreland).  This is the
   precinct-level source this parser emits (same DOS-fallback pattern used
   for the 2022 primaries where the county source was countywide-only).

Not available in any source: per-precinct write-ins (the DOS bulk file for
the 2024 primary carries no write-in rows, and the county PDF's write-in
detail is countywide-only), and per-precinct Registered Voters / Ballots
Cast / vote-method breakdowns (DOS does not carry them; the county PDF has
countywide turnout only).  Output is therefore a 7-column file:

    county,precinct,office,district,party,candidate,votes

Six DEM contests are WRITE-IN ONLY (no named candidate on any municipal
ballot in the district): State Senate 39, State Senate 41, State House 55,
56, 57 and 60.  The DOS file has no rows for them, so they are omitted;
their countywide write-in totals are printed by verify() for the record.

Contest / candidate coverage notes:
  * Every contest in the DOS file has an (office, district, party)
    counterpart in the county PDF; candidate names follow the county
    PDF's printed forms (e.g. "Joseph R. Biden, Jr.", "Ken Bach",
    "Michelle Milan McFall", "Melanie Stringhill Patterson"), verified
    below against the DOS per-precinct sums.
  * DOS reports 308 precinct descriptions for 306 physical precincts:
    Hempfield Middletown and Unity Pleasant Unity are split by
    congressional district (USC 12/14 and Cong. 13/14).  Those are kept as
    separate precinct names carrying the DOS suffix -- "(USC n)" /
    "(Cong. n)" -- so districted votes stay unmerged.  "Penn Township
    3-3" (in the 2022 general data) does not exist in the 2024 returns.
  * Precinct names follow the county's own export convention already used
    in the repo Westmoreland files (2022 primary/general): "Allegheny
    Township #1 Shearers", "City Of Arnold - 1-1", "Bell Township -
    Perrysville", "W Newton Borough - 1St Ward", ... verified 1:1 below
    against the DOS descriptions.

Usage:
  python pa_westmoreland_primary_2024_results_parser.py <prcres.txt> \
      <output.csv> [official_results.pdf]

  <prcres.txt> may be the statewide DOS file (filtered by county code 65).
  With the optional 3rd argument the parser verifies every contest's
  countywide candidate sum against the county's official printed totals
  (pdftotext-style layout text is extracted with pdfplumber).
"""

import csv
import re
import sys
from collections import Counter, OrderedDict, defaultdict

COUNTY = "Westmoreland"
COUNTY_CODE = "65"
YEAR = "2024"
ETYPE = "P"

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate", "votes"]

# ------------------------------------------------------------------ offices
OFFICE_MAP = {          # DOS office code -> (output office, district from row?)
    "USP": ("President", False),
    "USS": ("U.S. Senate", False),
    "ATT": ("Attorney General", False),
    "AUD": ("Auditor General", False),
    "TRE": ("State Treasurer", False),
    "USC": ("U.S. House", True),      # district = candidate district field
    "STS": ("State Senate", True),
    "STH": ("State House", True),
    "DED": ("Delegate to the Democratic National Convention", True),
    "DER": ("Delegate to the Republican National Convention", True),
    "ADR": ("Alternate Delegate to the Republican National Convention", True),
}

# (output office, district, party) -> {DOS candidate number: printed name}
# Names follow the county PDF's printed candidate rows; the per-contest
# sets were matched (and verified) contest-total by contest-total.
CANDIDATE_OUT = {
    ('Attorney General', '', 'DEM', '2024C0026'): 'Keir Bradford-Grey',
    ('Attorney General', '', 'DEM', '2024C0267'): 'Joe Khan',
    ('Attorney General', '', 'DEM', '2024C0277'): 'Eugene DePasquale',
    ('Attorney General', '', 'DEM', '2024C0378'): 'Jack Stollsteimer',
    ('Attorney General', '', 'DEM', '2024C0721'): 'Jared Solomon',
    ('Attorney General', '', 'REP', '2024C0238'): 'Dave Sunday',
    ('Attorney General', '', 'REP', '2024C0791'): 'Craig Williams',
    ('Auditor General', '', 'DEM', '2024C0008'): 'Malcolm Kenyatta',
    ('Auditor General', '', 'DEM', '2024C0286'): 'Mark Pinsley',
    ('Auditor General', '', 'REP', '2024C0276'): 'Tim DeFoor',
    ('President', '', 'DEM', '2024C0558'): 'Joseph R. Biden, Jr.',
    ('President', '', 'DEM', '2024C0754'): 'Dean Phillips',
    ('President', '', 'REP', '2024C0271'): 'Donald J. Trump',
    ('President', '', 'REP', '2024C0517'): 'Nikki R. Haley',
    ('State Treasurer', '', 'DEM', '2024C0036'): 'Ryan Bizzarro',
    ('State Treasurer', '', 'DEM', '2024C0748'): 'Erin McClelland',
    ('State Treasurer', '', 'REP', '2024C0072'): 'Stacy Garrity',
    ('U.S. Senate', '', 'DEM', '2024C0545'): 'Robert P. Casey, Jr.',
    ('U.S. Senate', '', 'REP', '2024C0018'): 'Dave McCormick',
    ('Alternate Delegate to the Republican National Convention', '12', 'REP', '2024C0057'): 'Jim Means',
    ('Alternate Delegate to the Republican National Convention', '12', 'REP', '2024C0058'): 'Kevin Sheahen',
    ('Alternate Delegate to the Republican National Convention', '12', 'REP', '2024C0079'): 'Kevin Z. Tatulyan',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0702'): 'Michelle Milan McFall',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0703'): 'Melissa Stephanou',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0704'): 'Nick Pisciottano',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0705'): 'Jim Depoe',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0706'): 'Nathaniel Yap',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0707'): 'Morgan Overton',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0708'): 'Dan Frankel',
    ('Delegate to the Democratic National Convention', '12', 'DEM', '2024C0709'): 'Jessica Benham',
    ('Delegate to the Republican National Convention', '12', 'REP', '2024C0059'): 'Sue Means',
    ('Delegate to the Republican National Convention', '12', 'REP', '2024C0060'): 'Rick Saccone',
    ('Delegate to the Republican National Convention', '12', 'REP', '2024C0078'): 'Kevin Z. Tatulyan',
    ('Delegate to the Republican National Convention', '12', 'REP', '2024C0507'): 'Dave Majernik',
    ('Delegate to the Republican National Convention', '12', 'REP', '2024C0508'): 'Steve Schlauch',
    ('U.S. House', '12', 'DEM', '2024C0551'): 'Bhavini Patel',
    ('U.S. House', '12', 'DEM', '2024C0730'): 'Summer Lee',
    ('U.S. House', '12', 'REP', '2024C0429'): 'James Hayes',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0066'): 'James Zerfoss',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0073'): 'Sue Darr',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0259'): 'Nancy Pleskovitch',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0298'): 'Steve Renz',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0609'): 'Thomas J. Uram',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0611'): 'Sonia Stopperich',
    ('Alternate Delegate to the Republican National Convention', '14', 'REP', '2024C0684'): 'Hunter Overdorff',
    ('Delegate to the Democratic National Convention', '14', 'DEM', '2024C0779'): 'JoJo Burgess',
    ('Delegate to the Democratic National Convention', '14', 'DEM', '2024C0780'): 'Sharon Laffey',
    ('Delegate to the Democratic National Convention', '14', 'DEM', '2024C0782'): 'Bibiana Boerio',
    ('Delegate to the Democratic National Convention', '14', 'DEM', '2024C0784'): 'Ted Kopas',
    ('Delegate to the Democratic National Convention', '14', 'DEM', '2024C0785'): 'Lillian Proctor',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0011'): 'Guy Reschenthaler',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0077'): 'Sue Darr',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0081'): 'Leslie Rossi',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0257'): 'Nancy Pleskovitch',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0295'): 'Steve Renz',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0329'): 'Melanie Stringhill Patterson',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0413'): 'Susanna N. DeJeet',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0606'): 'Thomas J. Uram',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0614'): 'Sonia Stopperich',
    ('Delegate to the Republican National Convention', '14', 'REP', '2024C0735'): 'Tom Ward',
    ('U.S. House', '14', 'DEM', '2024C0285'): 'Ken Bach',
    ('U.S. House', '14', 'DEM', '2024C0535'): 'Chris Dziados',
    ('U.S. House', '14', 'REP', '2024C0012'): 'Guy Reschenthaler',
    ('State Senate', '39', 'REP', '2024C0051'): 'Kim Ward',
    ('State Senate', '41', 'REP', '2024C0273'): 'Joe Pittman',
    ('State House', '55', 'REP', '2024C0019'): 'Jill Cooper',
    ('State House', '55', 'REP', '2024C0408'): 'Jamie Lingg',
    ('State House', '56', 'REP', '2024C0124'): 'Brian Rasel',
    ('State House', '57', 'REP', '2024C0144'): 'Eric Nelson',
    ('State House', '58', 'DEM', '2024C0781'): 'Cherri Rogers',
    ('State House', '58', 'REP', '2024C0386'): 'Eric Davanzo',
    ('State House', '59', 'DEM', '2024C0540'): 'Margie Zelenak',
    ('State House', '59', 'REP', '2024C0080'): 'Leslie Rossi',
    ('State House', '60', 'REP', '2024C0179'): 'Abby Major',
}

# DOS municipality/precinct description -> output precinct name (repo
# convention).  Verified below against the DOS descriptions found.
PRECINCT_OUT = {
    'ADAMSBURG': 'Adamsburg Borough',
    'ALLEGHENY X 1': 'Allegheny Township #1 Shearers',
    'ALLEGHENY X 2': 'Allegheny Township #2 Mckees',
    'ALLEGHENY X 3': 'Allegheny Township #3 Sobers',
    'ALLEGHENY X 4': 'Allegheny Township #4 Stewarts',
    'ALLEGHENY X 5': 'Allegheny Township #5 Bellvue',
    'ARNOLD W 1 P 1': 'City Of Arnold - 1-1',
    'ARNOLD W 1 P 2': 'City Of Arnold - 1-2',
    'ARNOLD W 2 P 1': 'City Of Arnold - 2-1',
    'ARNOLD W 2 P 2': 'City Of Arnold - 2-2',
    'ARNOLD W 2 P 3': 'City Of Arnold - 2-3',
    'ARONA': 'Arona Borough',
    'AVONMORE': 'Avonmore Borough',
    'BELL X PERRYSVILLE': 'Bell Township - Perrysville',
    'BELL X ROARING RUN': 'Bell Township - Roaring Run',
    'BELL X SALINA': 'Bell Township - Salina',
    'BOLIVAR': 'Bolivar Borough',
    'COOK': 'Cook Township',
    'DELMONT': 'Delmont Borough',
    'DERRY X ALTERS': 'Derry Township - Alters',
    'DERRY X BRADENVILLE': 'Derry Township - Bradenville',
    'DERRY X COKEVILLE': 'Derry Township - Cokeville',
    'DERRY X COOPERSTOWN': 'Derry Township - Cooperstown',
    'DERRY X KINGSTON': 'Derry Township - Kingston',
    'DERRY X LOYALHANNA': 'Derry Township - Loyalhanna',
    'DERRY X MILLWOOD': 'Derry Township - Millwood',
    'DERRY X NEW DERRY': 'Derry Township - New Derry',
    'DERRY X PEANUT': 'Derry Township - Peanut',
    'DERRY X SAXMAN': 'Derry Township - Saxman',
    'DERRY X SCALP LEVEL': 'Derry Township - Scalp Level',
    'DERRY X SIMPSONS': 'Derry Township - Simpsons',
    'DERRY X TORRANCE': 'Derry Township - Torrance',
    'DERRY W 1': 'Derry Borough - 1St Ward',
    'DERRY W 2': 'Derry Borough - 2Nd Ward',
    'DERRY W 3': 'Derry Borough - 3Rd Ward',
    'DERRY W 4': 'Derry Borough - 4Th Ward',
    'DONEGAL X FOUR MILE RUN': 'Donegal Township - Four Mile Run',
    'DONEGAL X INDIAN CREEK': 'Donegal Township - Indian Creek',
    'DONEGAL': 'Donegal Borough',
    'EAST HUNTINGDON X BESSEMER X 1': 'East Huntingdon Township Bessemer #1',
    'EAST HUNTINGDON X BESSEMER X 2': 'East Huntingdon Township Bessemer #2',
    'EAST HUNTINGDON X RUFFSDALE': 'E Huntingdon Township - Ruffssale',
    'EAST HUNTINGDON X STONERS': 'E Huntingdon Township - Stoners',
    'EAST HUNTINGDON X STROHMS': 'E Huntingdon Township - Strohms',
    'EAST HUNTINGDON X WHITES': 'E Huntingdon Township - Whites',
    'EAST VANDERGRIFT': 'E Vandergrift Borough',
    'EXPORT': 'Export Borough',
    'FAIRFIELD X FAIRFIELD': 'Fairfield Township - Fairfield',
    'FAIRFIELD X NORTH FAIRFIELD': 'Fairfield Township - N Fairfield',
    'FAIRFIELD X SOUTH FAIRFIELD': 'Fairfield Township - S Fairfield',
    'GREENSBURG W 1 P 1': 'City Of Greensburg - 1-1',
    'GREENSBURG W 1 P 2': 'City Of Greensburg - 1-2',
    'GREENSBURG W 2 P 1': 'City Of Greensburg - 2-1',
    'GREENSBURG W 2 P 2': 'City Of Greensburg - 2-2',
    'GREENSBURG W 2 P 3': 'City Of Greensburg - 2-3',
    'GREENSBURG W 2 P 4': 'City Of Greensburg - 2-4',
    'GREENSBURG W 3': 'City Of Greensburg - 3',
    'GREENSBURG W 4 P 1': 'City Of Greensburg - 4-1',
    'GREENSBURG W 5 P 1': 'City Of Greensburg - 5-1',
    'GREENSBURG W 5 P 2': 'City Of Greensburg - 5-2',
    'GREENSBURG W 6': 'City Of Greensburg - 6',
    'GREENSBURG W 7 P 1': 'City Of Greensburg - 7-1',
    'GREENSBURG W 7 P 2': 'City Of Greensburg - 7-2',
    'GREENSBURG W 8 P 1': 'City Of Greensburg - 8-1',
    'GREENSBURG W 8 P 2': 'City Of Greensburg - 8-2',
    'HEMPFIELD X ALWINE': 'Hempfield Township - #2 Alwine',
    'HEMPFIELD X BOVARD': 'Hempfield Township - #8 Bovard',
    'HEMPFIELD X CARBON': 'Hempfield Township - #4 Carbon',
    'HEMPFIELD X EAST ADAMSBURG': 'Hempfield Township - East Adamsburg',
    'HEMPFIELD X EASTVIEW': 'Hempfield Township - Eastview',
    'HEMPFIELD X FORT ALLEN': 'Hempfield Township - Fort Allen',
    'HEMPFIELD X FOXHILL': 'Hempfield Township - #1 Foxhill',
    'HEMPFIELD X GAYVILLE': 'Hempfield Township - Gayville',
    'HEMPFIELD X GRAPEVILLE': 'Hempfield Township - Grapeville',
    'HEMPFIELD X HANNASTOWN': 'Hempfield Township - #7 Hannastown',
    'HEMPFIELD X HAYDENVILLE': 'Hempfield Township - Haydenville',
    'HEMPFIELD X HIGH PARK': 'Hempfield Township - High Park',
    'HEMPFIELD X LINCOLN HEIGHTS': 'Hempfield Township - Lincoln Heights East',
    'HEMPFIELD X LINCOLN HEIGHTS WEST': 'Hempfield Township - Lincoln Heights West',
    'HEMPFIELD X LUXOR': 'Hempfield Township - #6 Luxor',
    'HEMPFIELD X MAPLEWOOD': 'Hempfield Township - #3 Maplewood',
    'HEMPFIELD X MIDDLETOWN (USC 12)': 'Hempfield Township - Middletown (USC 12)',
    'HEMPFIELD X MIDDLETOWN (USC 14)': 'Hempfield Township - Middletown (USC 14)',
    'HEMPFIELD X NEW STANTON': 'Hempfield Township - New Stanton',
    'HEMPFIELD X NORTH CARBON': 'Hempfield Township - North Carbon',
    'HEMPFIELD X SIBEL': 'Hempfield Township - Sibel',
    'HEMPFIELD X TODD': 'Hempfield Township - Todd',
    'HEMPFIELD X UNIVERSITY': 'Hempfield Township - University',
    'HEMPFIELD X VALLEY': 'Hempfield Township - Valley',
    'HEMPFIELD X WEAVERS OLD STAND': "Hempfield Township - Weaver'S Old Stand",
    'HEMPFIELD X WEGLEY': 'Hempfield Township - Wegley',
    'HEMPFIELD X WENDEL HERM': 'Hempfield Township - #5 Wendel-Herminie',
    'HEMPFIELD X WEST HEMPFIELD': 'Hempfield Township - West Hempfield',
    'HEMPFIELD X WEST POINT': 'Hempfield Township - West Point',
    'HUNKER': 'Hunker Borough',
    'HYDE PARK': 'Hyde Park Borough',
    'IRWIN W 1': 'Irwin Borough - 1St Ward',
    'IRWIN W 2': 'Irwin Borough - 2Nd Ward',
    'IRWIN W 3': 'Irwin Borough - 3Rd Ward',
    'IRWIN W 4': 'Irwin Borough - 4Th Ward',
    'IRWIN W 5': 'Irwin Borough - 5Th Ward',
    'IRWIN W 6': 'Irwin Borough - 6Th Ward',
    'IRWIN W 7': 'Irwin Borough - 7Th Ward',
    'JEANNETTE W 1': 'City Of Jeannette - 1',
    'JEANNETTE W 2 P 1': 'City Of Jeannette - 2-1',
    'JEANNETTE W 2 P 2': 'City Of Jeannette - 2-2',
    'JEANNETTE W 3': 'City Of Jeannette - 3',
    'JEANNETTE W 4': 'City Of Jeannette - 4',
    'JEANNETTE W 5 P 1': 'City Of Jeannette - 5-1',
    'JEANNETTE W 5 P 2': 'City Of Jeannette - 5-2',
    'JEANNETTE W 6': 'City Of Jeannette - 6',
    'LATROBE W 1': 'City Of Latrobe - 1St Ward',
    'LATROBE W 2': 'City Of Latrobe - 2Nd Ward',
    'LATROBE W 3': 'City Of Latrobe - 3Rd Ward',
    'LATROBE W 4': 'City Of Latrobe - 4Th Ward',
    'LATROBE W 5': 'City Of Latrobe - 5Th Ward',
    'LATROBE W 6': 'City Of Latrobe - 6Th Ward',
    'LAUREL MOUNTAIN': 'Laurel Mountain Borough',
    'LIGONIER X IDLE PARK': 'Ligonier Township - Idle Park',
    'LIGONIER X LAUGHLINSTOWN': 'Ligonier Township - Laughlintown',
    'LIGONIER X NORTH LIGONIER': 'Ligonier Township - North Ligonier',
    'LIGONIER X SOUTH LIGONIER': 'Ligonier Township - South Ligonier',
    'LIGONIER X WATERFORD': 'Ligonier Township - Waterford',
    'LIGONIER X WILPEN': 'Ligonier Township - Wilpen',
    'LIGONIER': 'Ligonier Borough',
    'LOWER BURRELL W 1 P 1': 'City Of Lower Burrell - 1-1',
    'LOWER BURRELL W 1 P 2': 'City Of Lower Burrell - 1-2',
    'LOWER BURRELL W 2 P 1': 'City Of Lower Burrell - 2-1',
    'LOWER BURRELL W 2 P 2': 'City Of Lower Burrell - 2-2',
    'LOWER BURRELL W 2 P 3': 'City Of Lower Burrell - 2-3',
    'LOWER BURRELL W 3 P 1': 'City Of Lower Burrell - 3-1',
    'LOWER BURRELL W 3 P 2': 'City Of Lower Burrell - 3-2',
    'LOWER BURRELL W 4 P 1': 'City Of Lower Burrell - 4-1',
    'LOWER BURRELL W 4 P 2': 'City Of Lower Burrell - 4-2',
    'LOYALHANNA X 1': 'Loyalhanna Township - #1',
    'LOYALHANNA X 2': 'Loyalhanna Township - #2',
    'MADISON': 'Madison Borough',
    'MANOR': 'Manor Borough',
    'MONESSEN W 1 P 1': 'City Of Monessen - 1-1',
    'MONESSEN W 1 P 2': 'City Of Monessen - 1-2',
    'MONESSEN W 1 P 3': 'City Of Monessen - 1-3',
    'MONESSEN W 1 P 4': 'City Of Monessen - 1-4',
    'MONESSEN W 1 P 5': 'City Of Monessen - 1-5',
    'MONESSEN W 2 P 1': 'City Of Monessen - 2-1',
    'MONESSEN W 2 P 2': 'City Of Monessen - 2-2',
    'MONESSEN W 2 P 3': 'City Of Monessen - 2-3',
    'MONESSEN W 3 P 2': 'City Of Monessen - 3-2',
    'MONESSEN W 3 P 3': 'City Of Monessen - 3-3',
    'MONESSEN W 3 P 4': 'City Of Monessen - 3-4',
    'MONESSEN W 4 P 2': 'City Of Monessen - 4-2',
    'MONESSEN W 4 P 3': 'City Of Monessen - 4-3',
    'MT. PLEASANT X BRIDGEPORT': 'Mt Pleasant Township - Bridgeport',
    'MT. PLEASANT X DUNCAN': 'Mt Pleasant Township - Duncan',
    'MT. PLEASANT X HECCLA': 'Mt Pleasant Township - Heccla',
    'MT. PLEASANT X LAUREL RUN': 'Mt Pleasant Township - Laurel Run',
    'MT. PLEASANT X MAMMOTH': 'Mt Pleasant Township - Mammoth',
    'MT. PLEASANT X PLEASANT VALLEY': 'Mt Pleasant Township - Pleasant Valley',
    'MT. PLEASANT X RIDGEVIEW': 'Mt Pleasant Township - Ridgeview',
    'MT. PLEASANT X SPRING GARDEN': 'Mt Pleasant Township - Spring Garden',
    'MT. PLEASANT X UNITED': 'Mt Pleasant Township - United',
    'MT. PLEASANT X WESTMORELAND': 'Mt Pleasant Township - Westmoreland Homesteads',
    'MT. PLEASANT W 1': 'Mt. Pleasant Borough 1St Ward',
    'MT. PLEASANT W 2': 'Mt. Pleasant Borough 2Nd Ward',
    'MT. PLEASANT W 3': 'Mt. Pleasant Borough 3Rd Ward',
    'MURRYSVILLE X EAST MANORDALE': 'Municipality Of Murrysville - East Manordale',
    'MURRYSVILLE X EAST MURRYSVILLE': 'Municipality Of Murrysville - East Murrysville',
    'MURRYSVILLE X MANORDALE': 'Municipality Of Murrysville - Manordale',
    'MURRYSVILLE X NEWLONSBURG D 1': 'Municipality Of Murrysville - Newlonsburg #1',
    'MURRYSVILLE X NEWLONSBURG D 2': 'Municipality Of Murrysville - Newlonsburg #2',
    'MURRYSVILLE X SARDIS D 1': 'Municipality Of Murrysville - Sardis #1',
    'MURRYSVILLE X SARDIS D 2': 'Municipality Of Murrysville - Sardis #2',
    'MURRYSVILLE X SOUTH MURRYSVILLE': 'Municipality Of Murrysville - South Murrysville',
    'MURRYSVILLE X WEST MURRYSVILLE': 'Municipality Of Murrysville - West Murrysville',
    'NEW ALEXANDRIA': 'New Alexandria Borough',
    'NEW FLORENCE': 'New Florence Borough',
    'NEW KENSINGTON W 2': 'City Of New Kensington - 2',
    'NEW KENSINGTON W 3 P 1': 'City Of New Kensington - 3-1',
    'NEW KENSINGTON W 3 P 2': 'City Of New Kensington - 3-2',
    'NEW KENSINGTON W 3 P 3': 'City Of New Kensington - 3-3',
    'NEW KENSINGTON W 3 P 4': 'City Of New Kensington - 3-4',
    'NEW KENSINGTON W 4 P 1': 'City Of New Kensington - 4-1',
    'NEW KENSINGTON W 4 P 2': 'City Of New Kensington - 4-2',
    'NEW KENSINGTON W 4 P 3': 'City Of New Kensington - 4-3',
    'NEW KENSINGTON W 4 P 4': 'City Of New Kensington - 4-4',
    'NEW KENSINGTON W 4 P 5': 'City Of New Kensington - 4-5',
    'NEW KENSINGTON W 5': 'City Of New Kensington - 5',
    'NEW KENSINGTON W 6': 'City Of New Kensington - 6',
    'NEW KENSINGTON W 7 P 1': 'City Of New Kensington - 7-1',
    'NEW KENSINGTON W 7 P 2': 'City Of New Kensington - 7-2',
    'NEW STANTON': 'New Stanton Borough',
    'NORTH BELLE VERNON D 1': 'North Belle Vernon - #1',
    'NORTH BELLE VERNON D 2': 'North Belle Vernon - #2',
    'NORTH HUNTINGDON W 1 P 1': 'N Huntingdon Township - 1-1',
    'NORTH HUNTINGDON W 1 P 2': 'N Huntingdon Township - 1-2',
    'NORTH HUNTINGDON W 1 P 3': 'N Huntingdon Township - 1-3',
    'NORTH HUNTINGDON W 1 P 4': 'N Huntingdon Township - 1-4',
    'NORTH HUNTINGDON W 2 P 1': 'N Huntingdon Township - 2-1',
    'NORTH HUNTINGDON W 2 P 2': 'N Huntingdon Township - 2-2',
    'NORTH HUNTINGDON W 2 P 3': 'N Huntingdon Township - 2-3',
    'NORTH HUNTINGDON W 3 P 1': 'N Huntingdon Township - 3-1',
    'NORTH HUNTINGDON W 3 P 2': 'N Huntingdon Township - 3-2',
    'NORTH HUNTINGDON W 4 P 1': 'N Huntingdon Township - 4-1',
    'NORTH HUNTINGDON W 4 P 2': 'N Huntingdon Township - 4-2',
    'NORTH HUNTINGDON W 4 P 3': 'N Huntingdon Township - 4-3',
    'NORTH HUNTINGDON W 4 P 4': 'N Huntingdon Township - 4-4',
    'NORTH HUNTINGDON W 5 P 1': 'N Huntingdon Township - 5-1',
    'NORTH HUNTINGDON W 5 P 2': 'N Huntingdon Township - 5-2',
    'NORTH HUNTINGDON W 5 P 3': 'N Huntingdon Township - 5-3',
    'NORTH HUNTINGDON W 6 P 1': 'N Huntingdon Township - 6-1',
    'NORTH HUNTINGDON W 6 P 2': 'N Huntingdon Township - 6-2',
    'NORTH HUNTINGDON W 6 P 3': 'N Huntingdon Township - 6-3',
    'NORTH HUNTINGDON W 6 P 4': 'N Huntingdon Township - 6-4',
    'NORTH HUNTINGDON W 7 P 1': 'N Huntingdon Township - 7-1',
    'NORTH HUNTINGDON W 7 P 2': 'N Huntingdon Township - 7-2',
    'NORTH HUNTINGDON W 7 P 3': 'N Huntingdon Township - 7-3',
    'NORTH HUNTINGDON W 7 P 4': 'N Huntingdon Township - 7-4',
    'NORTH IRWIN': 'N Irwin Borough',
    'OKLAHOMA': 'Oklahoma Borough',
    'PENN W 1 P 1': 'Penn Township - 1-1',
    'PENN W 1 P 2': 'Penn Township - 1-2',
    'PENN W 2 P 1': 'Penn Township - 2-1',
    'PENN W 2 P 2': 'Penn Township - 2-2',
    'PENN W 3 P 1': 'Penn Township - 3-1',
    'PENN W 3 P 2': 'Penn Township - 3-2',
    'PENN W 4 P 1': 'Penn Township - 4-1',
    'PENN W 4 P 2': 'Penn Township - 4-2',
    'PENN W 5 P 1': 'Penn Township - 5-1',
    'PENN W 5 P 2': 'Penn Township - 5-2',
    'PENN W 5 P 3': 'Penn Township - 5-3',
    'PENN': 'Penn Borough',
    'ROSTRAVER X COLLINSBURG': 'Rostraver Township - Collinsburg',
    'ROSTRAVER X CONCORD': 'Rostraver Township - Concord',
    'ROSTRAVER X CROSS ROADS': 'Rostraver Township - Crossroads',
    'ROSTRAVER X FELLSBURG': 'Rostraver Township - Fellsburg',
    'ROSTRAVER X LYNNWOOD': 'Rostraver Township - Lynnwood',
    'ROSTRAVER X PRICEDALE': 'Rostraver Township - Pricedale',
    'ROSTRAVER X VAN METER': 'Rostraver Township - Van Meter',
    'ROSTRAVER X WEBSTER': 'Rostraver Township - Webster',
    'SALEM X FIVE POINTS': 'Salem Township - Five Points',
    'SALEM X HUGUS': 'Salem Township - Hugus',
    'SALEM X MECHLINGS': 'Salem Township - Mechlings',
    'SALEM X NEW SALEM': 'Salem Township - New Salem',
    'SALEM X SLICKVILLE': 'Salem Township - Slickville',
    'SALEM X STEELES': 'Salem Township - Steeles',
    'SALEM X TREES MILLS': 'Salem Township - Trees Mills',
    'SCOTTDALE W 1': 'Scottdale Borough - 1St Ward',
    'SCOTTDALE W 2': 'Scottdale Borough - 2Nd Ward',
    'SCOTTDALE W 3': 'Scottdale Borough - 3Rd Ward',
    'SCOTTDALE W 4': 'Scottdale Borough - 4Th Ward',
    'SEWARD': 'Seward Borough',
    'SEWICKLEY X EAST HERMINIE': 'Sewickley Township - East Herminie',
    'SEWICKLEY X LOWBER': 'Sewickley Township - Lowber',
    'SEWICKLEY X RILLTON': 'Sewickley Township - Rillton',
    'SEWICKLEY X SEWICKLEY': 'Sewickley Township - Sewickley',
    'SEWICKLEY X WEST HERMINIE': 'Sewickley Township - West Herminie',
    'SEWICKLEY X WHYEL': 'Sewickley Township - Whyel',
    'SMITHTON': 'Smithton Borough',
    'SOUTH GREENSBURG D 1': 'S Greensburg Borough - #1',
    'SOUTH GREENSBURG D 2': 'S Greensburg Borough - #2',
    'SOUTH HUNTINGDON X HIXON': 'South Huntingdon Township - Hixon',
    'SOUTH HUNTINGDON X JACOBS CREEK': 'South Huntingdon Township - Jacobs Creek',
    'SOUTH HUNTINGDON X MINERAL': 'South Huntingdon Township - Mineral',
    'SOUTH HUNTINGDON X PORT ROYAL': 'South Huntingdon Township - Port Royal',
    'SOUTH HUNTINGDON X SOUTH HUNTINGDON': 'South Huntingdon Township - South Huntingdon',
    'SOUTH HUNTINGDON X WAYNE': 'South Huntingdon Township - Wayne',
    'SOUTH HUNTINGDON X WYANO': 'South Huntingdon Township - Wyano',
    'SOUTH HUNTINGDON X YUKON': 'South Huntingdon Township - Yukon',
    'SOUTHWEST GREENSBURG D 1': 'Sw Greensburg Borough - #1',
    'SOUTHWEST GREENSBURG D 2': 'Sw Greensburg Borough - #2',
    'ST. CLAIR X SEWARD': 'St Clair Township - - Seward',
    'ST. CLAIR X ST. CLAIR': 'St Clair Township - - St Clair',
    'SUTERSVILLE': 'Sutersville Borough',
    'TRAFFORD D 1': 'Trafford Borough - #1',
    'TRAFFORD D 2': 'Trafford Borough - #2',
    'UNITY X BAGGALEY': 'Unity Township - Baggaley',
    'UNITY X BEATTY': 'Unity Township - Beatty',
    'UNITY X CRABTREE': 'Unity Township - Crabtree',
    'UNITY X DENNISON': 'Unity Township - Dennison',
    'UNITY X DOROTHY': 'Unity Township - Dorothy',
    'UNITY X GRAVEL HILL': 'Unity Township - Gravel Hill',
    'UNITY X KUHNS': 'Unity Township - Kuhns',
    'UNITY X LLOYDSVILLE': 'Unity Township - Lloydsville',
    'UNITY X MARGUERITE': 'Unity Township - Marguerite',
    'UNITY X MUTUAL': 'Unity Township - Mutual',
    'UNITY X PLEASANT UNITY (cong. 13)': 'Unity Township - Pleasant Unity (Cong. 13)',
    'UNITY X PLEASANT UNITY (Cong. 14)': 'Unity Township - Pleasant Unity (Cong. 14)',
    'UNITY X ROBLE': 'Unity Township - Roble',
    'UNITY X WHITNEY': 'Unity Township - Whitney',
    'UPPER BURRELL X 1': 'Upper Burrell Township - # 1',
    'UPPER BURRELL X 2': 'Upper Burrell Township - # 2',
    'VANDERGRIFT W 1': 'Vandergrift Borough - 1St Ward',
    'VANDERGRIFT W 2': 'Vandergrift Borough - 2Nd Ward',
    'VANDERGRIFT W 3': 'Vandergrift Borough - 3Rd Ward',
    'VANDERGRIFT W 4': 'Vandergrift Borough - 4Th Ward',
    'VANDERGRIFT W 5': 'Vandergrift Borough - 5Th Ward',
    'VANDERGRIFT W 6': 'Vandergrift Borough - 6Th Ward',
    'WASHINGTON X BEAMERS': 'Washington Township - Beamers',
    'WASHINGTON X NORTH WASHINGTON': 'Washington Township - North Washington',
    'WASHINGTON X OAKLAND X ROADS': 'Washington Township - Oakland Cross Roads',
    'WASHINGTON X PAULTON': 'Washington Township - Paulton',
    'WEST LEECHBURG': 'W Leechburg Borough',
    'WEST NEWTON W 1': 'W Newton Borough - 1St Ward',
    'WEST NEWTON W 2': 'W Newton Borough - 2Nd Ward',
    'WEST NEWTON W 3': 'W Newton Borough - 3Rd Ward',
    'YOUNGSTOWN': 'Youngstown Borough',
    'YOUNGWOOD D 1': 'Youngwood Borough - 1St Ward',
    'YOUNGWOOD D 2': 'Youngwood Borough - 2Nd Ward',
    'YOUNGWOOD D 3': 'Youngwood Borough - 3Rd Ward',
}


# ------------------------------------------------------------ prcres reader
def read_prcres(path):
    """Read Westmoreland's 2024 primary rows from the DOS bulk file."""
    contests = OrderedDict()   # (prec_code, prec_name) -> {(office,dist,party): {cno: votes}}
    names = OrderedDict()      # (office, dist, party) -> {cno: ballots order}
    seen = set()
    n_rows = 0
    with open(path, newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 27 or r[0].split(".")[0] != YEAR or r[1] != ETYPE \
                    or r[2].split(".")[0] != COUNTY_CODE:
                continue
            n_rows += 1
            off_code, cno = r[8], r[10]
            party = r[9]
            if off_code not in OFFICE_MAP:
                raise ValueError(f"unhandled office code {off_code!r}")
            office, districted = OFFICE_MAP[off_code]
            district = str(int(float(r[5]))) if districted else ""
            key = (office, district, party)
            entry = names.setdefault(key, [])
            if cno not in entry:
                entry.append(cno)
            desc = r[22].strip()
            if desc not in PRECINCT_OUT:
                raise ValueError(f"unmapped DOS precinct description {desc!r}")
            prec = (int(float(r[3])), PRECINCT_OUT[desc])
            cands = contests.setdefault(prec, OrderedDict())
            cell = cands.setdefault(key, OrderedDict())
            votes = int(float(r[15]))
            cell[cno] = cell.get(cno, 0) + votes
            seen.add((key, cno))
            # sanity: ballot position consistent per candidate
    # candidate coverage check
    n_cands = sum(len(c) for c in names.values())
    n_cand_out = sum(1 for (k, cno) in seen if (k[0], k[1], k[2], cno) not in CANDIDATE_OUT)
    if n_cand_out:
        missing = [(k, cno) for (k, cno) in seen if (k[0], k[1], k[2], cno) not in CANDIDATE_OUT]
        raise ValueError(f"unmapped candidate numbers: {missing[:5]}")
    print(f"prcres: {n_rows} rows, {len(contests)} precinct codes / "
          f"{len(set(p[1] for p in contests))} output precincts, "
          f"{len(names)} contests, {n_cands} candidate rows")
    return contests, names


# ------------------------------------------------------------------ output
def build_rows(contests, names):
    rows = []
    for (prec_code, prec), cands in contests.items():
        for (office, district, party) in sorted(
                cands, key=lambda k: (k[0], k[1], k[2])):
            cell = cands[(office, district, party)]
            order = dict((c, i) for i, c in enumerate(names[(office, district, party)]))
            for cno in sorted(cell, key=lambda c: order.get(c, 99)):
                name = CANDIDATE_OUT[(office, district, party, cno)]
                rows.append({"county": COUNTY, "precinct": prec,
                             "office": office, "district": district,
                             "party": party, "candidate": name,
                             "votes": cell[cno]})
    print(f"rows: {len(rows)}")
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {path}")


# ------------------------------------------------------- official PDF verify
# (contest title -> output office), district parsed from the title
PDF_CONTESTS = {
    "PRESIDENT OF THE UNITED STATES": ("President", ""),
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "ATTORNEY GENERAL": ("Attorney General", ""),
    "AUDITOR GENERAL": ("Auditor General", ""),
    "STATE TREASURER": ("State Treasurer", ""),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", None),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", None),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", None),
    "DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION":
        ("Delegate to the Democratic National Convention", None),
    "DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        ("Delegate to the Republican National Convention", None),
    "ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION":
        ("Alternate Delegate to the Republican National Convention", None),
}
ROW_RE = re.compile(r"^(.+?)\s{2,}([\d,]+)\s*([\d,]*)\s*([\d,]*)\s*([\d,]*)\s*$")


def parse_official_pdf(path):
    """Countywide printed totals: {(office, district, party): {name: total}}
    plus the printed Write-In Totals per contest."""
    import pdfplumber

    anchor = OrderedDict()
    writeins = {}
    cur = None
    with pdfplumber.open(path) as pdf:
        text = "\n".join((p.extract_text(layout=True) or "") for p in pdf.pages)
    for line in text.splitlines():
        if not line.strip():
            continue
        t = line.strip()
        if t.startswith("Summary Results Report") or \
                t.startswith("Westmoreland Official") or \
                t.startswith("Vote For") or t.startswith("STATISTICS") or \
                re.match(r"^Page \d+ of \d+$", t):
            continue
        if "Election" in t and "Absentee" in t:
            continue
        m = re.match(r"^(DEM|REP|NONPARTISAN)\s+(.+)$", t)
        if m and m.group(1) in ("DEM", "REP", "NONPARTISAN"):
            party = m.group(1)
            # layout extraction can inject doubled spaces inside the title
            title = re.sub(r"\s+", " ", m.group(2)).strip()
            hit = None
            for key, val in PDF_CONTESTS.items():
                if title.upper().startswith(key):
                    rest = title[len(key):].strip()
                    md = re.match(r"(\d+)(?:st|nd|rd|th)?\s+District$", rest, re.I)
                    dist = md.group(1) if md else (rest.strip() if rest else "")
                    hit = (val[0], dist, party)
                    break
            if hit is None:
                print(f"note: unrecognized section title {title!r}")
                continue
            cur = hit
            anchor.setdefault(cur, OrderedDict())
            continue
        if cur is None:
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        name = m.group(1).strip()
        total = int(m.group(2).replace(",", "")) if m.group(2) else None
        if total is None:
            continue
        if name == "Write-In Totals":
            writeins[cur] = writeins.get(cur, 0) + total
        elif name.lower().startswith("not assigned") or \
                name.startswith("Write-In:"):
            pass
        else:
            anchor[cur][name] = anchor[cur].get(name, 0) + total
    return anchor, writeins


def verify(anchor, writeins, rows):
    """Contest-level check: our per-precinct sums vs official printed
    names/total rows.  Write-ins are excluded (no per-precinct source)."""
    print("\n--- verification vs official county report ---")
    ours = defaultdict(int)
    for r in rows:
        ours[(r["office"], r["district"], r["party"], r["candidate"])] += r["votes"]
    n_exact = n_diff = n_no_prec = 0
    for (office, district, party), pdf_cands in anchor.items():
        # official totals are the sum over the ballot's named candidates
        o_sum = sum(v for (o, d, p, c), v in ours.items()
                    if o == office and d == district and p == party)
        p_sum = sum(pdf_cands.values())
        line = (f"{office or '-'} {district or '-'} {party}: ours(named) {o_sum} "
                f"official(named) {p_sum}")
        if not pdf_cands:
            wi = writeins.get((office, district, party), 0)
            print(f"  {line}; WRITE-IN ONLY contest, official Write-In Totals "
                  f"{wi}, no DOS rows -> omitted")
            continue
        # candidate-level
        pmap = {}
        problems = []
        got = {c: v for (o, d, p, c), v in ours.items()
               if o == office and d == district and p == party}
        for cand, tot in pdf_cands.items():
            g = got.get(cand, None)
            pmap[cand] = g
            if g is None:
                problems.append(f"{cand}: no precinct rows (official {tot})")
                n_no_prec += 1
            elif g != tot:
                problems.append(f"{cand}: ours {g} official {tot}")
                n_diff += 1
            else:
                n_exact += 1
        extra = [c for c in got if c not in pmap]
        if extra:
            problems.append(f"candidates we emit that official doesn't list: {extra}")
        print("  " + line + (" -> exact" if not problems else
                             " -> MISMATCH: " + "; ".join(problems)))
    # contests in DOS but not in the official report?
    official_keys = set(anchor)
    for (office, district, party) in {k[:3] for k in ours}:
        if (office, district, party) not in official_keys:
            print(f"  contest only in DOS (no official section): "
                  f"{office} {district} {party}")
    print(f"candidate-level: {n_exact} exact, {n_diff} mismatched, "
          f"{n_no_prec} official names without precinct rows")
    print(f"DOS carries no write-in rows, so per-precinct write-ins do not "
          f"exist; countywide official Write-In Totals per contest:")
    for k, v in sorted(writeins.items()):
        if v:
            print(f"    {k[0]} {k[1]} {k[2]}: {v}")


# --------------------------------------------------------------------- main
def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    prcres, outfile = argv[1:3]
    contests, names = read_prcres(prcres)
    rows = build_rows(contests, names)
    write_csv(outfile, rows)
    if len(argv) > 3:
        anchor, writeins = parse_official_pdf(argv[3])
        verify(anchor, writeins, rows)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
