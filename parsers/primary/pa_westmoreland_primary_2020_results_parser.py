from collections import defaultdict, namedtuple
from io import BytesIO
from zipfile import ZipFile
import clarify
import requests
import csv
import os


CandidateData = namedtuple('CandidateData', 'precinct office district party candidate')

COUNTY = 'Westmoreland'
OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__westmoreland__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate',
                 'election_day', 'mail_in', 'absentee', 'provisional', 'votes']

CLARITY_ELECTIONS_PA_URL = 'https://results.enr.clarityelections.com/PA'
WESTMORELAND_URL = f'{CLARITY_ELECTIONS_PA_URL}/{COUNTY}/103293/255115/reports/detailxml.zip'
XML_FILENAME = 'detail.xml'

CONTEST_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT = {
    'PRESIDENTIAL ELECTORS': ('President', '', ''),
    'DEM REPRESENTATIVE IN CONGRESS 13th DISTRICT': ('U.S. House', 'DEM', 13),
    'REP REPRESENTATIVE IN CONGRESS 13th DISTRICT': ('U.S. House', 'REP', 13),
    'DEM REPRESENTATIVE IN CONGRESS 14th DISTRICT': ('U.S. House', 'DEM', 14),
    'REP REPRESENTATIVE IN CONGRESS 14th DISTRICT': ('U.S. House', 'REP', 14),
    'DEM SENATOR IN THE GENERAL ASSEMBLY 39th DISTRICT': ('State Senate', 'DEM', 39),
    'REP SENATOR IN THE GENERAL ASSEMBLY 39th DISTRICT': ('State Senate', 'REP', 39),
    'DEM SENATOR IN THE GENERAL ASSEMBLY 41st DISTRICT': ('State Senate', 'DEM', 41),
    'REP SENATOR IN THE GENERAL ASSEMBLY 41st DISTRICT': ('State Senate', 'REP', 41),
    'DEM SENATOR IN THE GENERAL ASSEMBLY 45th DISTRICT': ('State Senate', 'DEM', 45),
    'REP SENATOR IN THE GENERAL ASSEMBLY 45th DISTRICT': ('State Senate', 'REP', 45),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 33rd DISTRI': ('General Assembly', 'DEM', 33),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 33rd DISTRI': ('General Assembly', 'REP', 33),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 52nd DISTRI': ('General Assembly', 'DEM', 52),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 52nd DISTRI': ('General Assembly', 'REP', 52),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 54th DISTRI': ('General Assembly', 'DEM', 54),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 54th DISTRI': ('General Assembly', 'REP', 54),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 55th DISTRI': ('General Assembly', 'DEM', 55),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 55th DISTRI': ('General Assembly', 'REP', 55),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 56th DISTRI': ('General Assembly', 'DEM', 56),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 56th DISTRI': ('General Assembly', 'REP', 56),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 57th DISTRI': ('General Assembly', 'DEM', 57),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 57th DISTRI': ('General Assembly', 'REP', 57),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 58th DISTRI': ('General Assembly', 'DEM', 58),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 58th DISTRI': ('General Assembly', 'REP', 58),
    'DEM REPRESENTATIVE IN THE GENERAL ASSEMBLY 59th DISTRI': ('General Assembly', 'DEM', 59),
    'REP REPRESENTATIVE IN THE GENERAL ASSEMBLY 59th DISTRI': ('General Assembly', 'REP', 59),
}

CLARITY_TO_OPENELECTIONS_VOTE_TYPE = {
    'Election Day': 'election_day',
    'Absentee': 'absentee',
    'Mail-in': 'mail_in',
    'Provisional': 'provisional',
}


def should_be_recorded(result):
    if not result.choice or not result.jurisdiction:
        return False
    if any(x in result.contest.text for x in ('COMMITTEE', 'DELEGATE')):
        return False
    if result.vote_type == 'Federal':
        assert result.votes == 0
        return False
    return True


def extract_office_party_and_district(result):
    contest = result.contest.text
    if contest in CONTEST_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT:
        return CONTEST_TO_OPENELECTIONS_OFFICE_PARTY_AND_DISTRICT[contest]
    return contest.title(), '', ''


def extract_candidate_data(result):
    precinct = result.jurisdiction.name
    candidate = result.choice.text
    office, party, district = extract_office_party_and_district(result)
    assert not party or party == result.choice.party
    party = party or result.choice.party
    return CandidateData(precinct, office, district, party, candidate)


def process_result(result, candidate_data_to_votes):
    candidate_data = extract_candidate_data(result)
    vote_type = CLARITY_TO_OPENELECTIONS_VOTE_TYPE[result.vote_type]
    vote_data = candidate_data_to_votes[candidate_data]
    vote_data[vote_type] = result.votes
    vote_data['votes'] = vote_data.get('votes', 0) + result.votes


def candidate_level_data(parser):
    candidate_data_to_votes = defaultdict(dict)
    for result in parser.results:
        if should_be_recorded(result):
            process_result(result, candidate_data_to_votes)
    for candidate_data in candidate_data_to_votes:
        yield {'county': COUNTY,
               **candidate_data._asdict(),
               **candidate_data_to_votes[candidate_data]}


def precinct_level_data(parser):
    for jurisdiction in parser.result_jurisdictions:
        yield {'county': COUNTY, 'precinct': jurisdiction.name,
               'office': 'Ballots Cast', 'votes': jurisdiction.ballots_cast}
        yield {'county': COUNTY, 'precinct': jurisdiction.name,
               'office': 'Registered Voters', 'votes': jurisdiction.total_voters}


def get_westmoreland_xml_file():
    response = requests.get(WESTMORELAND_URL, stream=True)
    zipped_data = ZipFile(BytesIO(response.content))
    return zipped_data.open(XML_FILENAME)


def clarity_to_csv(parser):
    with open(OUTPUT_FILE, 'w', newline='') as f_out:
        csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
        csv_writer.writeheader()
        for row in precinct_level_data(parser):
            csv_writer.writerow(row)
        for row in candidate_level_data(parser):
            csv_writer.writerow(row)


def main():
    with get_westmoreland_xml_file() as f_in:
        parser = clarify.Parser()
        parser.parse(f_in)
        clarity_to_csv(parser)


if __name__ == "__main__":
    main()
