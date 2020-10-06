import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import pdf_to_csv, ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Clinton'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__clinton__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

CLINTON_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Clinton PA Primary 2020.pdf')
CLINTON_HEADER = [
    '',
    'Summary Results Report',
    'OFFICIAL GENERAL PRIMARY ELECTION',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Clinton County',
]

TABLE_HEADER = [
    'TOTAL',
    'Election Day',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER),)

FIRST_FOOTER_SUBSTRING = 'Precinct Summary - 06/19/2020'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REP IN CONGRESS 12TH DISTRICT': ('U.S. House', 12),
    'SEN GEN ASSEMBLY 25TH DISTRICT': ('State Senate', 25),
    'REP IN THE GEN ASSEMBLY 76TH DISTRICT': ('General Assembly', 76),
}

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day'
]


class ClintonPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class ClintonPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    def _populate_votes(self, row):
        super()._populate_votes(row)
        if 'election_day' in row:
            # skip election day votes, since they match the total
            del row['election_day']

    @classmethod
    def _clean_row(cls, row):
        row['candidate'] = row['candidate'].replace('REPUBLICIAN', 'REPUBLICAN')
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()

    @classmethod
    def _should_be_recorded(cls, row):
        if row['candidate'].startswith('Write-In: '):
            # there's already a Write-In Totals field; this prevents double counting
            return False
        if 'Del ' in row['office']:
            return False
        if 'Cmte' in row['office']:
            return False
        return super()._should_be_recorded(row)


class ClintonPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = ClintonPDFStringIterator
    _pdf_table_parser_clazz = ClintonPDFTableParser
    _header = CLINTON_HEADER


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(CLINTON_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER),
                   ClintonPDFPageParser)
