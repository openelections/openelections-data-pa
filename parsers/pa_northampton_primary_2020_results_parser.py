import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator, PDFStringIterator


# Uses Electionware Summary Precinct Results Report PDF format
COUNTY = 'Northampton'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__northampton__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'provisional', 'votes']

NORTHAMPTON_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                                'Northampton PA Primary Precinct Results.pdf')
NORTHAMPTON_HEADER = [
    '',
    'Summary Precinct Results Report',
    'Primary Election',
    'April 28, 2020',
    'OFFICIAL RESULTS',
    'NORTHAMPTON COUNTY, PENNSYLVANIA',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
    'Mail-in',
    'Provisional',
]
# column ordering is the same but the header text strings order can be different
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Provisional',
    'Absentee',
    'Mail-in',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

TABLE_HEADER_TO_DICT = {
    'TOTAL': 'votes',
    'Election Day': 'election_day',
    'Absentee': 'absentee',
    'Mail-in': 'provisional',
}

INSTRUCTION_ROW_SUBSTRING = 'Vote For'
FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/22/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT  = {
    'President of the United States': ('President', ''),
    'Representative in Congress': ('U.S. House', 7),
    'Representative in the General Assembly 131st Legislative District': ('General Assembly', 131),
    'Representative in the General Assembly 135th Legislative District': ('General Assembly', 135),
    'Representative in the General Assembly 136th Legislative District': ('General Assembly', 136),
    'Representative in the General Assembly 137th Legislative District': ('General Assembly', 137),
    'Representative in the General Assembly 138th Legislative District': ('General Assembly', 138),
    'Representative in the General Assembly 183rd Legislative District': ('General Assembly', 183),
}

PARTIES = {
    'DEM',
    'REP',
}
PARTY_ABBREVIATIONS = {
    'Total': '',
    'Blank': 'Blank',
    'DEMOCRATIC': 'DEM',
    'REPUBLICAN': 'REP',
    'NONPARTISAN': 'NPA',    
}


class NorthamptonPDFStringIterator(PDFStringIterator):
    def page_is_done(self):
        s = self.peek()
        return s.startswith(FIRST_FOOTER_SUBSTRING) or s.startswith(SECOND_FOOTER_SUBSTRING)

    def table_is_done(self):
        return self.peek().startswith(INSTRUCTION_ROW_SUBSTRING)

    def swap_any_bad_ballots_cast_fields(self):
        s = self._strings[self._strings_offset + 1]
        if s.startswith('Ballots Cast'):
            self._strings[self._strings_offset + 1] = self._strings[self._strings_offset]
            self._strings[self._strings_offset] = s


class NorthamptonPDFTableParser():
    def __init__(self, precinct, string_iterator):
        self._string_iterator = string_iterator
        self._precinct = precinct
        self._skip_instruction_row()
        self._parse_header()
        self._verify_table_header()

    def __iter__(self):
        while True:
            row = self._parse_row()
            if self._should_be_recorded(row):
                yield row

    def _parse_header(self):
        self._office = next(self._string_iterator)
        self._party = ''
        for party in PARTIES:
            if self._office.startswith(party):
                self._party, self._office = self._office.split(' ', 1)

    def _verify_table_header(self):
        actual_header = ''
        while len(actual_header) < len(EXPECTED_TABLE_HEADERS[0]):
            actual_header += next(self._string_iterator) + ' '
        assert(actual_header.strip() in EXPECTED_TABLE_HEADERS)

    def _parse_row(self):
        if self._string_iterator.page_is_done() or self._string_iterator.table_is_done():
            raise StopIteration
        self._string_iterator.swap_any_bad_ballots_cast_fields()
        candidate = next(self._string_iterator)
        row = {'county': COUNTY, 'precinct': self._precinct,'office': self._office, 'party': self._party,
               'district': '', 'candidate': candidate.strip()}
        self._clean_row(row)
        self._populate_votes(row)
        return row

    def _populate_votes(self, row):
        for header in TABLE_HEADER[:-1]:
            votes_string = next(self._string_iterator)
            if '%' not in votes_string:
                row[TABLE_HEADER_TO_DICT[header]] = int(votes_string.replace(',', ''))
            if row['office'] in ('Registered Voters', 'Voter Turnout'):
                # only one column for each of these
                break

    def _skip_instruction_row(self):
        if self._string_iterator.peek().startswith(INSTRUCTION_ROW_SUBSTRING):
            next(self._string_iterator)

    @staticmethod
    def _clean_row(row):
        if row['office'] == 'STATISTICS':
            row['office'], party = row['candidate'].split(' - ', 1)
            row['party'] = PARTY_ABBREVIATIONS[party]
            row['candidate'] = ''
        if row['office'] in RAW_OFFICE_TO_OFFICE_AND_DISTRICT:
            row['office'], row['district'] = RAW_OFFICE_TO_OFFICE_AND_DISTRICT[row['office']]
        if row['candidate'] == 'Write-In Totals':
            row['candidate'] = 'Write-in'

    @staticmethod
    def _should_be_recorded(row):
        if row['candidate'] == 'Not Assigned':
            return False
        if 'Delegate' in row['office'] or 'County Committee' in row['office']:
            return False
        if row['office'] in ('Voter Turnout', 'Library Tax Question'):
            return False
        if row['party'] == 'Blank':
            return False
        return True


class NorthamptonPDFPageParser:
    def __init__(self, page):
        self._string_iterator = NorthamptonPDFStringIterator(page.get_strings())
        self._verify_header()
        self._init_precinct()

    def __iter__(self):
        while not self._string_iterator.page_is_done():
            table_parser = NorthamptonPDFTableParser(self._precinct, self._string_iterator)
            yield from iter(table_parser)

    def _verify_header(self):
        header = [next(self._string_iterator) for _ in range(len(NORTHAMPTON_HEADER))]
        assert (header == NORTHAMPTON_HEADER)

    def _init_precinct(self):
        self._precinct = next(self._string_iterator)


def pdf_to_csv(pdf, csv_writer):
    csv_writer.writeheader()
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = NorthamptonPDFPageParser(page)
        for row in pdf_page_parser:
            csv_writer.writerow(row)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(NORTHAMPTON_FILE), csv.DictWriter(f, OUTPUT_HEADER))
