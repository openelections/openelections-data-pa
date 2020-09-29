import csv
import os
import requests
from lxml import html
from time import sleep

COUNTY = 'Delaware'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__delaware__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'votes']

DELAWARE_REPORT_URLS = 'http://election.co.delaware.pa.us/eb/June_2020/reports/{}.html'
PAGE_READ_THROTTLE_IN_SECONDS = 3

MAX_REPORT_ID = 429
REPORT_ID_RANGE = range(1, MAX_REPORT_ID + 1)
SKIPPED_REPORT_ID = 351

PARTY_ABBREVIATIONS = {
    'Republican Party': 'REP',
    'Democratic Party': 'DEM',
}

OFFICE_AND_DISTRICT_MAPPING = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 5TH DISTRICT': ('U.S. House', 5),
    'REPRESENTATIVE IN THE  GENERAL ASSEMBLY 159TH DISTRICT': ('General Assembly', 159),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 160TH DISTRICT': ('General Assembly', 160),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 161ST DISTRICT': ('General Assembly', 161),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 162ND DISTRICT': ('General Assembly', 162),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 163RD DISTRICT': ('General Assembly', 163),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 164TH DISTRICT': ('General Assembly', 164),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 165TH DISTRICT': ('General Assembly', 165),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 166TH DISTRICT': ('General Assembly', 166),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 168TH DISTRICT': ('General Assembly', 168),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 185TH DISTRICT': ('General Assembly', 185),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 191ST DISTRICT': ('General Assembly', 191),
    'SENATOR IN THE GENERAL ASSEMBLY 9TH DISTRICT': ('State Senate', 9),
    'SENATOR IN THE GENERAL ASSEMBLY 17TH DISTRICT': ('State Senate', 17),
}

PRECINCT_HTML_CLASS = 'a60l'
OFFICE_TABLE_HTML_CLASS = 'a632'
HEADER_HTML_CLASS = 'a158'
CANDIDATE_HTML_CLASS = 'a221'
ELECTION_DAY_VOTES_HTML_CLASS = 'a233'
MAIL_IN_VOTES_HTML_CLASS = 'a245'
TOTAL_VOTES_HTML_CLASS = 'a269'
ELECTION_DAY_WRITE_IN_VOTES_HTML_CLASS = 'a557'
MAIL_IN_WRITE_IN_VOTES_HTML_CLASS = 'a569'
TOTAL_WRITE_IN_VOTES_HTML_CLASS = 'a593'


class OfficeTableParser:
    def __init__(self, office_table):
        self._office_table = office_table

    def __iter__(self):
        office, district, party = self._extract_office_data()
        if self._should_be_recorded(office):
            candidate_data = self._extract_candidate_data()
            for candidate, election_day, mail_in, votes in candidate_data:
                yield {'office': office, 'party': party,
                       'district': district, 'candidate': candidate,
                       'election_day': election_day, 'mail_in': mail_in, 'votes': votes}

    def _extract_office_data(self):
        header = self._extract_text(HEADER_HTML_CLASS)[0]
        office_and_district, party_string, _ = header.split(' - ')
        party = PARTY_ABBREVIATIONS[party_string]
        if office_and_district in OFFICE_AND_DISTRICT_MAPPING:
            office, district = OFFICE_AND_DISTRICT_MAPPING[office_and_district]
        else:
            office = office_and_district.title()
            district = ''
        return office, district, party

    def _extract_candidate_data(self):
        candidates = self._extract_candidates() + ['Write-in']
        election_day_votes = self._extract_votes(ELECTION_DAY_VOTES_HTML_CLASS) + \
            self._extract_votes(ELECTION_DAY_WRITE_IN_VOTES_HTML_CLASS, write_in=True)
        mail_in_votes = self._extract_votes(MAIL_IN_VOTES_HTML_CLASS) + \
            self._extract_votes(MAIL_IN_WRITE_IN_VOTES_HTML_CLASS, write_in=True)
        total_votes = self._extract_votes(TOTAL_VOTES_HTML_CLASS) + \
            self._extract_votes(TOTAL_WRITE_IN_VOTES_HTML_CLASS, write_in=True)
        return zip(candidates, election_day_votes, mail_in_votes, total_votes)

    def _extract_candidates(self):
        candidates = self._extract_text(CANDIDATE_HTML_CLASS)
        return [candidate.strip().rsplit(' ', 1)[0].title() for candidate in candidates]

    def _extract_votes(self, html_class, write_in=False):
        votes = self._extract_text(html_class, html_tag='td' if write_in else 'div')
        return [int(vote) for vote in votes]

    def _extract_text(self, html_class, html_tag='div'):
        return self._office_table.xpath(f'.//{html_tag}[@class="{html_class}"]/text()')

    @staticmethod
    def _should_be_recorded(office):
        return 'Delegate' not in office and 'Committee' not in office


def get_report_html_tree(report_id):
    report_url = DELAWARE_REPORT_URLS.format(report_id)
    response = requests.get(report_url)
    report_html_string = response.content.decode("utf-8")
    return html.fromstring(report_html_string)


def process_report(report_id):
    report_html_tree = get_report_html_tree(report_id)
    precinct = report_html_tree.xpath(f'//td[@class="{PRECINCT_HTML_CLASS}"]/text()')[0]
    office_tables = report_html_tree.xpath(f'//table[@class="{OFFICE_TABLE_HTML_CLASS}"]')
    for office_table in office_tables:
        office_table_parser = OfficeTableParser(office_table)
        for row in office_table_parser:
            row.update(county=COUNTY, precinct=precinct)
            yield row


def process_reports():
    for report_id in REPORT_ID_RANGE:
        print(f'Processing precinct {report_id} of {MAX_REPORT_ID}')
        if report_id != SKIPPED_REPORT_ID:
            yield from process_report(report_id)
            sleep(PAGE_READ_THROTTLE_IN_SECONDS)  # don't hammer the Delaware County website


def html_reports_to_csv(csv_writer):
    csv_writer.writeheader()
    for row in process_reports():
        csv_writer.writerow(row)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        html_reports_to_csv(csv.DictWriter(f, OUTPUT_HEADER))
