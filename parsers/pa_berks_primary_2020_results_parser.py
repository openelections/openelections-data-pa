from collections import namedtuple
import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator, PDFStringIterator
from parsers.constants.pa_candidates_2020 import STATEWIDE_PRIMARY_CANDIDATES


# Uses Electionware Precinct Report PDF format
COUNTY = 'Berks'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__berks__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

BERKS_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                          'Berks PA 2020 Primary Precinct Results.pdf')
BERKS_HEADER = [
    '',
    '2020 PRIMARY',
    'June 2, 2020',
    'BERKS COUNTY',
    'CERTIFIED RESULTS',
    'Precinct Report',
]

TERMINAL_SUBHEADER_STRINGS = ('Ballots Cast - Total', 'Write-in Totals')
FIRST_FOOTER_SUBSTRING = 'Custom Table Report - 06/22/2020'
LAST_ROW_PRECINCT = 'Totals'

VALID_HEADERS = {
    'STATISTICS',
    'PRESIDENT OF THE UNITED STATES',
    'ATTORNEY GENERAL',
    'AUDITOR GENERAL',
    'STATE TREASURER',
    'REPRESENTATIVE IN CONGRESS C04',
    'REPRESENTATIVE IN CONGRESS 6TH DISTRICT',
    'REPRESENTATIVE IN CONGRESS 9TH DISTRICT',
    'SENATOR IN THE GENERAL ASSEMBLY 11TH DISTRICT',
    'SENATOR IN THE GENERAL ASSEMBLY 29TH DISTRICT',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L5',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L124',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L126',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L127',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L128',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L129',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L130',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY L134',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 187TH DISTRICT',
    'DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION C04',
    'DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 6TH DISTRICT',
    'DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION 9TH DISTRICT',
    'ALTERNATE DELEGATE TO THE DEMOCRATIC NATIONAL CONVENTION C04',
    'ALTERNA TE DELEGATE TO THE DEMOCRA TIC NATIONA L CONVENT ION 6TH DISTRICT',
    'DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION C04',
    'DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 6TH DISTRICT',
    'DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 9TH  DISTRICT',
    'ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 4',
    'ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 6',
    'ALTERNATE DELEGATE TO THE REPUBLICAN NATIONAL CONVENTION 9',
}

HEADER_TO_OFFICE  = {
    'PRESIDENT OF THE UNITED STATES': 'President',
    'REPRESENTATIVE IN CONGRESS': 'U.S. House',
    'SENATOR IN THE GENERAL ASSEMBLY': 'State Senate',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY': 'General Assembly',
}

OFFICES_WITH_DISTRICTS = (
    'REPRESENTATIVE IN CONGRESS',
    'SENATOR IN THE GENERAL ASSEMBLY',
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY',
)

PARTIES = {
    'DEM',
    'REP',
}

VALID_SUBHEADERS = STATEWIDE_PRIMARY_CANDIDATES | {
    'Ballots Cast - Total',
    'Write-in Totals',
    'MADELEINE DEAN',
    'CHRISSY HOULAHAN',
    'LAURA QUICK',
    'GARY WEGMAN',
    'JUDY SCHWANK',
    'GRAHAM GONZALES',
    'TAYLOR PICONE',
    'MARK ROZZI',
    'ROBIN COSTENBADER- JACOBSON',
    'MANNY GUZMAN',
    'ROBERT MELENDEZ',
    'RAYMOND EDWARD BAKER',
    'CESAR CEPEDA',
    'KELLY MCDONOUGH',
    'LAMAR FOLEY',
    'MICHAEL BLICHAR, JR.',
    'KATHY BARNETTE',
    'JOHN EMMONS',
    'VINCENT D. GAGLIARDO, JR.',
    'BRANDEN MOYER',
    'MARK M. GILLEN',
    'JIM COX',
    'DAVID M. MALONEY',
    'RYAN E. MACKENZIE',
    'GARY DAY',
    'LEANNE BURCHIK',
    'LIZ BETTINGER',
    'VINCE DEMELFI',
    'JIM SAFFORD',
    'PHILA BACK',
    'CATHERINE MAHON',
    'SANDRA WATERS',
    'DAN MEUSER',
    'ANNETTE C. BAKER',
    'DAVE ARGALL',
    'BARRY JOZWIAK',
    'JERRY KNOWLES',
    'JAMES D. OSWALD',
}


Candidate = namedtuple('Candidate', 'office district party name')
ParsedRow = namedtuple('ParsedRow', 'county precinct office district party candidate votes')


