from collections import namedtuple
from pdfreader import PageDoesNotExist, SimplePDFViewer


CandidateData = namedtuple('CandidateData', 'office district party candidate')


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
            return self
        except PageDoesNotExist as e:
            raise StopIteration(e)

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
    _congressional_keywords = None
    _party_map = None
    _subheader_to_candidate_mapping = None

    def __init__(self, table_headers, unparsed_header_strings, party):
        self._party = party
        self._candidate_data = list(self._candidate_data_iter(table_headers))
        self._unparsed_header_strings = unparsed_header_strings

    def __iter__(self):
        return iter(self._candidate_data)

    def unparsed_length(self):
        return len(self._unparsed_header_strings)

    def strings_prefix_matches(self, strings):
        unparsed_header_strings = strings[:self.unparsed_length()]
        assert(self._unparsed_header_strings == unparsed_header_strings)

    def get_party(self):
        return self._party

    def _candidate_data_iter(self, table_headers):
        for header, subheaders in table_headers:
            party, office, district = self._parse_column_header(*header)
            if party:
                self._party = party
            for subheader in subheaders:
                candidate = self._subheader_to_candidate_mapping.get(subheader, subheader)
                yield CandidateData(office.title(), district, self._party, candidate.title())

    def _parse_column_header(self, office, extra=''):
        district = ''
        party = ''
        if extra in self._party_map:
            party = self._party_map[extra]
        else:
            office = ' '.join([office, extra])
        for keyword in self._congressional_keywords:
            if keyword in office:
                office, district = office.rsplit(keyword, 1)
                office += keyword
                district = int(district.split(' ', 2)[1].replace('ST', '').replace('TH', '').replace('RD', ''))
        return party, office.strip(), district


class TableHeaderParser:
    _first_subheader_string = None
    _terminal_header_string = None
    _terminal_subheader_strings = None
    _writein_substring = None
    _valid_subheaders = None
    _table_header_clazz  = None

    def __init__(self, strings, continued_table_header, continued_table_party):
        self._strings = strings
        self._strings_offset = 0
        self._continued_table_header = continued_table_header
        self._continued_table_party = continued_table_party
        self._table_headers = []  # list of strings with their subheaders
        self._table_header = None  # class for managing table headers
        # temporary parsing management fields
        self._in_subheader_block = False
        self._active_header = []
        self._active_subheader = []
        self._active_subheaders = []
        self._terminal_header_string_seen = False

    def get_header(self):
        if not self._table_header:
            if self._continued_table_header:
                self._parse_continued_table_header()
            else:
                self._parse()
        return self._table_header

    def get_strings_offset(self):
        return self._strings_offset

    def get_party(self):
        return self._table_header.get_party()

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
        self._table_header = self._table_header_clazz(self._table_headers,
                                                      self._strings[:self._strings_offset - 1],
                                                      self._continued_table_party)

    def _done(self):
        return self._terminal_header_string_seen or len(self._strings) <= self._strings_offset

    def _process_string(self, s):
        if s == self._terminal_header_string:
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
        if s == self._writein_substring and not self._active_subheader:
            self._active_subheaders[-1] += ' ' + self._writein_substring
        else:
            self._active_subheader.append(s)
            self._process_active_subheader()
            self._process_end_subheader_state(s)

    def _process_active_subheader(self):
        active_subheader_string = ' '.join(self._active_subheader)
        if active_subheader_string in self._valid_subheaders:
            self._active_subheaders.append(active_subheader_string)
            self._active_subheader = []

    def _process_start_subheader_state(self, s):
        if s == self._first_subheader_string:
            self._in_subheader_block = True
            if self._active_subheaders:
                self._finalize_header_block()

    def _process_end_subheader_state(self, s):
        if s in self._terminal_subheader_strings:
            self._in_subheader_block = False
            self._finalize_header_block()

    def _finalize_header_block(self):
        self._table_headers.append((self._active_header, self._active_subheaders))
        self._active_header = []
        self._active_subheaders = []


class TableBodyParser:
    TURNOUT_OFFICE = 'Turnout'
    SKIPPED_TURNOUT_SUBHEADERS = ('% Turnout', 'Blank')
    SKIPPED_CANDIDATE_SUBHEADERS = ('Registered Voters', 'Total Votes')

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
                yield from self.iterate_jurisdiction_fields()

    def iterate_jurisdiction_fields(self):
        raise NotImplementedError

    def is_office_section_active(self):
        return self._is_office_section_active

    @staticmethod
    def _is_turnout_header(candidate_data):
        return candidate_data.office == TableBodyParser.TURNOUT_OFFICE

    @staticmethod
    def _skipped_subheader(candidate_data):
        if TableBodyParser._is_turnout_header(candidate_data):
            return candidate_data.candidate in TableBodyParser.SKIPPED_TURNOUT_SUBHEADERS
        return candidate_data.candidate in TableBodyParser.SKIPPED_CANDIDATE_SUBHEADERS

    def _populate_jurisdiction_data(self, candidate_data_to_votes, candidate_data):
        if self._skipped_subheader(candidate_data):
            self._get_next_string()  # metadata field
        else:
            vote_count = self._get_next_string()
            vote_count = int(vote_count if vote_count != '-' else 0)
            if not self._is_turnout_header(candidate_data):
                self._get_next_string()  # vote percent
            else:
                # Registered Voters and Ballot Cast are treated as `office` instead of `candidate`
                candidate_data = CandidateData(candidate_data.candidate, '', '', '')
            candidate_data_to_votes[candidate_data].append(vote_count)

    def _get_next_string(self):
        s = self._strings[self._strings_offset]
        self._strings_offset += 1
        return s


class PDFPageParser:
    _standard_header = None
    _table_header_parser = None
    _table_body_parser = None

    def __init__(self, page, continued_table_header, continued_party):
        strings = page.get_strings()
        self._validate_header(strings, page.get_page_number())
        strings = strings[len(self._standard_header) + 1:]
        table_header_parser = self._table_header_parser(strings, continued_table_header, continued_party)
        self._table_headers = table_header_parser.get_header()
        self._party = table_header_parser.get_party()
        strings = strings[table_header_parser.get_strings_offset():]
        self._table_body_parser = self._table_body_parser(strings, self._table_headers)

    def __iter__(self):
        return iter(self._table_body_parser)

    def get_continued_table_header(self):
        if not self._table_body_parser.is_office_section_active():
            return None
        return self._table_headers

    def get_continued_party(self):
        return self._party

    def _validate_header(self, strings, page_number):
        header = strings[:len(self._standard_header)]
        page_number_string = strings[len(self._standard_header)]
        assert(header == self._standard_header)
        assert(page_number_string.split('/')[0].split()[-1] == str(page_number))


def pdf_to_csv(pdf, csv_writer, pdf_page_parser_clazz):
    csv_writer.writeheader()
    previous_table_header = None
    previous_party = ''
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = pdf_page_parser_clazz(page, previous_table_header, previous_party)
        for row in pdf_page_parser:
            csv_writer.writerow(row._asdict())
        previous_table_header = pdf_page_parser.get_continued_table_header()
        previous_party = pdf_page_parser.get_continued_party()
