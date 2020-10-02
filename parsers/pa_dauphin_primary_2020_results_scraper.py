import csv
import os
import requests
from lxml import html
from time import sleep

COUNTY = 'Dauphin'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__dauphin__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

DAUPHIN_URL = 'http://www.dauphinc.org/election/Race'
PAGE_READ_THROTTLE_IN_SECONDS = 30
RACE_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT = {
    'PRESIDENT - D (DEM)': ('President', 'DEM', ''),
    'PRESIDENT - R (REP)': ('President', 'REP', ''),
    'ATTORNEY GENERAL - D (DEM)': ('Attorney General', 'DEM', ''),
    'ATTORNEY GENERAL - R (REP)': ('Attorney General', 'REP', ''),
    'AUDITOR GENERAL - D (DEM)': ('Auditor General', 'DEM', ''),
    'AUDITOR GENERAL - R (REP)': ('Auditor General', 'REP', ''),
    'STATE TREASURER - D (DEM)': ('State Treasurer', 'DEM', ''),
    'STATE TREASURER - R (REP)': ('State Treasurer', 'REP', ''),
    'REPRESENTATIVE IN CONGRESS - D (DEM)': ('U.S. House', 'DEM', 10),
    'REPRESENTATIVE IN CONGRESS - R (REP)': ('U.S. House', 'REP', 10),
    'SENATOR IN THE GENERAL ASSEMBLY - D15 (DEM)': ('State Senate', 'DEM', 15),
    'SENATOR IN THE GENERAL ASSEMBLY - R15 (REP)': ('State Senate', 'REP', 15),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D98 (DEM)': ('General Assembly', 'DEM', 98),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R98 (REP)': ('General Assembly', 'REP', 98),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D103 (DEM)': ('General Assembly', 'DEM', 103),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R103 (REP)': ('General Assembly', 'REP', 103),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D104 (DEM)': ('General Assembly', 'DEM', 104),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R104 (REP)': ('General Assembly', 'REP', 104),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D105 (DEM)': ('General Assembly', 'DEM', 105),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R105 (REP)': ('General Assembly', 'REP', 105),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D106 (DEM)': ('General Assembly', 'DEM', 106),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R106 (REP)': ('General Assembly', 'REP', 106),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - D125 (DEM)': ('General Assembly', 'DEM', 125),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY - R125 (REP)': ('General Assembly', 'REP', 125),
}


def process_race(race_html_tree):
    table = race_html_tree.xpath(f'//table')[-1]
    header, *rows = table.xpath(f'.//tr')
    candidates = header.xpath('.//td/text()')
    for row in rows:
        precinct, *candidate_votes = row.xpath(f'.//td/text()')
        for candidate, votes in zip(candidates, candidate_votes):
            candidate = candidate.title()
            votes = int(votes.strip())
            yield {'precinct': precinct, 'candidate': candidate, 'votes': votes}


def get_html_tree(race):
    post_data = {
        'Key': '27',
        'SelectedRaceOrPrecinct': 'Race',
        'SelectedValue': race,
    }
    response = requests.post(DAUPHIN_URL, data=post_data)
    return html.fromstring(response.content.decode("utf-8"))


def iterate_html_races():
    for race in RACE_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT:
        office, party, district = RACE_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT[race]
        print(f'Processing {race}')
        race_html_tree = get_html_tree(race)
        for row in process_race(race_html_tree):
            row.update(county=COUNTY, office=office, party=party, district=district)
            yield row
        sleep(PAGE_READ_THROTTLE_IN_SECONDS)


def html_races_to_csv():
    with open(OUTPUT_FILE, 'w', newline='') as f_out:
        csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
        csv_writer.writeheader()
        for row in iterate_html_races():
            csv_writer.writerow(row)


if __name__ == "__main__":
    html_races_to_csv()
