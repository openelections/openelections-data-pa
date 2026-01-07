import csv
import os
import json
import requests


COUNTY = 'Luzerne'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__luzerne__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

LUZERNE_CLARITY_URL = 'https://results.enr.clarityelections.com/PA/Luzerne/103399/254832/json'
SUMMARY_JSON_URL = f'{LUZERNE_CLARITY_URL}/en/summary.json'
DETAILS_JSON_URL = f'{LUZERNE_CLARITY_URL}/details.json'

RACE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT = {
    'President of the United States': ('President', ''),
    'Representative in Congress - 8th Congressional District': ('U.S. House', 8),
    'Representative in Congress - 9th Congressional District': ('U.S. House', 9),
    'Representative in the General Assembly 116th': ('General Assembly', 116),
    'Representative in the General Assembly 117th': ('General Assembly', 117),
    'Representative in the General Assembly 118th': ('General Assembly', 118),
    'Representative in the General Assembly 119th': ('General Assembly', 119),
    'Representative in the General Assembly 120th': ('General Assembly', 120),
    'Representative in the General Assembly 121st': ('General Assembly', 121),
    'Senator in the General Assembly-27th Senatorial District': ('State Senate', 27),
}


def jsons_to_csv():
    summary_json = json.loads(requests.get(SUMMARY_JSON_URL).text)
    details_json = json.loads(requests.get(DETAILS_JSON_URL).text)
    with open(OUTPUT_FILE, 'w', newline='') as f:
        csv_writer = csv.DictWriter(f, OUTPUT_HEADER)
        csv_writer.writeheader()
        for row in iterate_candidate_level_data(details_json, summary_json):
            csv_writer.writerow(row)


def iterate_candidate_level_data(details_json, summary_json):
    contest_key_to_contest = {contest['K']: contest for contest in summary_json}
    for contest_details in details_json['Contests']:
        contest = contest_key_to_contest[contest_details['K']]
        contest_name = contest['C']
        if 'Committee' not in contest_name and 'Delegate' not in contest_name:
            yield from iterate_contest_level_data(contest, contest_details, contest_name)


def iterate_contest_level_data(contest, contest_details, contest_name):
    office, party, district = extract_office_party_and_district(contest_name)
    candidates = contest['CH']
    precincts = contest_details['P']
    vote_data = contest_details['V']
    assert len(vote_data) == len(precincts)
    for precinct, precinct_vote_data in zip(precincts, vote_data):
        assert len(precinct_vote_data) == len(candidates)
        for candidate, votes in zip(candidates, precinct_vote_data):
            yield {'county': COUNTY, 'precinct': precinct,
                   'office': office, 'district': district, 'party': party,
                   'candidate': candidate, 'votes': votes}


def extract_office_party_and_district(contest_name):
    office, party = contest_name.replace(')', '').split(' (', 1)
    district = ''
    if office in RACE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT:
        office, district = RACE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT[office]
    return office.strip(), party.strip(), district


if __name__ == "__main__":
    jsons_to_csv()