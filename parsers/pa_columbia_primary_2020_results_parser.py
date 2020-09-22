from collections import defaultdict, namedtuple
import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator, PDFPageParser,\
    TableBodyParser, TableHeaderParser, TableHeader, pdf_to_csv


ParsedRow = namedtuple('ParsedRow', 'county precinct office district party candidate votes')

COUNTY = 'Columbia'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__columbia__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

COLUMBIA_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                             'Columbia PA 2020GeneralPrimaryOfficialResults.pdf')
COLUMBIA_HEADER = [
    '',
    'Statement of Votes Cast',
    ' COLUMBIA COUNTY, PA',
    'GENERAL PRIMARY ELECTION',
    'JUNE 2, 2020',
    'Results',
    'Date: 6/17/2020',
    'Time: 9:49:17 AM PDT'
]
LAST_VALID_PAGE_NUMBER = 36

PARTY_MAP = {
    '(REPUBLICAN)': 'REP',
    '(DEMOCRATIC)': 'DEM'
}

TERMINAL_HEADER_STRING = 'Jurisdiction Wide'
FIRST_SUBHEADER_STRING = 'Reg.'
TERMINAL_SUBHEADER_STRINGS = ('Write-in', 'Blank')
WRITE_IN_SUBSTRING = '(W)'
CONGRESSIONAL_KEYWORDS = ('IN THE GENERAL ASSEMBLY', 'IN CONGRESS')
INVALID_OFFICES = ('Delegate', 'Delagate',)

VALID_SUBHEADERS = {
    'Reg. Voters',
    'Ballots Cast',
    '% Turnout',
    'Blank',
    'Total Votes',
    'BERNIE SANDERS',
    'Bernie Sanders  (W)',
    'JOSEPH R BIDEN',
    'Joe Biden  (W)',
    'TULSI GABBARD',
    'DONALD J TRUMP',
    'Donald J Trump  (W)',
    'Robert Casey (W)',
    'Kamala Harris (W)',
    'JOSH SHAPIRO',
    'HEATHER HEIDELBAUG H',
    'Heather Heidelbaugh (W)',
    'H SCOTT CONKLIN',
    'MICHAEL LAMB',
    'TRACIE FOUNTAIN',
    'ROSE ROSIE MARIE DAVIS',
    'NINA AHMAD',
    'CHRISTINA M HARTMAN',
    'TIMOTHY DEFOOR',
    'Timothy Defoor (W)',
    'JOE TORSELLA',
    'Joe Torsella (W)',
    'STACY L GARRITY',
    'Stacy L Garrity (W)',
    'LAURA QUICK',
    'GARY WEGMAN',
    'Dan Mueser (W)',
    'MICHELLE SIEGEL',
    'JOHN R GORDNER',
    'John Gordner (W)',
    'BILL MONAHAN',
    'DAVID MILLARD',
    'David Millard (W)',
    'Frances Mannino  (W)',
    'LEANNE BURCHICK',
    'LIZ BETTINGER',
    'VINCE DEMELFI',
    'JIM SAFFORD',
    'PHILA BACK',
    'CATHERINE MAHON',
    'DONNA LEA MERRITT',
    'Donna Lea Merritt  (W)',
    'ROQUE ROCKY DE LA FUENTE',
    'BILL WELD',
    'Paul J Fedder (W)',
    'John Timbrell (W)',
    'DAN MEUSER',
    'KURT A MASSER',
    'J P MORGAN (W)',
    'ROCHELLE MARIE PASQUARIEL',
    'ANDREW SHECKTOR',
    'JANINE PENMAN',
    'JOHN K REBER SR',
    'GEORGE HALCOVAGE',
    'CAROLYN L BONKOSKI',
    'ELLE RULAVAGE',
    'DAVID J MCELWEE',
    'STEVEN MICHAEL WOLFE',
    'Jasmin Watson (W)',
    'Thomas Bond (W)',
    'Ted Buriak Jr (W)',
    'EUGENE Z BONKOSKI',
    'JOHN CUSATIS',
    'Other  (W)',
    'Write-in',
}


