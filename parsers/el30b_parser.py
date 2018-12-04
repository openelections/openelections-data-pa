import csv

source = '/Users/derekwillis/code/openelections-sources-pa/2018/Westmoreland PA 2018-General-PrecinctSummary.htm'
offices = ['STRAIGHT PARTY', 'UNITED STATES SENATOR', 'GOVERNOR', 'REPRESENTATIVE IN CONGRESS', 'SENATOR IN THE GENERAL ASSEMBLY', 'REPRESENTATIVE IN THE GENERAL ASSEMBLY']

lines = open(source).readlines()
results = []

for line in lines:
    if line == '\n':
        continue
    if line == 'DISTRICT\n':
        continue
    if "<" in line:
        continue
    if "PREC REPORT-GROUP DETAIL" in line:
        continue
    if "General Election" in line:
        continue
    if "NOVEMBER 6, 2018" in line:
        continue
    if "Run Date" in line:
        precinct = None
        continue
    if "TOTAL VOTES" in line:
        continue
    if 'NOT MORE THAN' in line:
        continue
    if 'VOTER TURNOUT - TOTAL' in line:
        continue
    if any(o in line for o in offices):
        office = line.strip()
    if not ".  ." in line and not any(o in line for o in offices):
        precinct = line.strip()
    if ".  ." in line:
        if "REGISTERED VOTERS" in line:
            office = None
            candidate = "Registered Voters"
            party = None
            votes = line.split('.  .', 1)[1].split(' ',1)[1].replace('.','').strip()
            election_day, absentee, emergency, provisional, federal = ["", "", "", "", ""]
        elif "BALLOTS CAST" in line:
            office = None
            candidate = "Ballots Cast"
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 7:
                fill, votes, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        elif 'WRITE-IN' in line:
            candidate = 'Write-ins'
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 7:
                fill, votes, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        elif 'Total' in line:
            continue
        elif 'Over Votes' in line:
            candidate = 'Over Votes'
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 7:
                fill, votes, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        elif 'Under Votes' in line:
            candidate = 'Under Votes'
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 7:
                fill, votes, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        else:
            candidate, party = line.split('(')
            party = party[0:3]
            candidate = candidate.strip()
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 7:
                fill, votes, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, emergency, provisional, federal = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        results.append(['Perry', precinct, office, None, party, candidate, votes.replace(',','').strip(), election_day.replace(',','').strip(), absentee.replace(',','').strip(), emergency.replace(',','').strip(), provisional.replace(',','').strip(), federal.replace(',','').strip()])

with open('20181106__pa__general__westmoreland__precinct.csv', 'wt') as csvfile:
    w = csv.writer(csvfile)
    headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes', 'election_day', 'absentee', 'emergency', 'provisional', 'federal']
    w.writerow(headers)
    w.writerows(results)
