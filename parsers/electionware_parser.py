from parsers.pa_pdf_parser import PDFStringIterator


INSTRUCTION_ROW_PREFIX = 'Vote For'
BALLOTS_CAST_PREFIX = 'Ballots Cast'

PARTIES = {
    'DEM',
    'REP',
}
PARTY_ABBREVIATIONS = {
    'Total': '',
    'Blank': 'Blank',
    'Democratic Party': 'DEM',
    'Republican Party': 'REP',
    'DEMOCRATIC': 'DEM',
    'REPUBLICAN': 'REP',
    'NONPARTISAN': 'NPA',
}


class ElectionwarePDFStringIterator(PDFStringIterator):
    _first_footer_substring = None
    _second_footer_substring = None

    def page_is_done(self):
        s = self.peek()
        return s.startswith(self._first_footer_substring) or s.startswith(self._second_footer_substring)

    def table_is_done(self):
        return self.peek().startswith(INSTRUCTION_ROW_PREFIX)

    def swap_any_bad_ballots_cast_fields(self):
        s = self._strings[self._strings_offset + 1]
        if s.startswith(BALLOTS_CAST_PREFIX):
            self._strings[self._strings_offset + 1] = self._strings[self._strings_offset]
            self._strings[self._strings_offset] = s


class ElectionwarePDFTableParser:
    _county = None
    _expected_table_headers = None
    _openelections_mapped_header = None
    _raw_office_to_office_and_district = None

    def __init__(self, precinct, string_iterator):
        self._string_iterator = string_iterator
        self._precinct = precinct
        self._skip_instruction_row()
        self._parse_header()
        self._verify_table_header()

    def __iter__(self):
        while True:
            row = self._parse_row()
            if self._should_be_recorded(row):
                yield row

    def _parse_header(self):
        self._office = next(self._string_iterator)
        self._party = ''
        for party in PARTIES:
            if self._office.upper().startswith(party + ' '):
                self._party, self._office = self._office.split(' ', 1)
                self._party = self._party.upper()
                break

    def _verify_table_header(self):
        actual_header = ''
        while len(actual_header) < len(self._expected_table_headers[0]):
            actual_header += next(self._string_iterator) + ' '
        assert actual_header.strip() in self._expected_table_headers

    def _parse_row(self):
        if self._string_iterator.page_is_done() or self._string_iterator.table_is_done():
            raise StopIteration
        self._string_iterator.swap_any_bad_ballots_cast_fields()
        candidate = next(self._string_iterator)
        row = {'county': self._county,
               'precinct': self._precinct,
               'office': self._office,
               'party': self._party,
               'district': '',
               'candidate': candidate.strip()}
        self._clean_row(row)
        self._populate_votes(row)
        return row

    def _populate_votes(self, row):
        for header in self._openelections_mapped_header:
            votes_string = next(self._string_iterator)
            if '%' not in votes_string:
                row[header] = int(votes_string.replace(',', ''))
            if row['office'] in ('Registered Voters', 'Voter Turnout'):
                # only one column for each of these
                break

    def _skip_instruction_row(self):
        if self._string_iterator.peek().startswith(INSTRUCTION_ROW_PREFIX):
            next(self._string_iterator)

    @classmethod
    def _clean_row(cls, row):
        if row['office'] == 'STATISTICS':
            row['office'], party = row['candidate'].split(' - ', 1)
            row['party'] = PARTY_ABBREVIATIONS[party]
            row['candidate'] = ''
        if row['office'] in cls._raw_office_to_office_and_district:
            row['office'], row['district'] = cls._raw_office_to_office_and_district[row['office']]
        if row['candidate'] == 'Write-In Totals':
            row['candidate'] = 'Write-in'

    @classmethod
    def _should_be_recorded(cls, row):
        if row['candidate'] in ('Total Votes Cast', 'Contest Totals', 'Not Assigned'):
            return False
        if 'Delegate' in row['office']:
            return False
        if row['office'] == 'Voter Turnout':
            return False
        if row['party'] == 'Blank':
            return False
        return True


class ElectionwarePDFPageParser:
    _pdf_string_iterator_clazz = None
    _pdf_table_parser_clazz = None
    _header = None

    def __init__(self, page):
        self._string_iterator = self._pdf_string_iterator_clazz(page.get_strings())
        self._verify_header()
        self._init_precinct()

    def __iter__(self):
        while not self._string_iterator.page_is_done():
            table_parser = self._pdf_table_parser_clazz(self._precinct, self._string_iterator)
            yield from iter(table_parser)

    def _verify_header(self):
        header = [next(self._string_iterator) for _ in range(len(self._header))]
        assert header == self._header

    def _init_precinct(self):
        self._precinct = next(self._string_iterator)


def pdf_to_csv(pdf, csv_writer, pdf_page_parser_clazz):
    csv_writer.writeheader()
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = pdf_page_parser_clazz(page)
        for row in pdf_page_parser:
            csv_writer.writerow(row)
