import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Schuylkill'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__schuylkill__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'absentee', 'provisional', 'votes']

SCHUYLKILL_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                               'Schuylkill PA Official June 2, 2020 Precinct Report.pdf')
SCHUYLKILL_HEADER = [
    '',
    'Summary Results Report',
    'Schuylkill Primary 2020',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Schuylkill County',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Mail-in',
    'Absentee',
    'Provisional',
]
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Provisional',
    'Absentee',
    'Mail-in',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'mail_in',
    'absentee',
    'provisional',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/19/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'CONGRESS 8TH DISTRICT': ('U.S. House', 9),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 123rd': ('General Assembly', 123),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 124th': ('General Assembly', 124),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 125th': ('General Assembly', 125),
    'SENATOR IN THE GENERAL ASSEMBLY': ('State Senate', 29),
}

BUGGY_ROWS = [
    {'county': 'Schuylkill', 'precinct': 'Mt. Carbon', 'office': 'State Senate', 'party': 'DEM', 'district': 29,
     'candidate': 'Write-in', 'votes': 0, 'election_day': 0, 'mail_in': 0, 'absentee': 0, 'provisional': 0},
    {'county': 'Schuylkill', 'precinct': 'Mt. Carbon', 'office': 'State Senate', 'party': 'DEM', 'district': 29,
     'candidate': 'Total Votes Cast', 'votes': 0, 'election_day': 0, 'mail_in': 0, 'absentee': 0, 'provisional': 0}
]


class SchuylkillPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class SchuylkillPDFTableParser(ElectionwarePDFTableParser):
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
        if self._office != 'STATISTICS' and row not in BUGGY_ROWS:
            vote_percent_string = next(self._string_iterator)
            assert '%' in vote_percent_string

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if not super()._should_be_recorded(row):
            return False
        if row['office'] == 'Borough Of Mahanoy City Mahanoy City':
            return False
        return True


class SchuylkillPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = SchuylkillPDFStringIterator
    _pdf_table_parser_clazz = SchuylkillPDFTableParser
    _header = SCHUYLKILL_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(SCHUYLKILL_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   SchuylkillPDFPageParser)
