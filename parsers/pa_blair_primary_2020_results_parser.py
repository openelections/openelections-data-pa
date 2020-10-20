import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Blair'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__blair__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

BLAIR_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                           'Blair PA June 2 Elections Results.pdf')

BLAIR_HEADER = [
    '',
    'Summary Results Report',
    'PRIMARY ELECTION',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'BLAIR COUNTY, PENNSYLVANIA',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER),)

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/11/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 79TH DISTRICT': ('General Assembly', 79),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 80TH DISTRICT': ('General Assembly', 80),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 13),
}


class BlairPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class BlairPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    def _verify_table_header(self):
        if self._office != 'STATISTICS':
            vote_percent_header = next(self._string_iterator)
            assert vote_percent_header == 'VOTE %'
        super()._verify_table_header()

    def _populate_votes(self, row):
        super()._populate_votes(row)
        for _ in range(3):
            # skip vote %, absentee, and election day;
            # these can be out of order, and not valid
            s = self._string_iterator.peek()
            if '%' in s or s.isnumeric():
                next(self._string_iterator)

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if row['candidate'].startswith('Write-In: '):
            # there's already a Write-In Totals field; this prevents double counting
            return False
        if 'Delegate' in row['office']:
            return False
        if 'Comm' in row['office']:
            return False
        return super()._should_be_recorded(row)


class BlairPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = BlairPDFStringIterator
    _pdf_table_parser_clazz = BlairPDFTableParser
    _header = BLAIR_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BLAIR_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   BlairPDFPageParser)
