from collections import defaultdict, namedtuple
from pdfreader import PageDoesNotExist, SimplePDFViewer
import csv
import os


CandidateData = namedtuple('CandidateData', 'office district party candidate')
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

TERMINAL_TABLE_HEADER_STRING = 'Jurisdiction Wide'
FIRST_SUBHEADER_STRING = 'Reg.'
TERMINAL_SUBHEADER_STRINGS = ('Write-in', 'Turnout')
WRITE_IN_SUBSTRING = '(W)'
CONGRESSIONAL_KEYWORDS = ('IN THE GENERAL ASSEMBLY', 'IN CONGRESS')
INVALID_OFFICES = ('Delegate', 'Dlegate', 'Republican Committee')
VOTE_CATEGORIES = ('Normal', 'Absentee', 'Mail-In', 'Provisional')

VALID_SUBHEADERS = {
    'Reg. Voters',
    'Ballots Cast',
    '% Turnout',
    'Total Votes',
    'BERNIE SANDERS',
    'BERNIE SANDERS (W)',
    'JOSEPH R. BIDEN',
    'JOSEPH BIDEN  (W)',
    'TULSI GABBARD',
    'DONALD J. TRUMP',
    'DONALD TRUMP  (W)',
    'JOSH SHAPIRO',
    'JOSH SHAPIRO  (W)',
    'HEATHER HEIDELBAUG H',
    'HEATHER HEIDELBAUG H  (W)',
    'H. SCOTT CONKLIN',
    'MICHAEL LAMB',
    'TRACIE FOUNTAIN',
    'ROSE ROSIE MARIE DAVIS',
    'NINA AHMAD',
    'CHRISTINA M. HARTMAN',
    'CHRISTINA HARTMAN',
    'CHRISTINA HARTMAN (W)',
    'TIMOTHY DEFOOR',
    'TIMOTHY DEFOOR  (W)',
    'TRACIE FOUNTAIN',
    'TRACIE FOUNTAIN (W)',
    'JOE TORSELLA',
    'JOSEPH TORSELLA',
    'JOSEPH TORSELLA (W)',
    'STACY L. GARRITY',
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
}


class PDFPageIterator:
    def __init__(self, filename):
        self._pdf_viewer = SimplePDFViewer(open(filename, 'rb'))
        self._page_number = 0
        self._rendered = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            self._go_to_next_pdf_page()
        except PageDoesNotExist as e:
            raise StopIteration(e)
        return self

    def get_page_number(self):
        return self._page_number

    def get_strings(self):
        if not self._rendered:
            self._pdf_viewer.render()
            self._rendered = True
        return self._pdf_viewer.canvas.strings

    def _go_to_next_pdf_page(self):
        if self._page_number != 0:
            self._pdf_viewer.next()
        self._page_number += 1
        self._rendered = False


class TableHeader:
    def __init__(self, table_headers, unparsed_header_strings):
        self._candidate_data = list(TableHeader._candidate_data_iter(table_headers))
        self._unparsed_header_strings = unparsed_header_strings

    def __iter__(self):
        return iter(self._candidate_data)

    def unparsed_length(self):
        return len(self._unparsed_header_strings)

    def strings_prefix_matches(self, strings):
        unparsed_header_strings = strings[:self.unparsed_length()]
        assert(self._unparsed_header_strings == unparsed_header_strings)

    @staticmethod
    def _candidate_data_iter(table_headers):
        for header, subheaders in table_headers:
            party, office, district = TableHeader._parse_column_header(*header)
            for subheader in subheaders:
                candidate = SUBHEADER_TO_CANDIDATE_MAPPING.get(subheader, subheader)
                yield CandidateData(office.title(), district, party, candidate.title())

    @staticmethod
    def _parse_column_header(office, extra=''):
        district = ''
        party = ''
        if extra in PARTY_MAP:
            party = PARTY_MAP[extra]
        else:
            office = ' '.join([office, extra])
        for keyword in CONGRESSIONAL_KEYWORDS:
            if keyword in office:
                office, district = office.rsplit(keyword, 1)
                office += keyword
                district = int(district.split(' ', 2)[1].replace('ST', '').replace('TH', '').replace('RD', ''))
        return party, office.strip(), district


