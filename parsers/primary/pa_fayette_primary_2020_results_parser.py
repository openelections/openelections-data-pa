import csv
import openpyxl
import os

COUNTY = 'Fayette'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__fayette__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

FAYETTE_FILE = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                            'Fayette PA Primary PrecinctStatementOfVotesCastRPT.xlsx')


SHEET_NAME_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT = {
    'Dem President': ('President', 'DEM', ''),
    'Rep President': ('President', 'REP', ''),
    'Dem Attorney General': ('Attorney General', 'DEM', ''),
    'Rep Attorney General': ('Attorney General', 'REP', ''),
    'Dem Auditor General': ('Auditor General', 'DEM', ''),
    'Rep Auditor General': ('Auditor General', 'REP', ''),
    'Dem State Treasurer': ('State Treasurer', 'DEM', ''),
    'Rep State Treasurer': ('State Treasurer', 'REP', ''),
    'Dem 14th Dist Rep. in Congress': ('U.S. House', 'DEM', 14),
    'Rep 14th Dist Rep in Congress': ('U.S. House', 'REP', 14),
    'SENATOR IN THE GENERAL ASSEMBLY - D15 (DEM)': ('State Senate', 'DEM', 15),
    'SENATOR IN THE GENERAL ASSEMBLY - R15 (REP)': ('State Senate', 'REP', 15),
    'Dem 49th Rep in General Assembl': ('General Assembly', 'DEM', 49),
    'Dem 50th Rep in General Assembl': ('General Assembly', 'DEM', 50),
    'Dem 51st Rep in General Assembl': ('General Assembly', 'DEM', 51),
    'Dem 52nd Rep in General Assembl': ('General Assembly', 'DEM', 52),
    'Rep 49th Rep in General Assembl': ('General Assembly', 'REP', 49),
    'Rep 50th Rep in General Assembl': ('General Assembly', 'REP', 50),
    'Rep 51st Rep in General Assembl': ('General Assembly', 'REP', 51),
    'Rep 52nd Rep in General Assembl': ('General Assembly', 'REP', 52),
}

VOTES_CAST_SHEET = 'Total Votes casted'
TOTAL_VOTES_STRING = 'Total Votes'
TOTALS_PRECINCT = 'Fayette - Total'
PARTIES = ('(REP)', '(DEM)')
WRITE_IN_CANDIDATE = 'Write-in'

CANDIDATE_ROW = 4
PRECINCT_START_ROW = 7
TOTAL_VOTES_PRECINCT_START_ROW = 16
MAX_ROW = 65536

PRECINCT_COLUMN = 1
REGISTERED_VOTERS_COLUMN = 2
VOTES_CAST_COLUMN = 6
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
        for row_index in range(PRECINCT_START_ROW, MAX_ROW):
            precinct = self._get_cell_value(row_index, PRECINCT_COLUMN)
            if precinct == TOTALS_PRECINCT:
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

    def _process_precinct_row(self, row_index, candidates):
        non_writein_votes = 0
        for candidate_index, candidate in enumerate(candidates):
            votes = self._get_votes(row_index, candidate_index)
            if candidate == TOTAL_VOTES_STRING:
                votes = self._get_votes(row_index, candidate_index)
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
        for row_index in range(TOTAL_VOTES_PRECINCT_START_ROW, MAX_ROW):
            precinct = self._get_cell_value(row_index, PRECINCT_COLUMN)
            if precinct == TOTALS_PRECINCT:
                break
            registered_voters = int(self._get_cell_value(row_index, REGISTERED_VOTERS_COLUMN))
            votes_cast = int(self._get_cell_value(row_index, VOTES_CAST_COLUMN))
            yield {'precinct': precinct, 'office': 'Registered Voters', 'votes': registered_voters}
            yield {'precinct': precinct, 'office': 'Votes Cast', 'votes': votes_cast}


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
        xlsx_to_csv(openpyxl.load_workbook(FAYETTE_FILE),
                    csv.DictWriter(f, OUTPUT_HEADER))


if __name__ == "__main__":
    main()
