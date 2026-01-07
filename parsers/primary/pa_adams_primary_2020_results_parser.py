import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Adams'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__adams__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'votes']

ADAMS_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                          'Adams PA 2020 Primary PrecinctSummary.pdf')
ADAMS_HEADER = [
    '',
    'Summary Results Report',
    'PRIMARY ELECTION',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'ADAMS COUNTY, PENNSYLVANIA',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
    'Absentee',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER),)

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'absentee',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/19/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'President of the United States': ('President', ''),
    'Rep in Congress - 13th Dist': ('U.S. House', 13),
    'Senator in the General Assembly': ('State Senate', 33),
    'Rep in Gen Assembly - 91st Dist': ('General Assembly', 91),
    'Rep in Gen Assembly - 193rd Dist': ('General Assembly', 193),
}


class AdamsPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class AdamsPDFTableParser(ElectionwarePDFTableParser):
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


class AdamsPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = AdamsPDFStringIterator
    _pdf_table_parser_clazz = AdamsPDFTableParser
    _header = ADAMS_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(ADAMS_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   AdamsPDFPageParser)
