import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Tioga'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__tioga__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

TIOGA_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                          'Tioga PA County June 2020 Official.pdf')
TIOGA_HEADER = [
    '',
    'Summary Results Report',
    'GENERAL PRIMARY ELECTION, JUNE 2, 2020',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'TIOGA COUNTY',
]

TABLE_HEADER = 'TOTAL'
EXPECTED_TABLE_HEADERS = (TABLE_HEADER,)
FIRST_PER_PRECINCT_PAGE = 10

FIRST_FOOTER_SUBSTRING = 'Election Summary - 06/10/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 12TH DISTRICT': ('U.S. House', 12),
    'SENATOR IN THE GENERAL ASSEMBLY 25TH DISTRICT': ('State Senate', 25),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 68TH DISTRICT': ('General Assembly', 68),
}

OPENELECTIONS_MAPPED_HEADER = [
    'votes'
]


class TiogaPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class TiogaPDFTableParser(ElectionwarePDFTableParser):
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
        if row['precinct'] in ('Elk Township', 'Shippen Township'):
            if row['district'] == 68 and row['party'] == 'DEM':
                # no vote % for these pages, because no voters
                return
        if self._office != 'STATISTICS' and row['candidate'] != 'Contest Totals':
            vote_percent_string = next(self._string_iterator)
            assert '%' in vote_percent_string

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if row['candidate'].startswith('Write-In: '):
            # there's already a Write-In Totals field; this prevents double counting
            return False
        return super()._should_be_recorded(row)


class TiogaPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = TiogaPDFStringIterator
    _pdf_table_parser_clazz = TiogaPDFTableParser
    _header = TIOGA_HEADER

    def __init__(self, page):
        super().__init__(page)
        if page.get_page_number() < FIRST_PER_PRECINCT_PAGE:
            # skip these pages; these are the summary pages
            strings = [FIRST_FOOTER_SUBSTRING]
            self._string_iterator = TiogaPDFStringIterator(strings)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(TIOGA_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   TiogaPDFPageParser)
