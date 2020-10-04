import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Lebanon'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__lebanon__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

LEBANON_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Lebanon PA 2020 Primary Election Precinct Results.pdf')
LEBANON_HEADER = [
    '',
    'Summary Results Report',
    '2020 Primary General Election',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Lebanon County',
]

TABLE_HEADER = 'TOTAL'
EXPECTED_TABLE_HEADERS = (TABLE_HEADER,)

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/19/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTIVE IN CONGRESS 9TH DISTRICT': ('U.S. House', 9),
    'REPRESENTATIVE GENERAL ASSEMBLY 101st State Legislative': ('General Assembly', 101),
    'REPRESENTATIVE GENERAL ASSEMBLY 102nd State Legislative': ('General Assembly', 102),
    'REPRESENTATIVE GENERAL ASSEMBLY 104th State Legislative': ('General Assembly', 104),
}

OPENELECTIONS_MAPPED_HEADER = [
    'votes'
]


class LebanonPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class LebanonPDFTableParser(ElectionwarePDFTableParser):
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
        if self._office != 'STATISTICS':
            vote_percent_string = next(self._string_iterator)
            assert '%' in vote_percent_string

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if not super()._should_be_recorded(row):
            return False
        if 'Committee' in row['office']:
            return False
        return True


class LebanonPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = LebanonPDFStringIterator
    _pdf_table_parser_clazz = LebanonPDFTableParser
    _header = LEBANON_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(LEBANON_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   LebanonPDFPageParser)
