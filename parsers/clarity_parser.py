import clarify
import re
import requests
import zipfile
import csv

try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO, BytesIO

def statewide_results(url):
    j = clarify.Jurisdiction(url=url, level="state")
    r = requests.get("http://results.enr.clarityelections.com/WV/74487/207685/reports/detailxml.zip", stream=True)
    z = zipfile.ZipFile(BytesIO(r.content))
    z.extractall()
    p = clarify.Parser()
    p.parse("detail.xml")
    results = []
    for result in p.results:
        candidate = re.sub(r'^\(\d+\)\s*', '', result.choice.text)
        office, district = parse_office(result.contest.text)
        party = parse_party(result.contest.text)
        if '(' in candidate and party is None:
            if '(I)' in candidate:
                if '(I)(I)' in candidate:
                    candidate = candidate.split('(I)')[0]
                    party = 'I'
                else:
                    candidate, party = candidate.split('(I)')
                candidate = candidate.strip() + ' (I)'
            else:
                print(candidate)
                candidate, party = candidate.split('(', 1)
                candidate = candidate.strip()
            party = party.replace(')','').strip()
        if result.jurisdiction:
            county = result.jurisdiction.name
        else:
            county = None
        r = [x for x in results if x['county'] == county and x['office'] == office and x['district'] == district and x['party'] == party and x['candidate'] == candidate]
        if r:
             r[0][result.vote_type] = result.votes
        else:
            results.append({ 'county': county, 'office': office, 'district': district, 'party': party, 'candidate': candidate, result.vote_type: result.votes})

    with open("20180508__wv__general.csv", "wt") as csvfile:
        w = csv.writer(csvfile)
        w.writerow(['county', 'office', 'district', 'party', 'candidate', 'votes'])
        for row in results:
            total_votes = row['Election Day']# + row['Absentee by Mail'] + row['Advance in Person'] + row['Provisional']
            w.writerow([row['county'], row['office'], row['district'], row['party'], row['candidate'], total_votes])

def download_county_files(url, filename):
    no_xml = []
    j = clarify.Jurisdiction(url=url, level="state")
    subs = j.get_subjurisdictions()
    for sub in subs:
        try:
            r = requests.get(sub.report_url('xml'), stream=True)
            z = zipfile.ZipFile(BytesIO(r.content))
            z.extractall()
            precinct_results(sub.name.replace(' ','_').lower(),filename)
        except:
            no_xml.append(sub.name)

    print(no_xml)

def precinct_results(county_name, filename):
    f = filename + '__' + county_name + '__precinct.csv'
    p = clarify.Parser()
    p.parse("detail.xml")
    results = []
    vote_types = []
    for result in [x for x in p.results if not 'Number of Precincts' in x.vote_type]:
        vote_types.append(result.vote_type)
        if result.choice is None:
            continue
        candidate = re.sub(r'^\(\d+\)\s*', '', result.choice.text)
        office, district = parse_office(result.contest.text)
        party = result.choice.party
        if '(' in candidate and party is None:
            if '(I)' in candidate:
                if '(I)(I)' in candidate:
                    candidate = candidate.split('(I)')[0]
                    party = 'I'
                else:
                    candidate, party = candidate.split('(I)')
            else:
                candidate, party = candidate.split('(', 1)
                candidate = candidate.strip()
            party = party.replace(')','').strip()
        county = p.region
        if result.jurisdiction:
            precinct = result.jurisdiction.name
        else:
            precinct = None
        if precinct == None:
            continue
        r = [x for x in results if x['county'] == county and x['precinct'] == precinct and x['office'] == office and x['district'] == district and x['party'] == party and x['candidate'] == candidate]
        if r:
             r[0][result.vote_type] = result.votes
        else:
            results.append({ 'county': county, 'precinct': precinct, 'office': office, 'district': district, 'party': party, 'candidate': candidate, result.vote_type: result.votes})

    vote_types = list(dict.fromkeys(vote_types))
    print(vote_types)
    try:
        vote_types.remove('regVotersCounty')
    except:
        pass
    try:
        vote_types.remove('Overvotes')
    except:
        pass
    try:
        vote_types.remove('Undervotes')
    except:
        pass
    with open(f, "wt") as csvfile:
        w = csv.writer(csvfile)
        headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes'] + [x.replace(' ','_').lower() for x in vote_types]
        w.writerow(headers)
        for row in results:
            if 'Republican' in row['office']:
                row['party'] = 'REP'
            elif 'Democrat' in row['office']:
                row['party'] = 'DEM'
            total_votes = sum([row.get(k, 0) for k in vote_types if row.get(k, 0)])
            w.writerow([row['county'], row['precinct'], row['office'], row['district'], row['party'], row['candidate'], total_votes] + [row.get(k, 0) for k in vote_types])


