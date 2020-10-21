import csv
import os
import requests
from io import BytesIO
from pdfreader import SimplePDFViewer
from time import sleep
from parsers.pa_pdf_parser import PDFPageIterator, PDFStringIterator

COUNTY = 'Armstrong'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__armstrong__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

ARMSTRONG_URL = 'https://co.armstrong.pa.us/images/resources/electionresults/official/{}.pdf'
FIRST_PRECINCT_ID = 1
LAST_PRECINCT_ID = 62
QUERY_SPACING_IN_SECONDS = 3


GENERIC_TABLE_HEADER_FIELD = 'Total'
CANDIDATE_TABLE_HEADER_FIELDS = ['Candidate', 'Party', 'Total']
PRECINCTS_REPORTED = 'Precincts Reported: '
TIMES_CAST_FIELD = 'Times Cast'
UNUSED_CONTENT_FIELDS = (GENERIC_TABLE_HEADER_FIELD, TIMES_CAST_FIELD, CANDIDATE_TABLE_HEADER_FIELDS[0])
PAGE_HEADER_FIELD = 'Page: '
PAGE_FOOTER_PREFIX = '6/10'
TABLE_TOTAL_VOTES = 'Total Votes'
UNRESOLVED_WRITE_IN = 'Unresolved Write-In'
WRITE_IN_PARTY = 'WRITE-IN'

RAW_OFFICE_TO_OFFICE_PARTY_AND_DISTRICT = {
    'ATTORNEY GENERAL (DEM)': ('Attorney General', 'DEM', ''),
    'ATTORNEY GENERAL (REP)': ('Attorney General', 'REP', ''),
    'AUDITOR GENERAL (DEM)': ('Auditor General', 'DEM', ''),
    'AUDITOR GENERAL (REP)': ('Auditor General', 'REP', ''),
    'PRESIDENT OF THE UNITED STATES (DEM)': ('President', 'DEM', ''),
    'PRESIDENT OF THE UNITED STATES (REP)': ('President', 'REP', ''),
    'REPRESENTATIVE IN CONGRESS 15th CONGRESSIONAL DISTRICT (DEM)': ('U.S. House', 'DEM', 15),
    'REPRESENTATIVE IN CONGRESS 15th CONGRESSIONAL DISTRICT (REP)': ('U.S. House', 'REP', 15),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 55th LEGISLATIVE DISTRICT (DEM)': ('General Assembly', 'DEM', 55),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 55th LEGISLATIVE DISTRICT (REP)': ('General Assembly', 'REP', 55),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 60th LEGISLATIVE DISTRICT (DEM)': ('General Assembly', 'DEM', 60),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 60th LEGISLATIVE DISTRICT (REP)': ('General Assembly', 'REP', 60),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 63rd LEGISLATIVE DISTRICT (DEM)': ('General Assembly', 'DEM', 63),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 63rd LEGISLATIVE DISTRICT (REP)': ('General Assembly', 'REP', 63),
    'SENATOR IN THE GENERAL ASSEMBLY 41st SENATORIAL DISTRICT (DEM)': ('State Senate', 'DEM', 41),
    'SENATOR IN THE GENERAL ASSEMBLY 41st SENATORIAL DISTRICT (REP)': ('State Senate', 'REP', 41),
    'STATE TREASURER (DEM)': ('State Treasurer', 'DEM', ''),
    'STATE TREASURER (REP)': ('State Treasurer', 'REP', ''),
}


class ArmstrongPDFStringIterator(PDFStringIterator):
    def page_is_done(self):
        return self.peek().startswith(PAGE_FOOTER_PREFIX)

    def table_is_next(self):
        return self.peek() in (GENERIC_TABLE_HEADER_FIELD, CANDIDATE_TABLE_HEADER_FIELDS[0])

    def is_next_field_generic_table_header(self):
        return self.peek() == GENERIC_TABLE_HEADER_FIELD

    def new_candidate_table_available(self):
        return self.peek() == CANDIDATE_TABLE_HEADER_FIELDS[0]

    def is_precincts_reported_next(self):
        return self.peek() == PRECINCTS_REPORTED


