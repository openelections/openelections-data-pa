import csv
import os
from pdfreader import SimplePDFViewer, PageDoesNotExist

COUNTY = 'York'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__york__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

YORK_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                         'York PA June 2 Elections Results.pdf')
YORK_HEADER = [
    'Results per Precinct',
    'York Primary 2020',
    'Official',
    '2020-06-17 10:07:09',
]
PARTIES = {
    'DEM',
    'REP',
}

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'President of the United States': ('President', ''),
    'Representative in Congress (10th Congressional District)': ('U.S. House', 10),
    'Representative in Congress (11th Congressional District)': ('U.S. House', 11),
    'Senator in the General Assembly (District 31)': ('State Senate', 31),
    'Senator in the General Assembly (District 33)': ('State Senate', 33),
    'Representative in the General Assembly (District 47)': ('General Assembly', 47),
    'Representative in the General Assembly (District 92)': ('General Assembly', 92),
    'Representative in the General Assembly (District 93)': ('General Assembly', 93),
    'Representative in the General Assembly (District 94)': ('General Assembly', 94),
    'Representative in the General Assembly (District 95)': ('General Assembly', 95),
    'Representative in the General Assembly (District 169)': ('General Assembly', 169),
    'Representative in the General Assembly (District 196)': ('General Assembly', 196),
}

FIRST_WORD_FOR_FIRST_PRECINCTS = ('Chanceford ', 'Codorus ', 'Conewago ', 'Dover ', 'East ')
TOTALS_PRECINCT = 'Total'
VALID_PRECINCTS = {
    'Carroll Township',
    'Chanceford Township 1',
    'Chanceford Township 2',
    'Chanceford Township 3',
    'Codorus Township 1',
    'Codorus Township 2',
    'Codorus Township 3',
    'Conewago Township 1',
    'Conewago Township 2',
    'Cross Roads Borough',
    'Dallastown Borough 1',
    'Dallastown Borough 2',
    'Delta Borough',
    'Dillsburg Borough',
    'Dover Borough',
    'Dover Township 1',
    'Dover Township 2',
    'Dover Township 3',
    'Dover Township 4',
    'East Hopewell Township',
    'East Manchester Township',
    'East Prospect Borough',
    'Fairview Township 1',
    'Fairview Township 2',
    'Fairview Township 3',
    'Fairview Township 4',
    'Fawn Grove Borough',
    'Fawn Township',
    'Felton Borough',
    'Franklin Township',
    'Franklintown Borough',
    'Glen Rock Borough',
    'Goldsboro Borough',
    'Hallam Borough',
    'Hanover Borough 1',
    'Hanover Borough 2',
    'Hanover Borough 3',
    'Hanover Borough 4',
    'Hanover Borough 5 ',
    'Heidelberg Township',
    'Hellam Township 1',
    'Hellam Township 2',
    'Hopewell Township',
    'Jackson Township 1',
    'Jackson Township 2',
    'Jacobus Borough',
    'Jefferson Borough',
    'Lewisberry Borough',
    'Loganville Borough',
    'Lower Chanceford Township 1',
    'Lower Chanceford Township 2',
    'Lower Windsor Township 1',
    'Lower Windsor Township 2',
    'Lower Windsor Township 3',
    'Manchester Borough',
    'Manchester Township 1',
    'Manchester Township 2',
    'Manchester Township 3',
    'Manchester Township 4',
    'Manchester Township 5',
    'Manchester Township 6',
    'Manchester Township 7',
    'Manheim Township',
    'Monaghan Township',
    'Mount Wolf Borough',
    'New Freedom Borough',
    'New Salem Borough',
    'Newberry Township 1',
    'Newberry Township 2',
    'Newberry Township 3',
    'North Codorus Township 1',
    'North Codorus Township 2',
    'North Hopewell Township',
    'North York Borough',
    'Paradise Township',
    'Peach Bottom Township',
    'Penn Township 1',
    'Penn Township 2',
    'Penn Township 3',
    'Penn Township 4',
    'Railroad Borough',
    'Red Lion Borough 1',
    'Red Lion Borough 2',
    'Red Lion Borough 3',
    'Seven Valleys Borough',
    'Shrewsbury Borough',
    'Shrewsbury Township 1',
    'Shrewsbury Township 2',
    'Spring Garden Township 1',
    'Spring Garden Township 2',
    'Spring Garden Township 3',
    'Spring Garden Township 4',
    'Spring Garden Township 5',
    'Spring Grove Borough',
    'Springettsbury Township 1',
    'Springettsbury Township 2',
    'Springettsbury Township 3',
    'Springettsbury Township 4',
    'Springettsbury Township 5',
    'Springettsbury Township 6',
    'Springettsbury Township 7',
    'Springettsbury Township 8',
    'Springfield Township',
    'Stewartstown Borough',
    'Warrington Township',
    'Washington Township',
    'Wellsville Borough',
    'West Manchester Township 1',
    'West Manchester Township 2',
    'West Manchester Township 3',
    'West Manchester Township 4',
    'West Manchester Township 5',
    'West Manheim Township 1',
    'West Manheim Township 2',
    'West Manheim Township 3',
    'West York Borough 1',
    'West York Borough 2',
    'Windsor Borough',
    'Windsor Township 1',
    'Windsor Township 2',
    'Windsor Township 3',
    'Windsor Township 4',
    'Winterstown Borough',
    'Wrightsville Borough 1',
    'Wrightsville Borough 2',
    'Wrightsville Borough 3',
    'Yoe Borough',
    'York City 1-0',
    'York City 11-0',
    'York City 12-1',
    'York City 12-2',
    'York City 12-3',
    'York City 12-4',
    'York City 13-0',
    'York City 14-1',
    'York City 14-2',
    'York City 14-3',
    'York City 15-0',
    'York City 5-0',
    'York City 6-0',
    'York City 7-0',
    'York City 8-0',
    'York City 9-1',
    'York City 9-2',
    'York Haven Borough',
    'York Township 1-1',
    'York Township 1-2',
    'York Township 1-3',
    'York Township 2-1',
    'York Township 2-2',
    'York Township 2-3',
    'York Township 3-1',
    'York Township 3-2',
    'York Township 3-3',
    'York Township 4-1',
    'York Township 4-2',
    'York Township 4-3',
    'York Township 5-1',
    'York Township 5-2',
    'York Township 5-3',
    'Yorkana Borough',
    TOTALS_PRECINCT,
}

