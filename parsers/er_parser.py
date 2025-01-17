import json
import csv

# Load the JSON file
with open('/Users/dwillis/Downloads/export-2024GeneralElection.json', 'r') as json_file:
    data = json.load(json_file)

# Extract relevant data
election_name = data.get("electionName", "Election Results")
results = data.get("results", {})
ballot_items = results.get("ballotItems", [])

# Prepare CSV data
csv_data = []
header = [
    "Precinct ID", "Precinct Name", "Office", "Ballot Item Name", 
    "Candidate Name", "Party", "Total Votes", 
    "Election Day Voting", "Mail Voting", "Provisional Voting"
]
csv_data.append(header)

for ballot_item in ballot_items:
    office = ballot_item.get("name")
    ballot_item_name = ballot_item.get("name")
    for candidate in ballot_item.get("ballotOptions", []):
        candidate_name = candidate.get("name")
        party = candidate.get("politicalParty", "N/A")
        for precinct in candidate.get("precinctResults", []):
            precinct_id = precinct.get("id")
            precinct_name = precinct.get("name")
            total_votes = precinct.get("voteCount", 0)
            group_results = {g["groupName"]: g["voteCount"] for g in precinct.get("groupResults", [])}
            election_day_votes = group_results.get("Election Day Voting", 0)
            av_votes = group_results.get("Mail Voting", 0)
            early_votes = group_results.get("Provisional Voting", 0)

            row = [
                precinct_id, precinct_name, office, ballot_item_name, 
                candidate_name, party, total_votes, 
                election_day_votes, av_votes, early_votes
            ]
            csv_data.append(row)

# Write to a CSV file
output_file = 'precinct_results_with_office.csv'
with open(output_file, 'w', newline='') as csv_file:
    writer = csv.writer(csv_file)
    writer.writerows(csv_data)

print(f"CSV file has been saved to {output_file}")
