import requests
import json
import csv

results = []

pres_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E46343344363839452D354634342D343832442D383532392D3343333741374644353330377E30"
house_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E42323135443941322D354237452D344145322D423345432D4335423831323837414445447E30"
ag_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E34433932463336432D333638412D343843332D384444362D3138423333374431423342397E30"
aud_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E44453734453741342D373438422D343543362D423546362D4542443242373041413937427E30"
treas_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E43323931373837462D303730412D343541362D393133452D3941443638363245334641467E30"
rep22_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E38393435463944332D334642422D343134352D413638452D4246373037423736433739327E30"
rep131_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E30354639374435302D433931342D344146442D424235442D3242464145344337303641417E30"
rep132_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E46333343323437392D313136452D343835462D423431302D4239323331323437443636467E30"
rep133_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E36303242363631412D374639352D343641452D414131312D4534313544383844433442377E30"
rep134_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E39373930463632362D383338352D344535352D393937342D4636424342393732333235387E30"
rep183_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E36393233323942392D343245392D343637452D384636442D4339303137363830363446397E30"
rep187_url = "https://home.lehighcounty.org/TallyHo/MapSupport/ElectionResultsHandler.ashx?racekey=317E7E526163657E32434535344346432D453131362D344438442D394231332D3636384536343543323234427E30"

for url in [pres_url, house_url, ag_url, aud_url, treas_url, rep22_url, rep131_url, rep132_url, rep133_url, rep134_url, rep183_url, rep187_url]:
    print(url)
    r = requests.get(url, verify=False)
    if 'Precincts' in r.json():
        for precinct in r.json()['Precincts']:
            for candidate in precinct['PrecinctCandidates']:
                results.append(['Lehigh', precinct['PrecinctId'], precinct['PrecinctName'], precinct['RaceName'], candidate['CandidateName'], candidate['PartyCode'], candidate['VoteCount']])

with open('20201103__pa__general__lehigh__precinct.csv', 'wt') as csvfile:
    w = csv.writer(csvfile)
    headers = ['county', 'precinct', 'office', 'party', 'candidate', 'votes']
    w.writerow(headers)
    w.writerows(results)