MULTILINE_CANDIDATES = {
    'Aidan T.|Stonegifer',
    'Alex|Kelly',
    'Allen|Westly',
    'Amy|Landis',
    'Angela|Coco',
    'Angela|Culpepper',
    'Anna|CORBIN',
    'Anne|Olsen',
    'Ben|Krantler',
    'Brian|A.|Davis ',
    'Brien|Jesse|Krebs',
    'Bryan Hower -|DEM',
    'Calvin|Emery',
    'Catherine|G.|Hardee',
    'Chad|Brindle',
    'Colin|Kowalewski',
    'Dan|Regner',
    'Daniel Doubet|- DEM',
    'Daniel|Kauffman',
    'Daniel|Risser',
    'David Vitale -|DEM',
    'Delma|Rivera-|Lytle',
    'Dennis|Myers',
    'Derrick|Ferree',
    'Diana E.|Kauffman',
    'Dorothea|Beloher',
    'Doug|Walker',
    'Drue Cappawana -|DEM',
    'Dwight|Sanderson',
    'Earl|White',
    'Edward|Bell',
    'Edward|Harvey ',
    'Eliza Booth -|DEM',
    'Elliot|Patrilla',
    'Eric C.|Hartman',
    'Eric|Samus',
    'Eugene|Depasquale',
    'Fred|Owens',
    'Gary|John|Hades|Jr.',
    'George|Landis|III',
    'Gerald|C.|Uniacle',
    'Gregory|Kossick',
    'Harry E.|Perkinson',
    'Jack|Dodderweich ',
    'Jacob|Ross',
    'james|Park|Anderson',
    'Janelle|Whirley',
    'Jason|Beinhower',
    'Jason|Querry',
    'Jason|Smith ',
    'Jeffrey|Heist',
    'Jeffrey|Stover',
    'Jenna|Geesey',
    'Jeremy|Mondok',
    'Jeremy|Spahr',
    'Jessica|Gonzales-|Rojas',
    'Jim|Sanders',
    'John T.|Spangler',
    'John|Bosh',
    'John|Yost',
    'Jonathan Smucker -|DEM',
    'Jonathan|Barton',
    'Jordan|Lewis',
    'Jordan|Sanderson',
    'Judith|Higgins',
    'Kate|Klunk',
    'Katie|King',
    'Keith|Engle',
    'Keith|Gillespie',
    'Kelysey|Hoke',
    'Kendra|Nabon',
    'Kevin|Schrieber',
    'Kim|Howard',
    'Kyle|Stambaugh ',
    'Lauren Edgell -|DEM',
    'Laurn|Faulkne-|Bond',
    'Leslie|Mon-|Lashway',
    'Lloyd|Smucker',
    'Logan|S|Reilly',
    'Lou|Flores',
    'Margaret|Girrdir',
    'Marie|McAndrew',
    'MaryLou|Heiser',
    'Matthew|Hacker',
    'Meghan|Stitt',
    'Melissa|Davenport',
    'Michael Fedor|- DEM',
    'Michael J.|Wascovich',
    'Michael Maguire -|DEM',
    'Michael|Dilks',
    'Mike|Jones',
    'Mike|Regan',
    'Myneca|Ojo',
    'Nancy Rohrbaugh|- DEM',
    'Nelly Torres -|DEM',
    'Nick|Smolko',
    'O.|Woodford',
    'Onah Ossai|- DEM',
    'Phillip|Klocek',
    'Raquelle|J. Lilly ',
    'Raymond|Van de|Castle',
    'Rebecca|Yoder',
    'Richard|Mylin',
    'Robert|Ayala',
    'Ronald|Ruman',
    'Ruth|Mickey',
    'Ryan Supler -|DEM',
    'Sandie|Walker',
    'Sara|Grove',
    'Sarah|Hammond',
    'Scott|Brenneman ',
    'Seth|Grove',
    'Shavonnia|Corbin-|Johnson',
    'Shelby|Ilgifritz',
    'Stan|Saylor',
    'Steve|Snell',
    'Suzanne|Gates',
    'Terence|Hemer',
    'Thais Vazquez-|Carrero - DEM',
    'Thomas Iwancio|- DEM',
    'Tia|Long',
    'Tiffany|Weaver',
    'Tim Butler -|DEM',
    'Timothy|Lawson',
    'Timothy|Strausbaugh',
    'Todd|Greer',
    'Todd|Reynolds',
    'Tom|Brier',
    'Whitney|Ortman',
    'Zakary Gregg -|DEM',
    'Write-|in',
}