def summary_results(county_name, filename):
    input_csv = "summary.csv"
    output_csv = filename + '__' + county_name + '__county.csv'
    county = county_name.title()
    results = []
    current_contest = None
    pending_overvotes = None
    pending_undervotes = None

    def flush_contest_extras(contest_name, overvotes, undervotes):
        if not contest_name:
            return
        if overvotes not in (None, ''):
            results.append([county, contest_name, '', '', 'Overvotes', str(overvotes)])
        if undervotes not in (None, ''):
            results.append([county, contest_name, '', '', 'Undervotes', str(undervotes)])

    with open(input_csv, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip().lower(): (v or '').strip() for k, v in row.items()}
            contest_name = row.get('contest name') or row.get('contest') or row.get('contest_name')
            candidate_name = row.get('choice name') or row.get('candidate name') or row.get('choice')
            party = row.get('party name') or row.get('party') or ''
            total_votes = (row.get('total votes') or row.get('votes') or '0').replace(',', '')
            overvotes = (row.get('over votes') or row.get('overvotes') or '').replace(',', '')
            undervotes = (row.get('under votes') or row.get('undervotes') or '').replace(',', '')

            if not contest_name or not candidate_name:
                continue

            contest_name = re.sub(r'\s*\(vote\s+for\s*\d+\)', '', contest_name, flags=re.IGNORECASE)
            contest_name = re.sub(r'\s{2,}', ' ', contest_name).strip()

            if current_contest is None:
                current_contest = contest_name
                pending_overvotes = overvotes
                pending_undervotes = undervotes
            elif contest_name != current_contest:
                flush_contest_extras(current_contest, pending_overvotes, pending_undervotes)
                current_contest = contest_name
                pending_overvotes = overvotes
                pending_undervotes = undervotes

            candidate = re.sub(r'^\(\d+\)\s*', '', candidate_name)

            if '(' in candidate and not party:
                if '(I)' in candidate:
                    if '(I)(I)' in candidate:
                        candidate = candidate.split('(I)')[0]
                        party = 'I'
                    else:
                        candidate, party = candidate.split('(I)')
                    candidate = candidate.strip() + ' (I)'
                else:
                    candidate, party = candidate.split('(', 1)
                    candidate = candidate.strip()
                party = party.replace(')', '').strip()

            results.append([county, contest_name, '', party, candidate, total_votes])

        flush_contest_extras(current_contest, pending_overvotes, pending_undervotes)

    with open(output_csv, 'w', newline='', encoding='utf-8') as csvfile:
        w = csv.writer(csvfile)
        w.writerow(['county', 'office', 'district', 'party', 'candidate', 'votes'])
        w.writerows(results)


def parse_office(office_text):
    if ' - ' in office_text:
        office = office_text.split('-')[0]
    else:
        office = office_text.split(',')[0]
    if ', District' in office_text:
        district = office_text.split(', District')[1].split(' - ')[0].strip()
    elif 'President' in office_text:
        office = "President"
        district = None
    elif 'United States Senator' in office_text:
        office = 'U.S. Senate'
        district = None
    elif ',' in office_text:
        district = office_text.split(',')[1]
    else:
        district = None
    return [office.strip(), district]

def parse_party(office_text):
    if '- REP' in office_text:
        party = 'REP'
    elif '- DEM' in office_text:
        party = 'DEM'
    else:
        party = None
    return party
