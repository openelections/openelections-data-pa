import csv
import os
from parsers.pa_pdf_parser import PDFPageIterator
from parsers.electionware_parser import ElectionwarePDFStringIterator, \
    ElectionwarePDFTableParser, ElectionwarePDFPageParser

COUNTY = 'Cumberland'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__cumberland__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'provisional', 'military', 'votes']

CUMBERLAND_PARTIES = {'Republican', 'Democratic'}
CUMBERLAND_FILE_FORMAT = os.path.join('..', '..', 'openelections-sources-pa', '2020',
                                      'Cumberland PA Primary Precinct Report {}.pdf')

CUMBERLAND_HEADER = [
    '',
    'Precinct Results Report',
    '2020 GENERAL PRIMARY',
    'June 2, 2020',
    '{party}',
    'Cumberland County',
]

TABLE_HEADER = [
    'TOTAL',
    'ELECTION DAY',
    'ABSENTEE/ MAIL-IN',
    'PROVISIONA L',
    'MILITARY',
]
TABLE_HEADER_VARIANT = [
    'TOTAL',
    'ELECTION DAY',
    'MILITARY',
    'PROVISION AL',
    'ABSENTEE/ MAIL-IN',
]
EXPECTED_TABLE_HEADERS = (' '.join(TABLE_HEADER), ' '.join(TABLE_HEADER_VARIANT))

OPENELECTIONS_MAPPED_HEADER = [
    'votes',
    'election_day',
    'mail_in',
    'provisional',
    'military',
]

FIRST_FOOTER_SUBSTRING = 'Precinct Report'
SECOND_FOOTER_SUBSTRING = 'Report generated with Electionware'

RAW_OFFICE_TO_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 10TH DISTRICT': ('U.S. House', 10),
    'REPRESENTATIVE IN CONGRESS 13TH DISTRICT': ('U.S. House', 13),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 193RD REPRESENTATIVE DISTRICT': ('General Assembly', 193),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 199TH REPRESENTATIVE DISTRICT': ('General Assembly', 199),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 86TH REPRESENTATIVE DISTRICT': ('General Assembly', 86),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 87TH REPRESENTATIVE DISTRICT': ('General Assembly', 87),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 88TH REPRESENTATIVE DISTRICT': ('General Assembly', 88),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 92ND REPRESENTATIVE DISTRICT': ('General Assembly', 92),
    'SENATOR IN THE GENERAL ASSEMBLY 31ST SENATORIAL DISTRICT': ('State Senate', 31),
    'SENATOR IN THE GENERAL ASSEMBLY 33RD SENATORIAL DISTRICT': ('State Senate', 33),
}


class CumberlandPDFStringIterator(ElectionwarePDFStringIterator):
    _first_footer_substring = FIRST_FOOTER_SUBSTRING
    _second_footer_substring = SECOND_FOOTER_SUBSTRING


class CumberlandPDFTableParser(ElectionwarePDFTableParser):
    _county = COUNTY
    _expected_table_headers = EXPECTED_TABLE_HEADERS
    _openelections_mapped_header = OPENELECTIONS_MAPPED_HEADER
    _raw_office_to_office_and_district = RAW_OFFICE_TO_OFFICE_AND_DISTRICT

    @classmethod
    def _should_be_recorded(cls, row):
        if not super()._should_be_recorded(row):
            return False
        if 'Committee' in row['office'] or 'Delegate' in row['office']:
            return False
        return True

    @classmethod
    def _clean_row(cls, row):
        super()._clean_row(row)
        row['office'] = row['office'].title()
        row['candidate'] = row['candidate'].title()


class CumberlandPDFPageParser(ElectionwarePDFPageParser):
    _pdf_string_iterator_clazz = CumberlandPDFStringIterator
    _pdf_table_parser_clazz = CumberlandPDFTableParser

    def __init__(self, page, party):
        self._party_header = [x if x != '{party}' else party for x in CUMBERLAND_HEADER]
        super().__init__(page)

    def _verify_header(self):
        header = [next(self._string_iterator) for _ in range(len(self._party_header))]
        assert header == self._party_header


def append_pdf_to_csv(pdf_page_iterator, csv_writer, party):
    party_abbrev = party[:3].upper()
    for page in pdf_page_iterator:
        print(f'processing {party_abbrev} pdf, page {page.get_page_number()}')
        pdf_page_parser = CumberlandPDFPageParser(page, party.upper())
        for row in pdf_page_parser:
            if row['party']:
                assert row['party'] == party_abbrev
            else:
                assert row['office'] in ('Registered Voters', 'Ballots Cast')
                if party_abbrev == 'DEM':
                    continue  # processed once already, on REP side
            csv_writer.writerow(row)


def pdfs_to_csv(csv_writer):
    csv_writer.writeheader()
    for party in CUMBERLAND_PARTIES:
        cumberland_file = CUMBERLAND_FILE_FORMAT.format(party)
        pdf_page_iterator = PDFPageIterator(cumberland_file)
        append_pdf_to_csv(pdf_page_iterator, csv_writer, party)


if __name__ == "__main__":
    with open(OUTPUT_FILE, 'w', newline='') as f:
        pdfs_to_csv(csv.DictWriter(f, OUTPUT_HEADER))
