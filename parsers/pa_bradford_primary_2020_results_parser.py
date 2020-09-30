from collections import defaultdict, namedtuple
import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator, PDFPageParser,\
    TableBodyParser, TableHeaderParser, TableHeader, pdf_to_csv
from parsers.constants.pa_candidates_2020 import STATEWIDE_PRIMARY_CANDIDATES


ParsedRow = namedtuple('ParsedRow', 'county precinct office district party candidate '
                                    'election_day absentee mail_in provisional votes')

COUNTY = 'Bradford'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__bradford__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'mail_in', 'provisional', 'votes']

BRADFORD_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                             'Bradford PA Primary SOVC_JUNEFINALREPORT.pdf')
BRADFORD_HEADER = [
    '',
    'Statement of Votes Cast',
    ' BRADFORD COUNTY, PA',
    'GENERAL PRIMARY ELECTION',
    'JUNE 2, 2020',
    'RESULTS', 'Date: 6/19/2020',
    'Time: 2:43:10 PM EDT'
]

PARTY_MAP = {
    '(REPUBLICAN)': 'REP',
    '(DEMOCRATIC)': 'DEM'
}

TERMINAL_HEADER_STRING = 'Jurisdiction Wide'
FIRST_SUBHEADER_STRING = 'Reg.'
TERMINAL_SUBHEADER_STRINGS = ('Write-in', 'Turnout')
WRITE_IN_SUBSTRING = '(W)'
CONGRESSIONAL_KEYWORDS = ('IN THE GENERAL ASSEMBLY', 'IN CONGRESS')
INVALID_OFFICES = ('Delegate', 'Dlegate', 'Republican Committee')
VOTE_CATEGORIES = ('Normal', 'Absentee', 'Mail-In', 'Provisional')

VALID_SUBHEADERS = STATEWIDE_PRIMARY_CANDIDATES | {
    'Reg. Voters',
    'Ballots Cast',
    '% Turnout',
    'Total Votes',
    'BERNIE SANDERS (W)',
    'JOSEPH BIDEN  (W)',
    'DONALD TRUMP  (W)',
    'JOSH SHAPIRO  (W)',
    'HEATHER HEIDELBAUG H',
    'HEATHER HEIDELBAUG H  (W)',
    'CHRISTINA HARTMAN',
    'CHRISTINA HARTMAN (W)',
    'TIMOTHY DEFOOR  (W)',
    'TRACIE FOUNTAIN (W)',
    'JOSEPH TORSELLA',
    'JOSEPH TORSELLA (W)',
    'STACY GARRITY  (W)',
    'LEE GRIFFIN',
    'LEE GRIFFIN (W)',
    'FRED KELLER',
    'FRED KELLER (W)',
    'DOUG MCLINKO',
    'DOUG MCLINKO  (W)',
    'JACKIE BAKER',
    'JACKIE BAKER  (W)',
    'GENE YAW',
    'GENE YAW (W)',
    'CLINT OWLETT',
    'CLINT OWLETT  (W)',
    'TINA PICKETT',
    'TINA PICKETT (W)',
    'DONNA IANNONE  (W)',
    'JAMES J DALY (W)',
    'JAMES J DALY  (W)',
    'JAMES SHAW',
    'JAMES SHAW (W)',
    'NANCI ROMMEL',
    'KEITH BIERLY',
    'RACHEL DELGREGO',
    'RICK THOMAS',
    'CAROLINE RIES',
    'KIMBERLY HART',
    'TARAN SAMARTH',
    'DANNY MULDOWNEY',
    'ROQUE ROCKY DE LA FUENTE',
    'BILL WELD',
    'DONALD HOFFMAN',
    'CAROL SIDES',
    'DAVE HUFFMAN',
    'ALAN HALL',
    'KRYSTLE BRISTOL',
    'TODD ROBATIN',
    'MARK J. HARRIS',
    'DAVID ROCKWELL',
    'DAVID ROCKWELL (W)',
    'DANIEL F. CLARK',
    'MARY J. HAYES',
    'IRENE C. HARRIS',
    'BRIAN R. HARRIS',
    'O.B. BUDDY CROCKETT, JR.',
    'NANCY C. CROCKETT',
    'JOYCE A. GRANT',
    'EDWARD S. GRANT',
    'GERALD V. ALLEN',
    'HOWARD J. SMITH',
    'JAMES VAN BLARCOM',
    'DARLENE VAN BLARCOM',
    'W. THOMAS BLACKALL',
    'DEEANN TOKACH',
    'ERIC A. CHAFFEE',
    'JASON C. KRISE',
    'VERNON PERRY, III',
    'NORMA MOORE',
    'ANN EASTABROOK',
    'PAT ANTHONY',
    'DEAN CACCIAVILLA NO',
    'DAWN I. CLOSE',
    'DIANE ELLIOTT',
    'GRACE GEORGE',
    'JOHN P. GEORGE',
    'ANDREW HICKEY',
    'ERIC MATTHEWS',
    'JANICE M. KELLOGG',
    'VICTOR LAWSON',
    'RICHARD HARRIS',
    'BETTY HARRIS',
    'DARYL MILLER',
    'KAY MILLER',
    'SEBRINA SHANKS',
    'DAVID A. SHADDUCK',
    'MARLETA L. SHADDUCK',
    'CHAD M. SALSMAN',
    'SUSAN L. SALSMAN',
    'WILLIAM W. THEM',
    'Write-in',
}