class Office:
    def __init__(self, s):
        self.name = s
        self.district = ''
        self.party = ''
        self.extract_party()

    def append(self, s):
        if not self.name:
            self.name = s
        else:
            self.name += ' ' + s
        self._trim_spaces()

    def extract_party(self):
        if self.name in PARTIES:
            self.party = self.name
            self.name = ''
        elif ' ' in self.name:
            prefix, suffix = self.name.split(' ', 1)
            if prefix in PARTIES:
                self.party, self.name = prefix, suffix

    def extract_district(self):
        for office_with_district in OFFICES_WITH_DISTRICTS:
            if office_with_district in self.name:
                self.name, self.district = self.name.rsplit(' ', 1)
                if self.district == 'DISTRICT':
                    self.name, self.district = self.name.rsplit(' ', 1)
                for stripped_string in ('ST', 'ND', 'RD', 'TH', 'L', 'C'):
                    self.district = self.district.replace(stripped_string, '')
                self.district = int(self.district)

    def is_valid(self):
        return self.name in VALID_HEADERS

    def is_terminal(self):
        return self.name.startswith('VOTE FOR') or self.name == 'STATISTICS'

    def should_be_recorded(self):
        return 'DELEGATE' not in self.name

    def normalize(self):
        self.name = HEADER_TO_OFFICE.get(self.name, self.name)

    def _trim_spaces(self):
        self.name = self.name.replace('STATISTI CS', 'STATISTICS')\
            .replace('REPRESE NTATIVE', 'REPRESENTATIVE')\
            .replace('ASSEMBL Y', 'ASSEMBLY')\
            .replace('DELEGAT E', 'DELEGATE')\
            .replace('  ', ' ')


class BerksPDFTableHeaderParser(PDFStringIterator):
    def __init__(self, strings):
        super().__init__(strings)
        self._candidates = None

    def get_candidates(self):
        if self._candidates is None:
            self._parse()
        return self._candidates

    def _parse(self):
        offices = list(self._parse_headers())
        self._candidates = list(self._parse_subheaders(offices))

    def _parse_headers(self):
        office = None
        while not (office and office.is_terminal()):
            s = self._get_next_string()
            if not office:
                office = Office(s)
            else:
                office.append(s)
            if office.is_valid():
                office.extract_district()
                if office.should_be_recorded():
                    office.normalize()
                    yield office
                if not office.is_terminal():
                    office = None

    def _parse_subheaders(self, offices):
        for office in offices:
            candidate = None
            while not (candidate and candidate in TERMINAL_SUBHEADER_STRINGS):
                s = self._get_next_string()
                if self._ignorable_string(s):
                    continue
                if not candidate:
                    candidate = s
                else:
                    candidate += ' ' + s
                if candidate in VALID_SUBHEADERS:
                    yield from self._process_candidate(candidate, office)
                    if candidate not in TERMINAL_SUBHEADER_STRINGS:
                        candidate = None

    @staticmethod
    def _ignorable_string(s):
        return s == '-' or s.startswith('VOTE FOR')

    @staticmethod
    def _process_candidate(candidate, office):
        if office.name == 'STATISTICS':
            assert(candidate == 'Ballots Cast - Total')
            yield Candidate('Ballots Cast', '', '', '')
        else:
            if candidate == 'Write-in Totals':
                candidate = 'Write-in'
            if candidate == 'ROBIN COSTENBADER- JACOBSON':
                candidate = 'ROBIN COSTENBADER-JACOBSON'
            yield Candidate(office.name, office.district, office.party, candidate)


class BerksPDFTableBodyParser(PDFStringIterator):
    def __init__(self, strings, candidates):
        super().__init__(strings)
        self._candidates = candidates
        self._page_is_done = False

    def __iter__(self):
        while self._has_next_string():
            precinct = self._get_next_string()
            if precinct.startswith(FIRST_FOOTER_SUBSTRING):
                self._page_is_done = True
                break
            yield from self._parse_row(precinct)
            if precinct == LAST_ROW_PRECINCT:
                break

    def page_is_done(self):
        return self._page_is_done

    def _parse_row(self, precinct):
        for candidate in self._candidates:
            votes = int(self._get_next_string().replace(',', ''))
            if precinct != LAST_ROW_PRECINCT:
                yield ParsedRow(COUNTY, precinct.title(), candidate.office.title(),
                                candidate.district, candidate.party,
                                candidate.name.title(), votes)


class BerksPDFPageParser:
    def __init__(self, page):
        self._table_body_parser = None
        strings = page.get_strings()
        if 'CITY OF READING QUESTIONS' in strings[3:5]:
            # skip these pages; different format than others and are amendment questions
            self._strings = [FIRST_FOOTER_SUBSTRING]
        else:
            header = strings[:len(BERKS_HEADER)]
            assert (header == BERKS_HEADER)
            self._strings = strings[len(BERKS_HEADER):]

    def __iter__(self):
        while not self.page_is_done():
            table_header_parser = BerksPDFTableHeaderParser(self._strings)
            candidates = table_header_parser.get_candidates()
            if not candidates:
                # any page without valid candidates has no additional
                # tables and is therefore skippable
                break
            self._strings = table_header_parser.get_remaining_strings()
            self._table_body_parser = BerksPDFTableBodyParser(self._strings, candidates)
            yield from iter(self._table_body_parser)
            self._strings = self._table_body_parser.get_remaining_strings()

    def page_is_done(self):
        if self._strings[0].startswith(FIRST_FOOTER_SUBSTRING):
            return True
        return self._table_body_parser and self._table_body_parser.page_is_done()


def pdf_to_csv(pdf, csv_writer):
    csv_writer.writeheader()
    for page in pdf:
        print(f'processing page {page.get_page_number()}')
        pdf_page_parser = BerksPDFPageParser(page)
        for row in pdf_page_parser:
            csv_writer.writerow(row._asdict())


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdf_to_csv(PDFPageIterator(BERKS_FILE), csv.DictWriter(f, OUTPUT_HEADER))
