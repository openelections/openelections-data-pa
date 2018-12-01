import csv

source = '/Users/derekwillis/code/openelections-sources-pa/2018/Beaver PA 2018_Results_by_Precinct_EL30.htm'
offices = ['STRAIGHT PARTY', 'UNITED STATES SENATOR', 'GOVERNOR AND LIEUTENANT GOVERNOR', 'REPRESENTATIVE IN CONGRESS 17TH DISTRICT', 'SENATOR IN THE GENERAL ASSEMBLY', 'REPRESENTATIVE IN THE GEN. ASSEMBLY']

lines = open(source).readlines()
results = []

for line in lines:
    if line == '\n':
        continue
    if "<" in line:
        continue
    if "OFFICIAL  REPORT\n" in line:
        continue
    if "RUN DATE" in line:
        continue
    if "RUN TIME" in line:
        precinct = None
        continue
    if "VOTES  PERCENT" in line:
        continue
    if 'Vote for NOT MORE THAN  1' in line:
        continue
    if 'VOTER TURNOUT - TOTAL' in line:
        continue
    if any(o in line for o in offices):
        office = line.strip()
    if "           " not in line:
        if not any(o in line for o in offices):
            precinct = line.strip()
    if ".  ." in line:
        # this is a result line
        if "REGISTERED VOTERS" in line:
            office = None
            candidate = "Registered Voters"
            party = None
            votes = line.split('.  .', 1)[1].split(' ',1)[1].replace('.','').strip()
        elif "BALLOTS CAST" in line:
            office = None
            candidate = "Ballots Cast"
            party = None
            votes = line.split('.  .', 1)[1].split(' ',1)[1].replace('.','').strip()
        elif 'WRITE-IN' in line:
            candidate = 'Write-ins'
            party = None
            votes = line.split('    ', 3)[3].split('   ')[0].strip()
        else:
            candidate, party = line.split('    ', 3)[2].split(').')[0].split(' (')
            party = party[0:3]
            candidate = candidate.strip()
            votes = line.split('    ', 3)[3].split('   ')[0].strip()
        results.append(['Beaver', precinct, office, None, party, candidate, votes])

with open('20181106__pa__general__beaver__precinct.csv', 'wt') as csvfile:
    w = csv.writer(csvfile)
    headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
    w.writerow(headers)
    w.writerows(results)
