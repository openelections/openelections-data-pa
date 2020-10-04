import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator, PDFStringIterator
from parsers.constants.pa_candidates_2020 import STATEWIDE_PRIMARY_CANDIDATES


COUNTY = 'Lycoming'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__lycoming__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

# To create a v2 file, open the existing file in Acrobat and save as the below filename.
# This action is needed because the provided file is version 1.4, but the parser only
# supports 1.5 and greater
LYCOMING_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                             'Lycoming PA 2020 General Primary SOVC v2.pdf')

LYCOMING_HEADER = [
    'Official Results - Statement of Votes Cast',
    'June 2, 2020 General Primary',
    'Lycoming County',
    'All Precincts, All Districts, All ScanStations, All Contests, All Boxes',
    'Total Ballots Cast: 24694',
    '2020-06-11',
    '16:54:35',
]

VALID_HEADERS = set([x.title() for x in STATEWIDE_PRIMARY_CANDIDATES]) | {
    'Lee Griffin',
    'Jackie Baker',
    'Airneezer J. Page',
    'Amanda R. Waldman',
    'Timothy DeFoor',
    'Fred Keller',
    'Gene Yaw',
    'Jeff Wheeland',
    'Dave Hines',
    'Joe Hamm',
    'Mike Dincher',
}

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'Congress 12th': ('U.S. House', 12),
    'PA Rep 83rd': ('General Assembly', 83),
    'PA Rep 84th': ('General Assembly', 84),
    'PA Senator 23rd': ('State Senate', 23),
}


class LycomingPDFPageParser:
    def __init__(self, page):
        strings = page.get_strings()
        self._string_iterator = PDFStringIterator(strings)
        self._validate_header()
        self._process_table_header()

    def __iter__(self):
        while not self.page_is_done():
            _, precinct = next(self._string_iterator).split(' ', 1)
            next(self._string_iterator)  # skip totals column
            for candidate in self._candidates:
                votes = int(next(self._string_iterator))
                yield {'county': COUNTY, 'office': self._office,
                       'district': self._district, 'party': self._party,
                       'precinct': precinct, 'candidate': candidate, 'votes': votes}

    def page_is_done(self):
        next_string = self._string_iterator.peek()
        return next_string.startswith('Page') or next_string.startswith('Total')

    def skip_page(self):
        return self._skipped_page

    def _validate_header(self,):
        header = [next(self._string_iterator) for _ in range(len(LYCOMING_HEADER))]
        assert(header == LYCOMING_HEADER)

    def _process_table_header(self):
        self._extract_office()
        self._skipped_page = 'Delegate' in self._office
        if not self._skipped_page:
            self._candidates = list(self._extract_candidates())

    def _extract_office(self):
        self._office, party, _ = next(self._string_iterator).split(' (')
        self._party = party[:-1].upper()
        self._district = ''
        if self._office in RAW_OFFICE_TO_OFFICE_AND_DISTRICT:
            self._office, self._district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT[self._office]
        assert all([next(self._string_iterator) == x for x in ('Precinct', 'Total', 'Votes')])

    def _extract_candidates(self):
        candidate = None
        while candidate != 'Write-in':
            candidate = self._process_candidate(candidate)
            if candidate in VALID_HEADERS:
                yield candidate
                candidate = None
            if candidate == 'Write-in':
                yield candidate

    def _process_candidate(self, candidate):
        s = next(self._string_iterator)
        if not candidate:
            return s
        return candidate + ' ' + s


def pdf_to_csv(pdf, csv_writer):
    csv_writer.writeheader()
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = LycomingPDFPageParser(page)
        if not pdf_page_parser.skip_page():
            for row in pdf_page_parser:
                csv_writer.writerow(row)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(LYCOMING_FILE),
                   csv.DictWriter(f, OUTPUT_HEADER))
