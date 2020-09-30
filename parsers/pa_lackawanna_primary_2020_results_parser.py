import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Lackawanna'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__lackawanna__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'absentee', 'provisional', 'votes']

LACKAWANNA_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                               'Lackawanna PA 20PrimaryPrecinctCertified.pdf')
LACKAWANNA_HEADER = [
    '',
    'Precinct Results Report',
    'PRIMARY ELECTION',
    'June 2, 2020',
    'CERTIFIED RESULTS',
    'Lackawanna',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Mail-In',
    'Absentee',
    'Provisional',
]
# column ordering is the same but the header text strings order can be different
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Provisional',
    'Absentee',
    'Mail-In',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'mail_in',
    'absentee',
    'provisional',
]

FIRST_FOOTER_SUBSTRING = 'precinct_lackawanna - 06/17/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'CONGRESS 8TH DISTRICT': ('U.S. House', 8),
    'REPRESENTATIVE 112TH': ('General Assembly', 112),
    'REPRESENTATIVE 113TH': ('General Assembly', 113),
    'REPRESENTATIVE 114TH': ('General Assembly', 114),
    'REPRESENTATIVE 117TH': ('General Assembly', 117),
    'REPRESENTATIVE 118TH': ('General Assembly', 118),
}


class LackawannaPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class LackawannaPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()


class LackawannaPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = LackawannaPDFStringIterator
    _pdf_table_parser_clazz = LackawannaPDFTableParser
    _header = LACKAWANNA_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(LACKAWANNA_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   LackawannaPDFPageParser)
