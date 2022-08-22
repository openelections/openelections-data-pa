import os
import glob
import csv

year = '2020'
election = '20201103'
path = election+'*precinct.csv'
output_file = election+'__pa__general__precinct.csv'

def generate_headers(year, path):
    os.chdir(year)
    os.chdir('counties')
    vote_headers = []
    for fname in glob.glob(path):
        print(fname)
        with open(fname, "r") as csvfile:
            reader = csv.reader(csvfile)
            headers = next(reader)
            print(set(list(h for h in headers if h not in ['county','precinct', 'office', 'district', 'candidate', 'party'])))
            #vote_headers.append(h for h in headers if h not in ['county','precinct', 'office', 'district', 'candidate', 'party'])
#    with open('vote_headers.csv', "w") as csv_outfile:
#        outfile = csv.writer(csv_outfile)
#        outfile.writerows(vote_headers)

def generate_offices(year, path):
    os.chdir(year)
    os.chdir('counties')
    offices = []
    for fname in glob.glob(path):
        with open(fname, "r") as csvfile:
            print(fname)
            reader = csv.DictReader(csvfile)
            for row in reader:
                if not row['office'] in offices:
                    offices.append(row['office'])
    with open('offices.csv', "w") as csv_outfile:
        outfile = csv.writer(csv_outfile)
        outfile.writerows(offices)

def generate_consolidated_file(year, path, output_file):
    results = []
    os.chdir(year)
    os.chdir('counties')
    for fname in glob.glob(path):
        with open(fname, "r") as csvfile:
            print(fname)
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['office'].strip() in ['Straight Party', 'President', 'Governor', 'Secretary of State', 'Railroad Commissioner', 'State Auditor', 'Auditor General', 'State Treasurer', 'Commissioner of Agriculture & Commerce', 'Commissioner of Insurance', 'Attorney General', 'U.S. House', 'State Senate', 'State House', 'U.S. Senate', 'House of Delegates', 'State Representative', 'Registered Voters', 'Ballots Cast', 'Ballots Cast Blank']:
                    if 'absentee' in row:
                        absentee = row['absentee']
                    else:
                        absentee = None
                    if 'mail' in row:
                        mail = row['mail']
                    else:
                        mail = None
                    if 'election_day' in row:
                        election_day = row['election_day']
                    else:
                        election_day = None
                    if 'provisional' in row:
                        provisional = row['provisional']
                    else:
                        provisional = None
                    if 'military' in row:
                        military = row['military']
                    else:
                        military = None
                    if 'extra' in row:
                        extra = row['extra']
                    else:
                        extra = None
                    results.append([row['county'], row['precinct'], row['office'], row['district'], row['candidate'], row['party'], row['votes'], election_day, absentee, mail, provisional, military, extra])
    os.chdir('..')
    os.chdir('..')
    with open(output_file, "w") as csv_outfile:
        outfile = csv.writer(csv_outfile)
        outfile.writerow(['county','precinct', 'office', 'district', 'candidate', 'party', 'votes', 'election_day', 'absentee', 'mail', 'provisional', 'military', 'extra'])
        outfile.writerows(results)
