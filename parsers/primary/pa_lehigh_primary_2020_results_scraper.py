import csv
import os
import requests
import urllib3
from lxml import html
from time import sleep


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

COUNTY = 'Lehigh'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__lehigh__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

RAW_OFFICE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT = {
    'President of the United States': ('President', ''),
    'Representative in Congress in the 7th Congressional District': ('U.S. House', 7),
    'Senator in the General Assembly': 'State Senate',
    'Representative in the General Assembly in the 22nd Representative District': ('General Assembly', 22),
    'Representative in the General Assembly in the 131st Representative District': ('General Assembly', 131),
    'Representative in the General Assembly in the 132nd Representative District': ('General Assembly', 132),
    'Representative in the General Assembly in the 133rd Representative District': ('General Assembly', 133),
    'Representative in the General Assembly in the 134th Representative District': ('General Assembly', 134),
    'Representative in the General Assembly in the 183rd Representative District': ('General Assembly', 183),
    'Representative in the General Assembly in the 187th Representative District': ('General Assembly', 187),
}

ELECTION_ID = '9b9b5c8bfcae482ca36cedc078916a4b'
FEDERAL_CANDIDATES_CATEGORY_ID = '87108f63b7fb499aa2c4d216d14bcbe5'
STATE_CANDIDATES_CATEGORY_ID = 'e0cc9809a21a403dbc5427e2ecd818e8'
COUNTY_CANDIDATES_CATEGORY_ID = 'b1253fda2dd04239a834bd90163a78af'
LEHIGH_BASE_URL = f'https://home.lehighcounty.org/TallyHo'
QUERY_SPACING_IN_SECONDS = 30  # don't spam requests; total process STATE_CANDIDATES_CATEGORY_ID be <50 queries


def get_candidate_urls():
    for category_id in [FEDERAL_CANDIDATES_CATEGORY_ID, STATE_CANDIDATES_CATEGORY_ID, COUNTY_CANDIDATES_CATEGORY_ID]:
        url = f'{LEHIGH_BASE_URL}/ElectionResultsView.aspx?election={ELECTION_ID}&category={category_id}'
        response = requests.get(url, verify=False)
        html_tree = html.fromstring(response.content.decode('utf-8'))
        yield from html_tree.xpath('//div[contains(@class, "col-districts-body")]/a/@href')


def process_candidate_paths(candidate_paths):
    for candidate_path in candidate_paths:
        sleep(QUERY_SPACING_IN_SECONDS)
        yield from process_candidate_url('/'.join([LEHIGH_BASE_URL, candidate_path]))


def process_candidate_url(url):
    response = requests.get(url, verify=False)
    html_tree = html.fromstring(response.content.decode('utf-8'))
    candidate = html_tree.xpath('//span[@id="candidateName"]/text()')[0].split('By District for: ')[1]
    office, party, district = extract_office_party_and_district(html_tree)
    if 'Delegate' not in office and 'Committee' not in office:
        print(f'Processing candidate `{candidate}`')
        for row in process_candidate(html_tree):
            row.update(office=office, party=party, district=district, candidate=candidate)
            yield row


def process_candidate(html_tree):
    precincts = html_tree.xpath('.//div[contains(@class, "districts-row ")]')
    for precinct in precincts:
        _, precinct_name, vote_count, _ = precinct.xpath('.//div/text()')
        yield {'county': COUNTY, 'precinct': precinct_name, 'votes': int(vote_count)}


def extract_office_party_and_district(html_tree):
    office = html_tree.xpath('//div[@class="race-title"]/text()')[0].strip()
    party, office = office.split(' ', 1)
    district = ''
    if office in RAW_OFFICE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT:
        office, district = RAW_OFFICE_TO_OPENELECTIONS_OFFICE_AND_DISTRICT[office]
    return office, party, district


def main():
    with open(OUTPUT_FILE, 'w', newline='') as f_out:
        csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
        csv_writer.writeheader()
        urls = get_candidate_urls()
        for row in process_candidate_paths(urls):
            csv_writer.writerow(row)


if __name__ == "__main__":
    main()