def get_first_word(multiline_candidate):
    first_word = multiline_candidate.split('|', 1)[0]
    if first_word[-1] == '-' and first_word[-2] != ' ':
        return first_word
    return first_word + ' '


VALID_MULTILINE_CANDIDATES = [s.replace(' -|', ' - ').replace('-|', '-').replace('|', ' ')
                              for s in MULTILINE_CANDIDATES]
FIRST_WORD_FOR_MULTILINE_CANDIDATES = [get_first_word(s) for s in MULTILINE_CANDIDATES]


class YorkPDFStrings:
    def __init__(self):
        self._strings = []
        self._string_offset = 0

    def process_canvas_block(self, canvas):
        self._strings.append(''.join(canvas.strings[self._string_offset:]))
        self._string_offset = len(canvas.strings)

    def get_iterator(self):
        return iter(self._strings)


class YorkPDFStringIterator(SimplePDFViewer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._strings = None
        self._rendered = False
        self._page_strings_iterator = None
        self._page_number = 0

    def __iter__(self):
        return self

    def __next__(self):
        return self._next()

    def on_ET(self, op):
        super().on_ET(op)
        self._strings.process_canvas_block(self.canvas)

    def _next(self):
        self._init_page_string_iterator()
        try:
            return next(self._page_strings_iterator)
        except StopIteration:
            self._page_strings_iterator = None
            return self._next()

    def _init_page_string_iterator(self):
        if not self._page_strings_iterator:
            self._strings = YorkPDFStrings()
            self._page_number += 1
            print(f'Processing page {self._page_number}')
            self.navigate(self._page_number)
            self.render()
            self._page_strings_iterator = self._strings.get_iterator()


class YorkPDFStringParser:
    def __init__(self, string_iterator):
        self._string_iterator = string_iterator
        self._active_precinct = None
        self._validate_header()
        self._table_iterator = None

    def __iter__(self):
        return self

    def __next__(self):
        try:
            if not self._table_iterator:
                self._table_iterator = self._process_table()
            return next(self._table_iterator)
        except StopIteration:
            self._table_iterator = None
            return next(self)
        except PageDoesNotExist:
            raise StopIteration

    def _validate_header(self):
        actual_header_strings = [next(self._string_iterator) for _ in range(len(YORK_HEADER))]
        assert actual_header_strings == YORK_HEADER

    def _process_table(self):
        office = next(self._string_iterator).split(' (Vote for', 1)[0]
        office, party = self._extract_party(office)
        office, district = self._extract_district(office)
        assert next(self._string_iterator) == 'Precinct'
        candidates = list(self._iterate_candidates())
        for row in self._iterate_precincts(candidates):
            if row['precinct'] != TOTALS_PRECINCT and 'Delegate' not in office:
                row.update(county=COUNTY, office=office, party=party, district=district)
                yield row

    def _iterate_candidates(self):
        while True:
            candidate = next(self._string_iterator)
            if candidate in FIRST_WORD_FOR_MULTILINE_CANDIDATES:
                while candidate not in VALID_MULTILINE_CANDIDATES:
                    candidate += next(self._string_iterator)
            if candidate in VALID_PRECINCTS or candidate in FIRST_WORD_FOR_FIRST_PRECINCTS:
                self._active_precinct = candidate
                break
            yield candidate.split(' - ', 1)[0].strip()

    def _iterate_precincts(self, candidates):
        precinct = self._active_precinct
        self._active_precinct = None
        while True:
            while precinct not in VALID_PRECINCTS:
                precinct += next(self._string_iterator)
            for candidate in candidates:
                if candidate in ('Nick Smolko', 'Myneca Ojo', 'Daniel Kauffman'):
                    # These candidates were truncated in the PDF
                    continue
                votes = next(self._string_iterator)
                yield {'precinct': precinct, 'candidate': candidate, 'votes': int(votes.replace(',', ''))}
            if precinct == TOTALS_PRECINCT:
                break
            precinct = ''

    @staticmethod
    def _extract_party(office):
        for party in PARTIES:
            if f' ({party})' in office:
                return office.replace(f' ({party})', ''), party
        return office, ''

    @staticmethod
    def _extract_district(office):
        if office in RAW_OFFICE_TO_OFFICE_AND_DISTRICT:
            return RAW_OFFICE_TO_OFFICE_AND_DISTRICT[office]
        return office, ''


def pdf_to_csv():
    with open(YORK_FILE, 'rb') as f_in:
        york_pdf_iterator = YorkPDFStringIterator(f_in)
        york_string_parser = YorkPDFStringParser(york_pdf_iterator)
        with open(OUTPUT_FILE, 'w', newline='') as f_out:
            csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
            csv_writer.writeheader()
            for row in york_string_parser:
                csv_writer.writerow(row)


if __name__ == "__main__":
    pdf_to_csv()