SUBHEADER_TO_CANDIDATE_MAPPING = {
    'BERNIE SANDERS (W)': 'BERNIE SANDERS',
    'JOSEPH BIDEN  (W)': 'JOSEPH R. BIDEN',
    'DONALD TRUMP  (W)': 'DONALD J. TRUMP',
    'JOSH SHAPIRO  (W)': 'JOSH SHAPIRO',
    'HEATHER HEIDELBAUG H': 'HEATHER HEIDELBAUGH',
    'HEATHER HEIDELBAUG H  (W)': 'HEATHER HEIDELBAUGH',
    'CHRISTINA HARTMAN': 'CHRISTINA M. HARTMAN',
    'CHRISTINA HARTMAN (W)': 'CHRISTINA M. HARTMAN',
    'TIMOTHY DEFOOR  (W)': 'TIMOTHY DEFOOR',
    'TRACIE FOUNTAIN (W)': 'TRACIE FOUNTAIN',
    'JOE TORSELLA': 'JOSEPH TORSELLA',
    'JOSEPH TORSELLA (W)': 'JOSEPH TORSELLA',
    'STACY GARRITY  (W)': 'STACY L. GARRITY',
    'LEE GRIFFIN (W)': 'LEE GRIFFIN',
    'FRED KELLER (W)': 'FRED KELLER',
    'DOUG MCLINKO  (W)': 'DOUG MCLINKO',
    'JACKIE BAKER  (W)': 'JACKIE BAKER',
    'GENE YAW (W)': 'GENE YAW',
    'CLINT OWLETT  (W)': 'CLINT OWLETT',
    'TINA PICKETT (W)': 'TINA PICKETT',
    'DONNA IANNONE  (W)': 'DONNA IANNONE',
    'JAMES J DALY (W)': 'JAMES J. DALY',
    'JAMES J DALY  (W)': 'JAMES J. DALY',
    'JAMES SHAW (W)': 'JAMES SHAW',
    'DAVID ROCKWELL (W)': 'DAVID ROCKWELL',
    'DEAN CACCIAVILLA NO': 'DEAN CACCIAVILLANO',
    'Reg. Voters': 'Registered Voters'
}


class BradfordTableHeader(TableHeader):
    _congressional_keywords = CONGRESSIONAL_KEYWORDS
    _party_map = PARTY_MAP
    _subheader_to_candidate_mapping = SUBHEADER_TO_CANDIDATE_MAPPING


class BradfordTableHeaderParser(TableHeaderParser):
    _first_subheader_string = FIRST_SUBHEADER_STRING
    _terminal_header_string = TERMINAL_HEADER_STRING
    _terminal_subheader_strings = TERMINAL_SUBHEADER_STRINGS
    _writein_substring = WRITE_IN_SUBSTRING
    _valid_subheaders = VALID_SUBHEADERS
    _table_header_clazz = BradfordTableHeader


class BradfordTableBodyParser(TableBodyParser):
    def iterate_jurisdiction_fields(self):
        candidate_data_to_category_votes = defaultdict(list)
        self._populate_category_votes(candidate_data_to_category_votes)
        yield from self._process_category_votes(candidate_data_to_category_votes)

    def _populate_category_votes(self, candidate_data_to_category_votes):
        for category in VOTE_CATEGORIES:
            self._parse_category_cell(category)
            for candidate_data in self._table_headers:
                self._populate_jurisdiction_data(candidate_data_to_category_votes, candidate_data)

    def _process_category_votes(self, candidate_data_to_category_votes):
        for candidate_data in candidate_data_to_category_votes:
            category_votes = candidate_data_to_category_votes[candidate_data]
            row = self._generate_row(candidate_data, category_votes)
            office_is_invalid = max(invalid_office in row.office for invalid_office in INVALID_OFFICES)
            if not office_is_invalid:
                yield row

    def _generate_row(self, candidate_data, category_votes):
        row_data = [COUNTY, self._jurisdiction.title()] + list(candidate_data)
        if candidate_data.office == 'Registered Voters':
            assert(min(category_votes) == max(category_votes))
            row_data += [''] * len(VOTE_CATEGORIES) + [category_votes[0]]
        else:
            row_data += category_votes + [sum(category_votes)]
        return ParsedRow(*row_data)

    def _parse_category_cell(self, category):
        new_category = next(self._string_iterator).strip()
        assert(category == new_category)


class BradfordPDFPageParser(PDFPageParser):
    _standard_header = BRADFORD_HEADER
    _table_header_parser = BradfordTableHeaderParser
    _table_body_parser = BradfordTableBodyParser


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BRADFORD_FILE), csv.DictWriter(f, OUTPUT_HEADER), BradfordPDFPageParser)
