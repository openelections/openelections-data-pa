import csv

source = '/Users/derekwillis/code/openelections-sources-pa/2018/Butler PA 2018 General EL30A.htm'
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
    if "NOVEMBER 6, 2018" in line:
        continue
    if "RUN DATE" in line:
        continue
    if "REPORT-EL30A" in line:
        precinct = None
        continue
    if "TOTAL VOTES" in line:
        continue
    if 'VOTE FOR NO MORE THAN' in line:
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
            election_day, absentee, provisional = ["", "", ""]
        elif "BALLOTS CAST" in line:
            office = None
            candidate = "Ballots Cast"
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 5:
                fill, votes, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        elif 'WRITE-IN' in line:
            candidate = 'Write-ins'
            party = None
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 5:
                fill, votes, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        else:
            candidate, party = line.split('(')
            party = party[0:3]
            candidate = candidate.strip()
            if len([x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']) == 5:
                fill, votes, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
            else:
                fill, votes, pct, election_day, absentee, provisional = [x.strip() for x in line.split('.  .', 1)[1].split(' ',1)[1].split('   ') if x !='']
        results.append(['Beaver', precinct, office, None, party, candidate, votes.replace(',','').strip(), election_day.replace(',','').strip(), absentee.replace(',','').strip(), provisional.replace(',','').strip()])

with open('20181106__pa__general__butler__precinct.csv', 'wt') as csvfile:
    w = csv.writer(csvfile)
    headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes', 'election_day', 'absentee', 'provisional']
    w.writerow(headers)
    w.writerows(results)
