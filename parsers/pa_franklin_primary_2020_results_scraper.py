import csv
import os
import requests
from io import BytesIO
from pdfreader import SimplePDFViewer
from time import sleep
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Franklin'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__franklin__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

FRANKLIN_URL = 'https://portal.co.franklin.pa.us/Election%20Results/{}.pdf'
FIRST_PRECINCT_ID = 1
LAST_PRECINCT_ID = 73
QUERY_SPACING_IN_SECONDS = 3

FRANKLIN_HEADER = [
    '',
    'Summary Results Report',
    'Primary Election',
    'June 2, 2020',
    'OFFICIAL RESULTS',
    'Electionware County',
]

TABLE_HEADER = 'TOTAL'
EXPECTED_TABLE_HEADERS = (TABLE_HEADER,)

OPENELECTIONS_MAPPED_HEADER = [
    'votes'
]

SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'CONGRESSIONAL REP 13TH DST': ('U.S. House', 13),
    'LEGISLATIVE REP 78TH DST': ('General Assembly', 78),
    'LEGISLATIVE REP 82ND DST': ('General Assembly', 82),
    'LEGISLATIVE REP 89TH DST': ('General Assembly', 89),
    'LEGISLATIVE REP 90TH DST': ('General Assembly', 90),
    'SENATOR 33RD DST': ('State Senate', 33),
}

INVALID_CANDIDATES = ('Contest Totals', 'Overvotes', 'Undervotes')
INVALID_ROWS = {'precinct': 'Orrstown Borough', 'office': 'General Assembly',
                'district': 89, 'party': 'DEM'}


class FranklinPDFStringIterator(ElectionwarePDFStringIterator):
    def __init__(self, strings):
        super().__init__(strings)
        self._custom_first_footer_substring = None

    def set_first_footer_substring(self, custom_first_footer_substring):
        self._custom_first_footer_substring = custom_first_footer_substring

    def page_is_done(self):
        s = self.peek()
        return s.startswith(self._custom_first_footer_substring) or s.startswith(SECOND_FOOTER_SUBSTRING)


class FranklinPDFTableParser(ElectionwarePDFTableParser):
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
        if self._office != 'STATISTICS' and row['candidate'] not in INVALID_CANDIDATES:
            if all(row[k] == INVALID_ROWS[k] for k in INVALID_ROWS):
                # no votes in this precinct for this office
                return
            vote_percent_string = next(self._string_iterator)
            assert '%' in vote_percent_string

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
        if row['candidate'] in INVALID_CANDIDATES:
            return False
        if row['office'].startswith('Liquor License'):
            return False
        return super()._should_be_recorded(row)


class FranklinPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = FranklinPDFStringIterator
    _pdf_table_parser_clazz = FranklinPDFTableParser
    _header = FRANKLIN_HEADER

    def __init__(self, page, precinct_id_string):
        super().__init__(page)
        self._string_iterator.set_first_footer_substring(precinct_id_string + ' - ')


class FranklinPDFPageIterator(PDFPageIterator):
    def __init__(self, precinct_id_string):
        super().__init__(filename=None)
        response = requests.get(FRANKLIN_URL.format(precinct_id_string))
        self._pdf_viewer = SimplePDFViewer(BytesIO(response.content))


def process_pdf(precinct_id_string):
    pdf_page_iterator = FranklinPDFPageIterator(precinct_id_string)
    for page in pdf_page_iterator:
        print(f'processing page {page.get_page_number()} of precinct {precinct_id_string}')
        yield from FranklinPDFPageParser(page, precinct_id_string)


def pdfs_to_csv(csv_writer):
    csv_writer.writeheader()
    for precinct_id in range(FIRST_PRECINCT_ID, LAST_PRECINCT_ID + 1):
        precinct_id_string = f'{precinct_id:02}'
        for row in process_pdf(precinct_id_string):
            csv_writer.writerow(row)
        sleep(QUERY_SPACING_IN_SECONDS)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdfs_to_csv(csv.DictWriter(f, OUTPUT_HEADER))