class TableHeaderParser:
    def __init__(self, strings, continued_table_header):
        self._strings = strings
        self._strings_offset = 0
        self._table_headers = []
        self._in_subheader_block = False
        self._active_header = []
        self._active_subheader = []
        self._active_subheaders = []
        self._terminal_header_string_seen = False
        self._continued_table_header = continued_table_header
        self._table_header = None

    def get_header(self):
        if not self._table_header:
            if self._continued_table_header:
                self._parse_continued_table_header()
            else:
                self._parse()
        return self._table_header

    def get_strings_offset(self):
        return self._strings_offset

    def _parse_continued_table_header(self):
        self._continued_table_header.strings_prefix_matches(self._strings)
        self._strings_offset = self._continued_table_header.unparsed_length()
        self._table_header = self._continued_table_header

    def _parse(self):
        while not self._done():
            s = self._strings[self._strings_offset]
            self._process_string(s)
            self._strings_offset += 1
        if self._active_subheaders:
            assert(not self._active_subheader)
            self._finalize_header_block()
        self._table_header = TableHeader(self._table_headers, self._strings[:self._strings_offset - 1])

    def _done(self):
        return self._terminal_header_string_seen or len(self._strings) <= self._strings_offset

    def _process_string(self, s):
        if s == TERMINAL_TABLE_HEADER_STRING:
            self._terminal_header_string_seen = True
        else:
            self._process_start_subheader_state(s)
            if not self._in_subheader_block:
                self._process_header_string(s)
            else:
                self._process_subheader_string(s)

    def _process_header_string(self, s):
        self._active_header.append(s)

    def _process_subheader_string(self, s):
        if s == WRITE_IN_SUBSTRING:
            self._active_subheaders[-1] += ' ' + WRITE_IN_SUBSTRING
        else:
            self._active_subheader.append(s)
            self._process_active_subheader()
            self._process_end_subheader_state(s)

    def _process_active_subheader(self):
        active_subheader_string = ' '.join(self._active_subheader)
        if active_subheader_string in VALID_SUBHEADERS:
            self._active_subheaders.append(active_subheader_string)
            self._active_subheader = []

    def _process_start_subheader_state(self, s):
        if s == FIRST_SUBHEADER_STRING:
            self._in_subheader_block = True
            if self._active_subheaders:
                self._finalize_header_block()

    def _process_end_subheader_state(self, s):
        if s in TERMINAL_SUBHEADER_STRINGS:
            self._in_subheader_block = False
            self._finalize_header_block()

    def _finalize_header_block(self):
        self._table_headers.append((self._active_header, self._active_subheaders))
        self._active_header = []
        self._active_subheaders = []


class TableBodyParser:
    def __init__(self, strings, table_headers):
        self._is_office_section_active = True
        self._strings_offset = 0
        self._strings = strings
        self._table_headers = table_headers
        self._jurisdiction = None

    def __iter__(self):
        while self._strings_offset < len(self._strings):
            self._jurisdiction = self._get_next_string()
            if self._jurisdiction == 'Total':
                self._is_office_section_active = False
            if self._is_office_section_active:
                yield from self._iterate_categories()

    def _iterate_categories(self):
        candidate_data_to_category_votes = defaultdict(list)
        for category in VOTE_CATEGORIES:
            self._parse_category_cell(category)
            self._populate_category_data(candidate_data_to_category_votes)
        for candidate_data in candidate_data_to_category_votes:
            category_votes = candidate_data_to_category_votes[candidate_data]
            row_data = [COUNTY, self._jurisdiction.title()] + list(candidate_data) \
                + category_votes + [sum(category_votes)]
            yield ParsedRow(*row_data)

    def _populate_category_data(self, candidate_data_to_category_votes):
        for candidate_data in self._table_headers:
            if self._skipped_subheader(candidate_data):
                self._get_next_string()  # metadata field
            else:
                vote_count = self._get_next_string()
                vote_count = int(vote_count if vote_count != '-' else 0)
                self._get_next_string()  # vote percent
                candidate_data_to_category_votes[candidate_data].append(vote_count)

    def is_office_section_active(self):
        return self._is_office_section_active

    @staticmethod
    def _skipped_subheader(candidate_data):
        return candidate_data.office == 'Turnout' or candidate_data.candidate in ('Reg. Voters', 'Total Votes')

    def _parse_category_cell(self, category):
        new_category = self._get_next_string().strip()
        assert(category == new_category)

    def _get_next_string(self):
        s = self._strings[self._strings_offset]
        self._strings_offset += 1
        return s


class PDFPageParser:
    def __init__(self, page, continued_table_header):
        strings = page.get_strings()
        self._validate_header(strings, page.get_page_number())
        strings = strings[len(BRADFORD_HEADER) + 1:]
        table_header_parser = TableHeaderParser(strings, continued_table_header)
        self._table_headers = table_header_parser.get_header()
        strings = strings[table_header_parser.get_strings_offset():]
        self._table_body_parser = TableBodyParser(strings, self._table_headers)

    def __iter__(self):
        return iter(self._table_body_parser)

    def get_continued_table_header(self):
        if not self._table_body_parser.is_office_section_active():
            return None
        return self._table_headers

    @staticmethod
    def _validate_header(strings, page_number):
        header = strings[:len(BRADFORD_HEADER)]
        page_number_string = strings[len(BRADFORD_HEADER)]
        assert(header == BRADFORD_HEADER)
        assert(page_number_string.split('/')[0].split()[-1] == str(page_number))


def pdf_to_csv(pdf, csv_writer):
    csv_writer.writeheader()
    previous_table_header = None
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_parser = PDFPageParser(page, previous_table_header)
        for row in pdf_parser:
            office_is_invalid = sum(invalid_office in row.office for invalid_office in INVALID_OFFICES)
            if not office_is_invalid:
                csv_writer.writerow(row._asdict())
        previous_table_header = pdf_parser.get_continued_table_header()


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BRADFORD_FILE), csv.DictWriter(f, OUTPUT_HEADER))
