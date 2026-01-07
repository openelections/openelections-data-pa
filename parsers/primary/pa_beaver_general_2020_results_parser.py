import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Beaver'

OUTPUT_FILE = os.path.join('2020', '20201103__pa__general__beaver__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

BEAVER_FILE = os.path.join('..', 'openelections-sources-pa', '2020', 'general',
                           'Beaver PA PrecinctsResults11022020.pdf')

BEAVER_HEADER = ['',
    'Summary Results Report',
    '2020 General Election',
    'November 3, 2020',
    'OFFICIAL RESULTS'
]

TABLE_HEADER = [
    'STATISTICS',
    'TOTAL',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 11/16/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENTIAL ELECTORS': ('President', ''),
    'ATTORNEY GENERAL': ('Attorney General',''),
    'AUDITOR GENERAL': ('Auditor General',''),
    'STATE TREASURER': ('State Treasurer', ''),
    'REP IN THE GENERAL ASSEMBLY 10TH DIST': ('General Assembly', 10),
    'REP IN THE GENERAL ASSEMBLY 10TH DISTRICT': ('General Assembly', 10),
    'REP IN THE GENERAL ASSEMBLY 14TH DISTRICT': ('General Assembly', 14),
    'REP IN THE GENERAL ASSEMBLY 15TH DISTRICT': ('General Assembly', 15),
    'REP IN THE GENERAL ASSEMBLY 16TH DISTRICT': ('General Assembly', 16),
    'REP IN THE GENERAL ASSEMBLY 17TH DISTRICT': ('General Assembly', 17),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 17),
    'SENATOR IN THE GENERAL ASSEMBLY 47TH DIST': ('State Senate', 47),
    'SEN IN THE GENERAL ASSEMBLY 47TH DIST': ('State Senate', 47),
}


class BeaverPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class BeaverPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if 'Del ' in row['office']:
            return False
        if 'Comm' in row['office']:
            return False
        return super()._should_be_recorded(row)


class BeaverPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = BeaverPDFStringIterator
    _pdf_table_parser_clazz = BeaverPDFTableParser
    _header = BEAVER_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BEAVER_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   BeaverPDFPageParser)
