import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Mifflin'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__mifflin__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'absentee', 'votes']

MIFFLIN_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Mifflin PA 2020 CERTIFIED PRIMARY ELECTION PRECINCT RESULTS.pdf')
MIFFLIN_HEADER = [
    '',
    'Summary Results Report',
    'GENERAL PRIMARY ELECTION',
    'June 2, 2020',
    'CERTIFIED RESULTS',
    'MIFFLIN COUNTY, PENNSYLVANIA',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
    'Mail-In',
    'Absentee',
    'Mail-In',
]
# column ordering is the same but the header text strings order can be different
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'Election Day',
    'Absentee',
    'Mail-In',
    'Mail-In',
    'Absentee',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    None,
    'mail_in',
    'absentee',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/19/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 81ST DISTRICT': ('General Assembly', 81),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 82ND DISTRICT': ('General Assembly', 82),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 171ST DISTRICT': ('General Assembly', 171),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 12),
}


class MifflinPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class MifflinPDFTableParser(ElectionwarePDFTableParser):
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
        if row['candidate'].startswith('Write-In: '):
            # there's already a Write-In Totals field; this prevents double counting
            return False
        if 'Delegate' in row['office']:
            return False
        if 'Comm' in row['office']:
            return False
        return super()._should_be_recorded(row)

class MifflinPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = MifflinPDFStringIterator
    _pdf_table_parser_clazz = MifflinPDFTableParser
    _header = MIFFLIN_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(MIFFLIN_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   MifflinPDFPageParser)