class ArmstrongPDFPageIterator(PDFPageIterator):
    def __init__(self, precinct_id):
        super().__init__(filename=None)
        response = requests.get(ARMSTRONG_URL.format(precinct_id))
        self._pdf_viewer = SimplePDFViewer(BytesIO(response.content))


class ArmstrongPDFTableParser:
    def __init__(self, string_iterator, write_in_votes_outstanding):
        self._string_iterator = string_iterator
        self._write_in_votes = write_in_votes_outstanding
        self._table_is_done = False
        self._skip_table_header()

    def __iter__(self):
        while not self._table_is_done and not self._string_iterator.page_is_done():
            if self._string_iterator.is_next_field_generic_table_header():
                self._table_is_done = True
            else:
                yield from self._process_candidate()

    def get_unprocessed_write_in_votes(self):
        return self._write_in_votes

    def _process_candidate(self):
        candidate = next(self._string_iterator)
        if self._process_times_cast_row(candidate):
            self._table_is_done = True
        elif self._process_total_votes_row(candidate):
            self._table_is_done = True
        elif self._process_unresolved_write_in(candidate):
            self._table_is_done = True
        else:
            yield from self._process_candidate_to_row(candidate)

    def _process_times_cast_row(self, candidate):
        if candidate == TIMES_CAST_FIELD:
            # ['Times Cast', 'XXX', ' ', '/', ' ', 'XXX', 'XX.XX%']
            [next(self._string_iterator) for _ in range(6)]
            return True
        return False

    def _process_total_votes_row(self, candidate):
        if candidate == TABLE_TOTAL_VOTES:
            next(self._string_iterator)  # skip total votes count
            return True
        return False

    def _process_unresolved_write_in(self, candidate):
        if candidate == UNRESOLVED_WRITE_IN:
            self._write_in_votes += int(next(self._string_iterator))
            return True
        return False

    def _process_candidate_to_row(self, candidate):
        candidate = self._get_full_candidate_name(candidate)
        party = next(self._string_iterator)
        votes_string = next(self._string_iterator)
        votes = int(votes_string)
        row = {'candidate': candidate.title(), 'party': party, 'votes': votes}
        if not self._process_write_in_data(row):
            yield row

    def _process_write_in_data(self, row):
        if row['party'] == WRITE_IN_PARTY:
            self._write_in_votes += row['votes']
            return True
        return False

    def _get_full_candidate_name(self, candidate):
        while self._string_iterator.peek().strip():
            candidate += next(self._string_iterator)
        next(self._string_iterator)  # skip empty string
        return candidate

    def _skip_table_header(self):
        if self._string_iterator.is_next_field_generic_table_header():
            next(self._string_iterator)
        else:
            assert [next(self._string_iterator) for _ in range(3)] == CANDIDATE_TABLE_HEADER_FIELDS


