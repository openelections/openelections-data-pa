import csv
import os
from lxml import html

COUNTY = 'Butler'

BUTLER_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                           'Butler PA 2020 Primary.html')
OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__butler__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

PARTIES = {
    'DEM',
    'REP',
}
PARTY_ABBREVIATIONS = {
    'Total': '',
    'Democratic': 'DEM',
    'Republican': 'REP',
}

OFFICE_AND_DISTRICT_MAPPING = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 15TH CONGRESSIONAL DISTRICT': ('U.S. House', 15),
    'REPRESENTATIVE IN CONGRESS 16TH CONGRESSIONAL DISTRICT': ('U.S. House', 16),
    'REPRESENTATIVE IN CONGRESS 17TH CONGRESSIONAL DISTRICT': ('U.S. House', 17),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 10TH LEGISLATIVE DISTRICT': ('General Assembly', 10),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 11TH LEGISLATIVE DISTRICT': ('General Assembly', 11),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 12TH LEGISLATIVE DISTRICT': ('General Assembly', 12),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 14TH LEGISLATIVE DISTRICT': ('General Assembly', 14),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 60TH LEGISLATIVE DISTRICT': ('General Assembly', 60),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 64TH LEGISLATIVE DISTRICT': ('General Assembly', 64),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 8TH LEGISLATIVE DISTRICT': ('General Assembly', 8),
    'SENATOR IN THE GENERAL ASSEMBLY 21ST SENATORIAL DISTRICT': ('State Senate', 21),
    'SENATOR IN THE GENERAL ASSEMBLY 41ST SENATORIAL DISTRICT': ('State Senate', 41),
    'SENATOR IN THE GENERAL ASSEMBLY 47TH SENATORIAL DISTRICT': ('State Senate', 47),
}


PRECINCT_FIELD_OFFSET = 13
OFFICE_FIELD_OFFSET = 3
VOTE_FOR_OFFSET = 2
SUBTABLE_HEADER_WIDTH = 4
SUBTABLE_COLUMN_WIDTH = 5
NEW_LINE = '\n'


class ButlerHtmlTableStringProcessor:
    def __init__(self, strings):
        self._strings = strings
        self._strings_offset = 1

    def get_next_precinct(self):
        if len(self._strings) <= PRECINCT_FIELD_OFFSET + self._strings_offset:
            raise StopIteration
        self._strings_offset += PRECINCT_FIELD_OFFSET
        precinct = self._strings[self._strings_offset]
        self._strings_offset += 1
        return precinct

    def get_next_office(self):
        if len(self._strings) <= OFFICE_FIELD_OFFSET + self._strings_offset:
            raise StopIteration
        if self._strings[OFFICE_FIELD_OFFSET + self._strings_offset] == NEW_LINE:
            raise StopIteration
        self._strings_offset += OFFICE_FIELD_OFFSET
        office = self._strings[self._strings_offset]
        if office.startswith('Vote For'):
            self._strings_offset += VOTE_FOR_OFFSET
            office = self._strings[self._strings_offset]
        self._strings_offset += 1
        return office

    def skip_subtable_header(self):
        self._strings_offset += SUBTABLE_HEADER_WIDTH

    def get_next_candidate_and_votes(self):
        if self._strings[self._strings_offset] == NEW_LINE:
            raise StopIteration
        a, _, b = self._strings[self._strings_offset:self._strings_offset + 3]
        self._strings_offset += SUBTABLE_COLUMN_WIDTH
        return a, b


class ButlerHtmlTableParser:
    def __init__(self, string_processor):
        self._string_processor = string_processor

    def iterate_precincts(self):
        while True:
            precinct = self._string_processor.get_next_precinct()
            for row in self.iterate_offices():
                row.update(precinct=precinct)
                yield row

    def iterate_offices(self):
        while True:
            office = self._string_processor.get_next_office()
            party, office = self.extract_party(office)
            district = ''
            if office in OFFICE_AND_DISTRICT_MAPPING:
                office, district = OFFICE_AND_DISTRICT_MAPPING[office]
            is_statistics_table = office == 'STATISTICS'
            office = office.title()
            self._string_processor.skip_subtable_header()
            for row in self.iterate_candidates():
                if 'Committee' not in office and 'Delegate' not in office:
                    if is_statistics_table:
                        office, party = row['candidate'].split(' - ')
                        party = PARTY_ABBREVIATIONS[party]
                        row.update(candidate='')
                    row.update(office=office, party=party, district=district)
                    yield row

    def iterate_candidates(self):
        while True:
            # candidates and votes can be reversed, so votes column is determine by an int-cast test
            a, b = self._string_processor.get_next_candidate_and_votes()
            if '%' not in a + b:
                try:
                    candidate = a
                    votes = int(b.replace(',', ''))
                except ValueError:
                    candidate = b
                    votes = int(a.replace(',', ''))
                yield {'county': COUNTY, 'candidate': candidate.title(), 'votes': votes}

    def extract_party(self, office):
        for party in PARTIES:
            if office.startswith(party + ' '):
                return office.split(' ', 1)
        return '', office



def process_html_tables(html_tables):
    for table in html_tables:
        string_processor = ButlerHtmlTableStringProcessor(table.xpath(f'.//text()'))
        butler_parser = ButlerHtmlTableParser(string_processor)
        yield from butler_parser.iterate_precincts()


def html_tables_to_csv(html_tables, csv_writer):
    csv_writer.writeheader()
    for row in process_html_tables(html_tables):
        csv_writer.writerow(row)


if __name__ == "__main__":
    with open(BUTLER_FILE) as f_in:
        report_html_string = f_in.read()
        html_tree = html.fromstring(report_html_string)
        html_tables = html_tree.xpath(f'//table')
        with open(OUTPUT_FILE, 'w', newline='') as f_out:
            html_tables_to_csv(html_tables, csv.DictWriter(f_out, OUTPUT_HEADER))
