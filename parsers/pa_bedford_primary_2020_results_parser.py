import csv
import openpyxl
import os

COUNTY = 'Bedford'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__bedford__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'absentee', 'mail_in', 'provisional', 'votes']

BEDFORD_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Bedford PA Official 6.2.2020 StatementOf Votes Report by precinct '
                            'with write in\'s, absentee and provisionals.xlsx')

SHEET_NAME_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT = {
    'Pres. D': ('President', 'DEM', ''),
    'Pres. R': ('President', 'REP', ''),
    'Att. Gen. D': ('Attorney General', 'DEM', ''),
    'Att. Gen. R': ('Attorney General', 'REP', ''),
    'Aud. Gen. D': ('Auditor General', 'DEM', ''),
    'Aud. Gen. R': ('Auditor General', 'REP', ''),
    'St. Treas. D': ('State Treasurer', 'DEM', ''),
    'St. Treas. R': ('State Treasurer', 'REP', ''),
    'Rep. Congress 13th D ': ('U.S. House', 'DEM', 13),
    'Rep. Congress 13th R': ('U.S. House', 'REP', 13),
    'Sen. G A 35th D': ('State Senate', 'DEM', 35),
    'Sen. G A 35th R': ('State Senate', 'REP', 35),
    'Rep. G A 78th D': ('General Assembly', 'DEM', 78),
    'Rep. G A 78th R': ('General Assembly', 'REP', 78),
    'Rep. G A 69th D': ('General Assembly', 'DEM', 69),
    'Rep. G A  69th R': ('General Assembly', 'REP', 69),
}

VOTES_CAST_SHEET = 'Sheet1'
TOTAL_VOTES_STRING = 'Total Votes'
PARTIES = ('(REP)', '(DEM)')
WRITE_IN_CANDIDATE = 'Write-in'

FIRST_PRECINCTS = ('0101 Bedford Borough East Ward', '0701 Cumberland Valley Township')
TOTAL_VOTES_CAST_PRECINCT = 'BEDFORD COUNTY - Total'
CUMULATIVE_PRECINCT = 'Cumulative'

CANDIDATE_ROW = 4
TOTAL_VOTES_PRECINCT_START_ROW = 10
MAX_ROW = 65536

VOTE_TYPE_ROWS = [
    'election_day',
    'mail_in',
    'absentee',
    'provisional',
    'votes'
]

PRECINCT_COLUMN = 1
REGISTERED_VOTERS_COLUMN = 2
VOTES_CAST_COLUMN = 4
CANDIDATE_START_COLUMN = 6
MERGED_COLUMNS = (11, 16)


class XLSXSheetParser:
    def __init__(self, sheet):
        self._sheet = sheet
        self._title = sheet.title

    def _get_cell_value(self, row, column):
        return self._sheet.cell(row=row, column=column).value


class OfficeXLSXSheetParser(XLSXSheetParser):
    def __init__(self, sheet):
        super().__init__(sheet)
        self._office, self._party, self._district = \
            SHEET_NAME_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT[self._title]

    def __iter__(self):
        for row in self._process_sheet():
            row.update(office=self._office, party=self._party, district=self._district)
            yield row

    def _process_sheet(self):
        candidates = list(self._get_candidates())
        first_row_index = self._find_first_precinct_row()
        for row_index in range(first_row_index, MAX_ROW, len(VOTE_TYPE_ROWS) + 3):
            precinct = self._get_cell_value(row_index, PRECINCT_COLUMN)
            if precinct == CUMULATIVE_PRECINCT:
                break
            for row in self._process_precinct_row(row_index, candidates):
                row.update(precinct=precinct)
                yield row

    def _get_candidates(self):
        column_index = CANDIDATE_START_COLUMN
        candidate = None
        while candidate != TOTAL_VOTES_STRING:
            candidate = self._get_cell_value(CANDIDATE_ROW, column_index)
            if candidate:
                candidate, *party = candidate.split('\n')
                assert not party or party[0].strip() in PARTIES
                yield candidate.strip()
            column_index += 1

    def _find_first_precinct_row(self):
        for row_index in range(1, MAX_ROW):
            precinct = self._get_cell_value(row_index, PRECINCT_COLUMN)
            if precinct in FIRST_PRECINCTS:
                return row_index
        raise IndexError

    def _process_precinct_row(self, row_index, candidates):
        non_writein_votes = 0
        for candidate_index, candidate in enumerate(candidates):
            votes = self._get_votes(row_index + len(VOTE_TYPE_ROWS) + 1, candidate_index)
            if candidate == TOTAL_VOTES_STRING:
                write_in_votes = votes - non_writein_votes
                yield {'candidate': WRITE_IN_CANDIDATE, 'votes': write_in_votes}
                break
            non_writein_votes += votes
            yield {'candidate': candidate, 'votes': votes}

    def _get_votes(self, row_index, candidate_index):
        column_index = CANDIDATE_START_COLUMN + candidate_index * 2
        for merged_column_index in MERGED_COLUMNS:
            if column_index < merged_column_index:
                break
            column_index += 1
        return int(self._get_cell_value(row_index, column_index))


class VotesCastXLSXSheetParser(XLSXSheetParser):
    def __iter__(self):
        for row_index in range(TOTAL_VOTES_PRECINCT_START_ROW, MAX_ROW, len(VOTE_TYPE_ROWS) + 1):
            precinct = self._get_cell_value(row_index, PRECINCT_COLUMN)
            if precinct == TOTAL_VOTES_CAST_PRECINCT:
                break
            votes_cast_row = self._get_votes_cast_row(row_index)
            registered_voters_row = self._get_registered_voters_row(row_index)
            for row in (votes_cast_row, registered_voters_row):
                row.update(precinct=precinct)
                yield row

    def _get_votes_cast_row(self, row_index):
        votes_cast_row = {'office': 'Votes Cast'}
        for row_offset, field in enumerate(VOTE_TYPE_ROWS):
            votes_cast_row[field] = self._get_cell_value(row_index + row_offset + 1, VOTES_CAST_COLUMN)
        return votes_cast_row

    def _get_registered_voters_row(self, row_index):
        registered_voters = int(self._get_cell_value(row_index + 1, REGISTERED_VOTERS_COLUMN))
        return {'office': 'Registered Voters', 'votes': registered_voters}


def iterate_workbook(workbook):
    for sheet in workbook.worksheets:
        if sheet.title in SHEET_NAME_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT:
            print(f'Processing sheet `{sheet.title}`')
            yield from OfficeXLSXSheetParser(sheet)
        elif sheet.title == VOTES_CAST_SHEET:
            print(f'Processing votes cast sheet')
            yield from VotesCastXLSXSheetParser(sheet)
        else:
            print(f'Skipping sheet `{sheet.title}`')


def xlsx_to_csv(workbook, csv_writer):
    csv_writer.writeheader()
    for row in iterate_workbook(workbook):
        row.update(county=COUNTY)
        csv_writer.writerow(row)


def main():
    with open(OUTPUT_FILE, 'w', newline='') as f:
        xlsx_to_csv(openpyxl.load_workbook(BEDFORD_FILE),
                    csv.DictWriter(f, OUTPUT_HEADER))


if __name__ == "__main__":
    main()