SUBHEADER_TO_CANDIDATE_MAPPING = {
    'Bernie Sanders  (W)': 'BERNIE SANDERS',
    'Joe Biden  (W)': 'JOSEPH R BIDEN',
    'Donald J Trump  (W)': 'DONALD J TRUMP',
    'Robert Casey (W)': 'ROBERT CASEY',
    'Kamala Harris (W)': 'KAMALA HARRIS',
    'HEATHER HEIDELBAUG H': 'HEATHER HEIDELBAUGH',
    'Heather Heidelbaugh (W)': 'HEATHER HEIDELBAUGH',
    'Timothy Defoor (W)': 'TIMOTHY DEFOOR',
    'Joe Torsella (W)': 'JOE TORSELLA',
    'Stacy L Garrity (W)': 'STACY L GARRITY',
    'Dan Mueser (W)': 'DAN MUESER',
    'John Gordner (W)': 'JOHN R GORDNER',
    'David Millard (W)': 'DAVID MILLARD',
    'Frances Mannino  (W)': 'FRANCES MANNINO',
    'Donna Lea Merritt  (W)': 'DONNA LEA MERRITT',
    'Paul J Fedder (W)': 'PAUL J FEDDER',
    'John Timbrell (W)': 'JOHN TIMBRELL',
    'J P MORGAN (W)': 'J P MORGAN',
    'Jasmin Watson (W)': 'JASMIN WATSON',
    'Thomas Bond (W)': 'THOMAS BOND',
    'Ted Buriak Jr (W)': 'TED BURIAK JR',
    'Other  (W)': 'OTHER',
    'Reg. Voters': 'Registered Voters'
}


class ColumbiaTableHeader(TableHeader):
    _congressional_keywords = CONGRESSIONAL_KEYWORDS
    _party_map = PARTY_MAP
    _subheader_to_candidate_mapping = SUBHEADER_TO_CANDIDATE_MAPPING


class ColumbiaTableHeaderParser(TableHeaderParser):
    _first_subheader_string = FIRST_SUBHEADER_STRING
    _terminal_header_string = TERMINAL_HEADER_STRING
    _terminal_subheader_strings = TERMINAL_SUBHEADER_STRINGS
    _writein_substring = WRITE_IN_SUBSTRING
    _valid_subheaders = VALID_SUBHEADERS
    _table_header_clazz = ColumbiaTableHeader


class ColumbiaTableBodyParser(TableBodyParser):
    def iterate_jurisdiction_fields(self):
        candidate_data_to_votes = defaultdict(list)
        self._populate_votes(candidate_data_to_votes)
        yield from self._process_votes(candidate_data_to_votes)

    def _populate_votes(self, candidate_data_to_votes):
        for candidate_data in self._table_headers:
            self._populate_jurisdiction_data(candidate_data_to_votes, candidate_data)

    def _process_votes(self, candidate_data_to_votes):
        for candidate_data in candidate_data_to_votes:
            votes = candidate_data_to_votes[candidate_data]
            row_data = [COUNTY, self._jurisdiction.title()] + list(candidate_data) + votes
            row = ParsedRow(*row_data)
            office_is_invalid = sum(invalid_office in row.office for invalid_office in INVALID_OFFICES)
            if not office_is_invalid:
                yield row

    def _parse_category_cell(self, category):
        new_category = self._get_next_string().strip()
        assert(category == new_category)


class ColumbiaPDFPageParser(PDFPageParser):
    _standard_header = COLUMBIA_HEADER
    _table_header_parser = ColumbiaTableHeaderParser
    _table_body_parser = ColumbiaTableBodyParser


class ColumbiaPDFPageIterator(PDFPageIterator):
    def __next__(self):
        next = super().__next__()
        if self.get_page_number() > LAST_VALID_PAGE_NUMBER:
            raise StopIteration
        return next


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
            pdf_to_csv(ColumbiaPDFPageIterator(COLUMBIA_FILE), csv.DictWriter(f, OUTPUT_HEADER), ColumbiaPDFPageParser)
