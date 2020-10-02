import csv
import json
import os
import requests
from time import sleep


COUNTY = 'Montgomery'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__montgomery__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

RAW_PARTY_TO_OPENELECTIONS_PARTY = {
    'Republican': 'REP',
    'Democratic': 'DEM'
}
RAW_OFFICE_TO_OPENELECTIONS_OFFICE = {
    'President of the United States': 'President',
    'Representative in Congress': 'U.S. House',
    'Senator in the General Assembly': 'State Senate',
    'Representative in the General Assembly': 'General Assembly',
}

MONTGOMERY_PRIMARY_2020_ARCGIS_ID = 'kOChldNuKsox8qZD'
ARCGIS_SERVICES_PATH = f'https://services1.arcgis.com/{MONTGOMERY_PRIMARY_2020_ARCGIS_ID}/arcgis/rest/services/'
MONTGOMERY_PRIMARY_2020_RESULTS_DASHBOARD_QUERY_PATH = 'Election_Results_dashboard_PE20/FeatureServer/1/query'
MONTGOMERY_PRIMARY_2020_RESULTS_URL = ARCGIS_SERVICES_PATH + MONTGOMERY_PRIMARY_2020_RESULTS_DASHBOARD_QUERY_PATH

QUERY_FIELDS = ['Contest', 'Party', 'Candidate', 'Precinct_Name', 'NumVotes']
QUERY_RECORD_BLOCK_SIZE = 1000
QUERY_SPACING_IN_SECONDS = 30  # don't spam requests; total process should be <50 queries


class ArcgisIterator:
    def __iter__(self):
        result_offset = 0
        done = False
        while not done:
            print(f'processing feature {result_offset + 1} - {result_offset + QUERY_RECORD_BLOCK_SIZE}')
            features = self._get_next_feature_block(result_offset)
            for feature in features:
                yield feature
            done = len(features) < QUERY_RECORD_BLOCK_SIZE
            if not done:
                result_offset += QUERY_RECORD_BLOCK_SIZE
                sleep(QUERY_SPACING_IN_SECONDS)

    def _get_next_feature_block(self, result_offset):
        params = {
            'f': 'json',
            'where': '(1=1)',
            'outFields': ','.join(QUERY_FIELDS),
            'orderByFields': ','.join(QUERY_FIELDS) + " DESC",
            'resultOffset': result_offset,
            'resultRecordCount': QUERY_RECORD_BLOCK_SIZE,
            'quantizationParameters': '{"mode":"edit"}',
        }
        response = requests.get(MONTGOMERY_PRIMARY_2020_RESULTS_URL, params)
        json_data = json.loads(response.text)
        return json_data['features']


def extract_party_from_office(office, party):
    if office.endswith('REP') or office.endswith('DEM'):
        office, expected_party = office.rsplit(' ', 1)
        assert (party == expected_party)
    return office, party


def extract_district_from_office(office):
    district = ''
    if office.endswith('District'):
        office, district, _ = office.rsplit(' ', 2)
        for suffix in ['st', 'nd', 'rd', 'th']:
            district = district.replace(suffix, '')
        district = int(district)
    return office, district


def process_features(arcgis_iterator):
    for feature in arcgis_iterator:
        raw_data = feature['attributes']
        office = raw_data['Contest']
        office_is_invalid = 'Delegate' in office
        if not office_is_invalid:
            party = RAW_PARTY_TO_OPENELECTIONS_PARTY.get(raw_data['Party'], '')
            office, party = extract_party_from_office(office, party)
            office, district = extract_district_from_office(office)
            office = RAW_OFFICE_TO_OPENELECTIONS_OFFICE.get(office, office)
            yield {
                'county': COUNTY,
                'precinct': raw_data['Precinct_Name'],
                'office': office,
                'district': district,
                'party': party,
                'candidate': raw_data['Candidate'].title(),
                'votes': raw_data['NumVotes']
            }


def main():
    with open(OUTPUT_FILE, 'w', newline='') as f_out:
        csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
        csv_writer.writeheader()
        for row in process_features(ArcgisIterator()):
            csv_writer.writerow(row)


if __name__ == "__main__":
    main()
