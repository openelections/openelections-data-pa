import csv
import os
import requests
from lxml import html
from time import sleep


COUNTY = 'Lancaster'

OUTPUT_FILE = os.path.join('..', '2020', '20200602__pa__primary__lancaster__precinct.csv')
OUTPUT_HEADER = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

QUERY_SPACING_IN_SECONDS = 30  # don't spam requests; total process should be <50 queries

LANCASTER_ELECTIONS_URL = 'http://vr.co.lancaster.pa.us/ElectionReturns'
LANCASTER_PRIMARY_2020_RESULTS_URL = LANCASTER_ELECTIONS_URL + '/June_2,_2020_-_General_Primary/{}ByPrecinct.html'

PRIMARY_2020_FIRST_CONTEST_ID = 250
PRIMARY_2020_LAST_CONTEST_ID = 284

WRITE_IN_CANDIDATE = 'Write-in'

CONTEST_TO_OPENELECTIONS_OFFICE_AND_DISTRICT = {
    'PRESIDENT OF THE UNITED STATES': ('President', ''),
    'REPRESENTATIVE IN CONGRESS 11th District': ('U.S. House', 11),
    'SENATOR IN THE GENERAL ASSEMBLY 13th District': ('State Senate', 13),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 13th District': ('General Assembly', 13),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 37th District': ('General Assembly', 37),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 41st District': ('General Assembly', 41),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 43rd District': ('General Assembly', 43),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 96th District': ('General Assembly', 96),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 97th District': ('General Assembly', 97),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 98th District': ('General Assembly', 98),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 99th District': ('General Assembly', 99),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 100th District': ('General Assembly', 100),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 128th District': ('General Assembly', 128),
    'REPRESENTATIVE IN THE GENERAL ASSEMBLY 129th District': ('General Assembly', 129),
}


def scrape_lancaster():
    for html_tree in lancaster_html_trees():
        _, _, contest_table, votes_table, _, _ = html_tree.xpath('//table')
        party = html_tree.xpath('//img/@title')[0][:3].upper()
        office, district = extract_office_and_district(contest_table)
        for row in process_votes_table(votes_table):
            row.update(office=office, party=party, district=district)
            yield row
        sleep(QUERY_SPACING_IN_SECONDS)


def process_votes_table(votes_table):
    votes_row_iter = iter(votes_table.xpath('.//tr'))
    candidates = next(votes_row_iter).xpath('.//td/text()')[:-2] + [WRITE_IN_CANDIDATE]
    next(votes_row_iter)  # skip write-in and total clues row
    for row in process_votes_rows(votes_row_iter, candidates):
        yield row


def lancaster_html_trees():
    for contest_id in range(PRIMARY_2020_FIRST_CONTEST_ID, PRIMARY_2020_LAST_CONTEST_ID):
        print(f'Processsing contest {contest_id}')
        response = requests.get(LANCASTER_PRIMARY_2020_RESULTS_URL.format(contest_id))
        yield html.fromstring(response.content.decode("utf-8"))


def process_votes_rows(votes_row_iter, candidates):
    for row in votes_row_iter:
        precinct, total_votes_cell_value = row.xpath('.//th/text()')
        if precinct == 'Totals':
            break
        total_votes = 0
        vote_cells = row.xpath('.//td')
        for candidate, vote_cell in zip(candidates, vote_cells):
            votes = extract_votes(vote_cell)
            total_votes += votes
            yield {'county': COUNTY, 'precinct': precinct, 'candidate': candidate, 'votes': votes}
        assert int(total_votes_cell_value) == total_votes


def extract_office_and_district(contest_table):
    contest = contest_table.xpath('.//td/text()')[0]
    if contest in CONTEST_TO_OPENELECTIONS_OFFICE_AND_DISTRICT:
        return CONTEST_TO_OPENELECTIONS_OFFICE_AND_DISTRICT[contest]
    return contest.title(), ''


def extract_votes(vote_cell):
    votes_string = vote_cell.xpath('.//text()')
    if votes_string:
        votes_string = votes_string[0]
        if votes_string == '\xa0':
            votes_string = '0'
    return int(votes_string or '0')


def main():
    with open(OUTPUT_FILE, 'w', newline='') as f_out:
        csv_writer = csv.DictWriter(f_out, OUTPUT_HEADER)
        csv_writer.writeheader()
        for row in scrape_lancaster():
            csv_writer.writerow(row)


if __name__ == "__main__":
    main()
