import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Northampton'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__northampton__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'provisional', 'votes']

NORTHAMPTON_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                                'Northampton PA Primary Precinct Results.pdf')
NORTHAMPTON_HEADER = [
    '',
    'Summary Precinct Results Report',
    'Primary Election',
    'April 28, 2020',
    'OFFICIAL RESULTS',
    'NORTHAMPTON COUNTY, PENNSYLVANIA',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
    'Mail-in',
    'Provisional',
]
# column ordering is the same but the header text strings order can be different
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
    'absentee',
    'provisional',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/22/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'President of the United States': ('President', ''),
    'Representative in Congress': ('U.S. House', 7),
    'Representative in the General Assembly 131st Legislative District': ('General Assembly', 131),
    'Representative in the General Assembly 135th Legislative District': ('General Assembly', 135),
    'Representative in the General Assembly 136th Legislative District': ('General Assembly', 136),
    'Representative in the General Assembly 137th Legislative District': ('General Assembly', 137),
    'Representative in the General Assembly 138th Legislative District': ('General Assembly', 138),
    'Representative in the General Assembly 183rd Legislative District': ('General Assembly', 183),
}


class NorthamptonPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class NorthamptonPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _should_be_recorded(cls, row):
        if not super()._should_be_recorded(row):
            return False
        if 'County Committee' in row['office']:
            return False
        if row['office'] == 'Library Tax Question':
            return False
        return True


class NorthamptonPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = NorthamptonPDFStringIterator
    _pdf_table_parser_clazz = NorthamptonPDFTableParser
    _header = NORTHAMPTON_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(NORTHAMPTON_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   NorthamptonPDFPageParser)