class ArmstrongPDFPageParser:
    def __init__(self, page, previous_page_parser):
        self._string_iterator = ArmstrongPDFStringIterator(page.get_strings())
        self._office = None
        self._vote_data = None
        self._write_in_votes_outstanding = 0
        self._table_parser = None
        self._init_from_previous_parser(previous_page_parser)

    def __iter__(self):
        while not self._string_iterator.page_is_done():
            self._skip_precincts_reported()
            yield from self._process_next_table_header_or_body()

    def continued_office(self):
        return self._office

    def get_write_in_votes_outstanding(self):
        return self._write_in_votes_outstanding

    def get_vote_data_outstanding(self):
        return self._vote_data

    def finish(self):
        if self._vote_data is not None and self._office:
            yield from self._process_table()

    def _init_from_previous_parser(self, previous_page_parser):
        if previous_page_parser:
            self._office = previous_page_parser.continued_office()
            self._vote_data = previous_page_parser.get_vote_data_outstanding()
            self._write_in_votes_outstanding = previous_page_parser.get_write_in_votes_outstanding()

    def _process_next_table_header_or_body(self):
        if self._string_iterator.table_is_next():
            if self._string_iterator.new_candidate_table_available() and self._has_table_header_and_body():
                yield from self._process_table()
            self._process_table_body()
        else:
            if self._has_table_header_and_body():
                yield from self._process_table()
            self._office = self._get_office()

    def _has_table_header_and_body(self):
        return self._office and self._vote_data is not None

    def _skip_precincts_reported(self):
        if self._string_iterator.is_precincts_reported_next():
            # ['Precincts Reported: ', '1', ' of ', '1', ' ', '(100.00%)']
            [next(self._string_iterator) for _ in range(6)]

    def _skip_vote_for_x(self):
        while ')' not in next(self._string_iterator):
            pass
        while self._string_iterator.peek().strip() in ('', 'DEM', 'REP', 'Non'):
            next(self._string_iterator)

    def _process_table_body(self):
        self._table_parser = ArmstrongPDFTableParser(self._string_iterator, self._write_in_votes_outstanding)
        new_vote_data = list(self._table_parser)
        if not self._vote_data:
            self._vote_data = []
        self._vote_data += new_vote_data
        self._write_in_votes_outstanding = self._table_parser.get_unprocessed_write_in_votes()

    def _process_table(self):
        if 'DELEGATE' not in self._office and 'REFERENDUM' not in self._office:
            yield from self._process_vote_data()
        self._write_in_votes_outstanding = 0
        self._office = None
        self._vote_data = None

    def _process_vote_data(self):
        office, party, district = RAW_OFFICE_TO_OFFICE_PARTY_AND_DISTRICT[self._office]
        for row in self._vote_data:
            assert row['party'] in ('', party)
            row.update(office=office, party=party, district=district)
            yield row
        if self._write_in_votes_outstanding:
            yield {'office': office, 'party': party, 'district': district,
                   'candidate': 'Write-In', 'votes': self._write_in_votes_outstanding}

    def _get_office(self):
        office = ''
        while self._string_iterator.peek().strip():
            office += next(self._string_iterator)
        self._skip_vote_for_x()
        return office


def extract_first_page_data(page):
    string_iterator = PDFStringIterator(page.get_strings())
    ballots_cast = None
    registered_voters = None
    precinct = None
    for s in string_iterator:
        if s == 'Registered Voters: ':
            ballots_cast, _, registered_voters = [next(string_iterator) for _ in range(3)]
        if s.startswith('Summary for:'):
            _, precinct, _, _ = s.split(', ')
    return precinct, ballots_cast, registered_voters


def process_pdf(precinct_id):
    pdf_page_iterator = ArmstrongPDFPageIterator(precinct_id)
    page_one = next(pdf_page_iterator)
    precinct, ballots_cast, registered_voters = extract_first_page_data(page_one)
    yield {'county': COUNTY, 'precinct': precinct, 'office': 'Ballots Cast', 'votes': ballots_cast}
    yield {'county': COUNTY, 'precinct': precinct, 'office': 'Registered Voters', 'votes': registered_voters}
    parser_prev = None
    for page in pdf_page_iterator:
        print(f'processing page {page.get_page_number()} of precinct {precinct_id}')
        parser = ArmstrongPDFPageParser(page, parser_prev)
        for row in parser:
            row.update(county=COUNTY, precinct=precinct)
            yield row
        parser_prev = parser
    for row in parser_prev.finish():
        row.update(county=COUNTY, precinct=precinct)
        yield row


def pdfs_to_csv(csv_writer):
    csv_writer.writeheader()
    for precinct_id in range(FIRST_PRECINCT_ID, LAST_PRECINCT_ID + 1):
        for row in process_pdf(precinct_id):
            csv_writer.writerow(row)
        sleep(QUERY_SPACING_IN_SECONDS)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdfs_to_csv(csv.DictWriter(f, OUTPUT_HEADER))
