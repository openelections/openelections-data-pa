import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Chester'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__chester__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'votes']

CHESTER_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Chester PA Primary2020_OfficialResults_PrecinctSummary_202007021412091638.pdf')

CHESTER_HEADER = [
    '',
    'SUMMARY RESULTS',
    'PRESIDENTIAL PRIMARY ELECTION',
    'JUNE 2, 2020',
    'OFFICIAL RESULTS',
    'CHESTER COUNTY',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee/M',
    'ail-in',
]
# column ordering is the same but the header text strings order can be different
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Absentee/',
    'Mail-in',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'absentee',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 07/02/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 13TH DISTRICT': ('General Assembly', 13),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 26TH DISTRICT': ('General Assembly', 26),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 74TH DISTRICT': ('General Assembly', 74),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 155TH DISTRICT': ('General Assembly', 155),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 156TH DISTRICT': ('General Assembly', 156),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 157TH DISTRICT': ('General Assembly', 157),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 158TH DISTRICT': ('General Assembly', 158),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 160TH DISTRICT': ('General Assembly', 160),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 167TH DISTRICT': ('General Assembly', 167),
    'REPRESENTATIVE IN CONGRESS 6TH DISTRICT': ('U.S. House', 6),
    'SENATOR IN THE GENERAL ASSEMBLY 9TH DISTRICT': ('State Senate', 9),
    'SENATOR IN THE GENERAL ASSEMBLY 19TH DISTRICT': ('State Senate', 19),
}


class ChesterPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class ChesterPDFTableParser(ElectionwarePDFTableParser):
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
            next(self._string_iterator)  # vote % string

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if 'Delegate' in row['office']:
            return False
        if 'Committee' in row['office']:
            return False
        if 'Liquor' in row['office']:
            return False
        if 'Council' in row['office']:
            return False
        return super()._should_be_recorded(row)


class ChesterPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = ChesterPDFStringIterator
    _pdf_table_parser_clazz = ChesterPDFTableParser
    _header = CHESTER_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(CHESTER_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   ChesterPDFPageParser)
