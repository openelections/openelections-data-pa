import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Cambria'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__cambria__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'provisional', 'votes']

CAMBRIA_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Cambria PA General+Primary+Precinct+2020.pdf')
CAMBRIA_HEADER = [
    '',
    'Summary Results Report',
    'GENERAL PRIMARY BALLOT',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'CAMBRIA COUNTY',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee/M',
    'ail-In',
    'Provisional',
]
# column ordering is the same but the header text strings order can be different
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Provisional',
    'Absentee/',
    'Mail-In',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'mail_in',
    'provisional',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/22/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 13TH DISTRICT': ('U.S. House', 13),
    'REPRESENTATIVE IN CONGRESS 15TH DISTRICT': ('U.S. House', 15),
    'SENATOR IN THE GENERAL ASSEMBLY 35TH DIST': ('State Senator', 35),
    'GENERAL ASSEMBLY 71ST DISTRICT': ('General Assembly', 71),
    'GENERAL ASSEMBLY 72ND DISTRICT': ('General Assembly', 72),
    'GENERAL ASSEMBLY 73RD DISTRICT': ('General Assembly', 73),
}


class CambriaPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class CambriaPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()


class CambriaPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = CambriaPDFStringIterator
    _pdf_table_parser_clazz = CambriaPDFTableParser
    _header = CAMBRIA_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(CAMBRIA_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   CambriaPDFPageParser)
