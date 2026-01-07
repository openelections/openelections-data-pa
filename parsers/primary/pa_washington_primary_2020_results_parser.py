import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Washington'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__washington__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'provisional', 'military', 'votes']

WASHINGTON_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                               'Washington PA 2020 Primary Precinct Summary.pdf')
WASHINGTON_HEADER = [
    '',
    'Summary Results Report',
    '2020 Presidential Primary',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Washington',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
    'Provisional',
    'Military',
]
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Military',
    'Provisional',
    'Absentee',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'absentee',
    'provisional',
    'military',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/23/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 14),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 15TH DISTRICT': ('General Assembly', 15),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 39TH DISTRICT': ('General Assembly', 39),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 40TH DISTRICT': ('General Assembly', 40),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 46TH DISTRICT': ('General Assembly', 46),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 48TH DISTRICT': ('General Assembly', 48),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 49TH DISTRICT': ('General Assembly', 49),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 50TH DISTRICT': ('General Assembly', 50),
    'SENATOR IN THE GENERAL ASSEMBLY 37TH DISTRICT': ('State Senate', 37),
}


class WashingtonPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class WashingtonPDFTableParser(ElectionwarePDFTableParser):
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
        if self._office != 'STATISTICS' and row['candidate'] != 'Contest Totals':
            vote_percent_string = next(self._string_iterator)
            assert '%' in vote_percent_string

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
        return super()._should_be_recorded(row)


class WashingtonPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = WashingtonPDFStringIterator
    _pdf_table_parser_clazz = WashingtonPDFTableParser
    _header = WASHINGTON_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(WASHINGTON_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   WashingtonPDFPageParser)
