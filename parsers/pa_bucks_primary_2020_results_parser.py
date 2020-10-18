import os
import csv
from parsers.pa_pdf_parser import PDFStringIterator, PDFPageIterator

COUNTY = 'BUCKS'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__bucks__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'provisional', 'votes']

BUCKS_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                          'Bucks PA 2020generalprimarycertifiedelectionresultsbypercinct.pdf')
PAGE_HEADER = [
    'Certified Returns by Precinct',
    'Bucks County General Primary Election, June 02, 2020',
    'All Precincts, All Districts, Absentee, Election Day, Provisional Ballot, All ScanStations, All Contests, All',
    'Boxes',
    'Certified June 23, 2020',
    'Total Ballots Cast: 157903',
    '304 precincts reported out of 304 total',
    'Choice',
    'Votes',
    'Vote %',
    'AB',
    'ED',
    'PR'
]

PRECINCT_PREFIX = 'Precinct '
LAST_PRECINCT = 'All Precincts'
PAGE_FOOTER_PREFIX = 'Page: '
LAST_ROW_CANDIDATE = 'Total'
VOTE_HEADER = ['votes', None, 'absentee', 'election_day', 'provisional']

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 1),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 18th': ('General Assembly', 18),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY  29th': ('General Assembly', 29),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 29th': ('General Assembly', 29),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 31st': ('General Assembly', 31),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY  140th': ('General Assembly', 140),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 140th': ('General Assembly', 140),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 141st': ('General Assembly', 141),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY  142nd': ('General Assembly', 142),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 142nd': ('General Assembly', 142),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 143rd': ('General Assembly', 143),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 144th': ('General Assembly', 144),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY  145th': ('General Assembly', 145),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 145th': ('General Assembly', 145),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 178th': ('General Assembly', 178),
}


class BucksPDFPageParser:
    def __init__(self, page, continued_table_header, continued_precinct):
        self._string_iterator = PDFStringIterator(page.get_strings())
        self._validate_and_skip_page_header()
        self._continued_table_header = continued_table_header
        self._table_header = None
        self._precinct = None
        self._continued_precinct = continued_precinct

    def __iter__(self):
        while not self._page_is_done():
            self._precinct = self._get_precinct()
            if self._precinct == LAST_PRECINCT:
                break
            while not self._precinct_is_done() and not self._page_is_done():
                yield from self._iterate_precinct_data()

    def get_continued_table_header(self):
        return self._table_header or self._continued_table_header

    def get_continued_precinct(self):
        return self._precinct or self._continued_precinct

    def _iterate_precinct_data(self):
        self._table_header = self._get_table_header()
        office, district, party = self._extract_office_district_and_party()
        for row in self._iterate_office_data():
            row.update(office=office, district=district, party=party)
            if self._row_is_valid(row):
                yield row

    def _iterate_office_data(self):
        while self._table_header and not self._page_is_done():
            candidate = self._get_candidate()
            row = {'county': COUNTY, 'precinct': self._precinct, 'candidate': candidate}
            self._populate_vote_data(row)
            if candidate == LAST_ROW_CANDIDATE:
                self._table_header = None
            yield row

    def _populate_vote_data(self, row):
        for vote_type in VOTE_HEADER:
            vote_count = next(self._string_iterator)
            if vote_type:
                row[vote_type] = int(vote_count)

    def _get_precinct(self):
        precinct = None
        if self._continued_precinct:
            precinct = self._continued_precinct
            self._continued_precinct = None
        if self._precinct_is_done():
            precinct = next(self._string_iterator)
            if precinct != LAST_PRECINCT:
                _, precinct = precinct.split(PRECINCT_PREFIX)
        assert precinct
        return precinct.strip()

    def _get_table_header(self):
        if self._continued_table_header:
            table_header = self._continued_table_header
            self._continued_table_header = None
            return table_header
        return next(self._string_iterator)

    def _get_candidate(self):
        candidate = next(self._string_iterator).title()
        candidate, *_ = candidate.split(' - ', 1)
        return candidate

    def _extract_office_district_and_party(self):
        office, party = self._table_header.split('(', 1)
        office, *_ = office.split(' - ', 1)
        party = party[:3].upper()
        assert party in ['REP', 'DEM'] or office.startswith('Question')
        district = ''
        if office in RAW_OFFICE_TO_OFFICE_AND_DISTRICT:
            office, district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT[office]
        office = office.title()
        return office, district, party

    def _validate_and_skip_page_header(self):
        assert [next(self._string_iterator) for _ in range(len(PAGE_HEADER))] == PAGE_HEADER

    def _page_is_done(self):
        return self._string_iterator.peek().startswith(PAGE_FOOTER_PREFIX)

    def _precinct_is_done(self):
        s = self._string_iterator.peek()
        return s.startswith(PRECINCT_PREFIX) or s == LAST_PRECINCT

    @staticmethod
    def _row_is_valid(row):
        if 'Delegate' in row['office']:
            return False
        if 'Question' in row['office']:
            return False
        if ' Man' in row['office']:
            return False
        if ' Woman' in row['office']:
            return False
        if row['candidate'] == LAST_ROW_CANDIDATE:
            return False
        return True


def pdf_to_csv(pdf, csv_writer):
    csv_writer.writeheader()
    previous_table_header = None
    previous_precinct = None
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = BucksPDFPageParser(page, previous_table_header, previous_precinct)
        for row in pdf_page_parser:
            csv_writer.writerow(row)
        previous_table_header = pdf_page_parser.get_continued_table_header()
        previous_precinct = pdf_page_parser.get_continued_precinct()


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BUCKS_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER))
