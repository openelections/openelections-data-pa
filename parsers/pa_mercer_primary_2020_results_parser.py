import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Mercer'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__mercer__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'votes']

MERCER_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                           'Mercer PA 2020 Primary PRECINCT.pdf')
MERCER_HEADER = [
    '',
    'Precinct Summary Results Report',
    'PRIMARY ELECTION',
    'JUNE 2, 2020',
    'OFFICIAL RESULTS',
    'Mercer County',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Mail /',
    'Provisional',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER),)

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'mail_in',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/15/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'U.S. REP, 16TH DISTRICT': ('U.S. House', 16),
    'STATE REP., 7TH DISTRICT': ('General Assembly', 7),
    'STATE REP., 8TH DISTRICT': ('General Assembly', 8),
    'STATE REP., 17TH DISTRICT': ('General Assembly', 17),
}


class MercerPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class MercerPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].replace('Write-In: ', '').title()

    @classmethod
    def _should_be_recorded(cls, row):
        if not super()._should_be_recorded(row):
            return False
        if row['office'] == 'Wheatland Home Rule':
            return False
        return True


class MercerPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = MercerPDFStringIterator
    _pdf_table_parser_clazz = MercerPDFTableParser
    _header = MERCER_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(MERCER_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   MercerPDFPageParser)
