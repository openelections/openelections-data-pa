import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Centre'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__centre__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

CENTRE_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                           'Centre PA 2020 Primary.pdf')

CENTRE_HEADER = [
    '',
    'Precinct Summary Results Report',
    'June 2, 2020',
    'General Primary',
    'OFFICIAL RESULTS',
    'Centre County',
]

TABLE_HEADER = [
    'TOTAL',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER),)

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/11/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 76TH LEGISLATIVE': ('General Assembly', 76),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 77TH LEGISLATIVE': ('General Assembly', 77),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 81ST LEGISLATIVE': ('General Assembly', 81),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 171ST LEGISLATIVE': ('General Assembly', 171),
    'REPRESENTATIVE IN CONGRESS 12TH CONGRESSIONAL': ('U.S. House', 12),
    'REPRESENTATIVE IN CONGRESS 15TH CONGRESSIONAL': ('U.S. House', 15),
}


class CentrePDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class CentrePDFTableParser(ElectionwarePDFTableParser):
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
        if '%' in self._string_iterator.peek():
            next(self._string_iterator)  # vote % string, not always supplied

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if 'Delegate' in row['office']:
            return False
        return super()._should_be_recorded(row)


class CentrePDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = CentrePDFStringIterator
    _pdf_table_parser_clazz = CentrePDFTableParser
    _header = CENTRE_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(CENTRE_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   CentrePDFPageParser)
