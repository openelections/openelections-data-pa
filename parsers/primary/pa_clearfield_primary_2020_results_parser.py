import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Clearfield'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__clearfield__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

CLEARFIELD_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                               'Clearfield PA June 2 Election Results.pdf')

CLEARFIELD_HEADER = [
    '',
    'Precinct Summary Results Report',
    'OFFICIAL GENERAL PRIMARY BALLOT',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Clearfield County',
]

TABLE_HEADER = [
    'TOTAL',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/11/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY HOUSE 73RD': ('General Assembly', 73),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY HOUSE 75TH': ('General Assembly', 75),
    'REPRESENTATIVE IN CONGRESS': ('U.S. House', 15),
    'SENATOR IN THE GENERAL ASSEMBLY 25TH DISTRICT': ('State Senate', 25),
    'SENATOR IN THE GENERAL ASSEMBLY 35TH DISTRICT': ('State Senate', 35),
}


class ClearfieldPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class ClearfieldPDFTableParser(ElectionwarePDFTableParser):
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


class ClearfieldPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = ClearfieldPDFStringIterator
    _pdf_table_parser_clazz = ClearfieldPDFTableParser
    _header = CLEARFIELD_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(CLEARFIELD_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   ClearfieldPDFPageParser)
