import json
import os
import requests
from collections import defaultdict
from csv import DictReader
from time import sleep

CSV_FILE_PATH = os.path.join('..', '2020')
PA_OFFICIAL_RESULTS_WEBSITE = 'https://www.electionreturns.pa.gov/api/ElectionReturn'
PA_OFFICIAL_COUNTY_API = 'GetCountyData?countyName={}&methodName=GetCountyData&electionid=83&electiontype=P&isactive=0'
PA_OFFICIAL_COUNTY_RESULTS_URL = '/'.join([PA_OFFICIAL_RESULTS_WEBSITE, PA_OFFICIAL_COUNTY_API])
QUERY_SPACING_IN_SECONDS = 3

PA_OFFICIAL_OFFICE_TO_OPEN_ELECTIONS_OFFICE = {
    'President of the United States': 'President',
    'Attorney General': 'Attorney General',
    'Auditor General': 'Auditor General',
    'Representative in Congress': 'U.S. House',
    'Representative in the General Assembly': 'General Assembly',
    'Senator in the General Assembly': 'State Senate',
    'State Treasurer': 'State Treasurer',
}
PA_OFFICIAL_PARTY_TO_OPEN_ELECTIONS_PARTY = {
    'Democratic': 'DEM',
    'Republican': 'REP',
}

PA_OFFICIAL_CANDIDATE_TO_OPEN_ELECTIONS_CANDIDATE = {
    # state and national offices
    'SANDERS, BERNARD': ['BERNIE SANDERS'],
    'BIDEN, JOSEPH ROBINETTE JR': ['JOSEPH R BIDEN'],
    'TRUMP, DONALD J.': ['DONALD J TRUMP'],
    'WELD, WILLIAM F': ['BILL WELD', 'BILLY WELD'],
    'SHAPIRO, JOSHUA D': ['JOSH SHAPIRO'],
    'CONKLIN, HARRY  SCOTT': ['H SCOTT CONKLIN'],
    'DAVIS, ROSE MARIE': ['ROSE ROSIE MARIE DAVIS', 'ROSE ROSIE DAVIS'],
    'AHMAD, NILOFER NINA': ['NINA AHMAD'],
    'DEFOOR, TIMOTHY  L': ['TIMOTHY DEFOOR'],
    'TORSELLA, JOSEPH M': ['JOE TORSELLA'],
    # districted offices
    'ROWLEY, RAYMOND TODD': ['TODD ROWLEY'],
    'MOUL, DANIEL P.': ['DAN MOUL'],
    'STERNER, RICHARD L': ['RICH STERNER'],
    'MASTRIANO, DOUGLAS VINCENT': ['DOUG MASTRIANO'],
    'THOMPSON, GLENN W JR': ['GLENN GT THOMPSON'],
    'PYLE, JEFFREY P.': ['JEFF PYLE'],
    'LANGERHOLC, WAYNE JR.': ['WAYNE LANGERHOLC JR'],
    'CUNNANE, MADELEINE  DEAN': ['MADELEINE DEAN'],
    'HOULAHAN, CHRISTINA J': ['CHRISSY HOULAHAN'],
    'QUICK, SUSAN LAURA': ['LAURA QUICK'],
    'MEUSER, DANIEL PHILIP': ['DAN MEUSER'],
    'KNOWLES, JEROME P': ['JERRY KNOWLES'],
    'OSWALD, JAMES DANIEL': ['JAMES D OSWALD'],
    'GUZMAN, MANUEL JR': ['MANNY GUZMAN'],
    'GAGLIARDO, VINCENT D JR': ['VINCENT D GAGLIARDO, JR'],
    'COX, JAMES A. JR.': ['JIM COX'],
    'FOLEY, FRANCIS LAMAR JR': ['LAMAR FOLEY'],
    'MALONEY, DAVID M. SR.': ['DAVID M MALONEY'],
    'MACKENZIE, RYAN': ['RYAN E MACKENZIE'],
    'BLICHAR, MICHAEL E JR': ['MICHAEL BLICHAR, JR', 'MICHAEL BLICHAR JR'],
    'SCHWANK, JUDITH L':  ['JUDY SCHWANK'],
    'ARGALL, DAVID G': ['DAVE ARGALL'],
    'HURWITZ, SKYLAR': ['SKYLAR D HURWITZ'],
    'MEEHAN, ANDREW MARTIN': ['ANDY MEEHAN'],
    'ARCHETTO, GREGORY': ['GREG ARCHETTO'],
    'LAREAU, MALINDA LAUREN': ['LAUREN LAREAU'],
    'POLINCHOCK, F. TODD': ['TODD POLINCHOCK'],
    'TOMLINSON, KATHLEEN C': ['KATHLEEN KC TOMLINSON'],
    'KELLY, GEORGE J JR': ['MIKE KELLY'],
    'PARNELL, RICHARD SEAN': ['SEAN PARNELL'],
    'HEASLEY, PHILLIP C': ['PHIL HEASLEY'],
    'BONNER, TIMOTHY R.': ['TIM BONNER'],
    'DOCTOR, SAMUEL JOSEPH': ['SAM DOCTOR'],
    'SMITH, DANIEL B JR': ['DANIEL SMITH JR'],
    'MARSHALL, JAMES E.': ['JIM MARSHALL'],
    'JAMES, R. LEE': ['R LEE JAMES'],
    'STROMYER, SHELBIE LYNN': ['SHELBIE L STROMYER'],
    'KRIZAN, STEPHEN JOHN III': ['STEPHEN J KRIZAN III'],
    'VOGEL, ELDER A JR': ['ELDER A VOGEL JR'],
    'RIGBY, JAMES PATRICK': ['JIM RIGBY'],
    'CARNICELLA, GERALD  S': ['JERRY CARNICELLA'],
    'SANKEY, THOMAS R III': ['TOMMY SANKEY'],
    'BRIER, THOMAS F JR': ['TOM BRIER'],
    'DEPASQUALE, EUGENIO A': ['EUGENE DEPASQUALE'],
    'NESSINGER, JEDIDIAH E': ['JED NESSINGER'],
    'BENNER, WILLIAM E. III': ['WILLIAM BILL BENNER'],
    'ROTHMAN, WILLIAM GREGORY': ['GREG ROTHMAN'],
    'ROSS, DOUGLAS F': ['DOUG ROSS'],
    'COPLEN, RICHARD CHASE': ['RICK COPLEN'],
    'REGAN, MICHAEL': ['MIKE REGAN'],
    'TROUTMAN, WILLIAM NELSON JR': ['BILL TROUTMAN'],
    'HICKERNELL, DAVID S.': ['DAVID S HICKERNELL'],
    'MAXSON, KELVIN': ['KEVIN MAXSON'],
    'SMITH, PATRICIA A': ['PATTY SMITH'],
    'HELM, SUSAN C.': ['SUSAN C SUE HELM'],
    'LUPP, CHRISTOPHER ANDRE': ['CHRIS LUPP'],
    'MEHAFFIE, THOMAS L III': ['TOM MEHAFFIE'],
    'BREAULT, HERVEY CONRAD II': ['HERV BREAULT'],
    'KERWIN, JOSEPH P': ['JOE KERWIN'],
    'TAYLOR, ALVIN Q SR': ['ALVIN Q TAYLOR'],
    'DISANTO, GIOVANNI M': ['JOHN DISANTO'],
    'JORDAN, ROBERT M': ['ROB JORDAN'],
    'SPAHR, CATHERINE E': ['CATHY SPAHR'],
    'WILLIAMS, WENDELL CRAIG': ['CRAIG WILLIAMS'],
    'DELLOSO, DAVID M': ['DAVE DELLOSO'],
    'GAGLIO, PETER THOMAS JR': ['PETE GAGLIO'],
    'ZABEL, MICHAEL P': ['MIKE ZABEL'],
    'SMYTHE, ROBERT F. JR.': ['ROBERT SMYTHE JR'],
    'CIAMACCA, DEBRA  A': ['DEB CIAMACCA'],
    'QUINN, CHRISTOPHER  B': ['CHRIS QUINN'],
    'DONATUCCI, MARIA P.': ['MARIA P DONATUCCI'],
    'MCCLINTON, JOANNA': ['JOANNA E MCCLINTON'],
    'KILLION, THOMAS H.': ['THOMAS H KILLION'],
    'MERSKI, ROBERT E': ['BOB MERSKI'],
    'SONNEY, CURTIS G.': ['CURT SONNEY'],
    'FERRENCE, MATTHEW': ['MATT FERRENCE'],
    'ROAE, BRADLEY T': ['BRAD ROAE'],
    'LAUGHLIN, DANIEL J MR': ['DAN LAUGHLIN'],
    'MARX, WILLIAM A': ['BILL MARX'],
    'COOK, DONALD': ['BUD COOK'],
    'BOTTINO, ANTHONY JAMES JR': ['TONY BOTTINO'],
    'HERSHEY, JOHNATHAN D': ['JOHN HERSHEY'],
    'KAUFFMAN, ROBERT W.': ['ROB KAUFFMAN'],
    'STRUZZI, JAMES BRUNO II': ['JIM STRUZZI'],
    'MATSON, JOHN D. JR': ['JOHN JACK MATSON'],
    'CARTWRIGHT, MATTHEW ALTON': ['MATT CARTWRIGHT'],
    'DANIELS, THEODORE V': ['TEDDY DANIELS'],
    'BOGNET, JAMES ROCCO': ['JIM BOGNET'],
    'CAMMISA, MIKEL J': ['MIKE CAMMISA'],
    'MARSICANO, MICHAEL P': ['MIKE MARSICANO'],
    'MULLINS, KYLE': ['KYLE J MULLINS'],
    'KOSIEROWSKI, BRIDGET': ['BRIDGET MALLOY KOSIEROWSKI'],
    'CARROLL, MICHAEL B': ['MIKE CARROLL'],
    'SMUCKER, LLOYD K.': ['LLOYD K SMUCKER'],
    'FEE, MELINDA S': ['MINDY FEE'],
    'WITMER, BRADFORD L': ['BRAD WITMER'],
    'GREINER, KEITH JAMES': ['KEITH J GREINER'],
    'STURLA, P MICHAEL': ['MIKE STURLA'],
    'GULICK, DANA': ['DANA HAMP GULICK'],
    'MENTZER, STEVEN CURTIS': ['STEVE MENTZER'],
    'HODGE, RICHARD MICHAEL': ['RICK HODGE'],
    'ZIMMERMAN, DAVID H.': ['DAVE ZIMMERMAN'],
    'TEMIN, JANET': ['JANET DIAZ'],
    'RYAN, FRANCIS X': ['FRANK RYAN'],
    'MACKENZIE, V MILOU': ['MILOU MACKENZIE'],
    'ELLENBERGER, JOSEPH AMOS': ['JOE ELLENBERGER'],
    'SCHLOSSBERG, MICHAEL': ['MIKE SCHLOSSBERG'],
    'MAKO, ZACHARY ALLEN': ['ZACH MAKO'],
    'EACHUS, TODD  A.': ['TODD A EACHUS'],
    'KELLER, FREDERICK B': ['FRED KELLER'],
    'HINES, DAVID  RAMSAY': ['DAVE HINES'],
    'HAMM, JOSEPH D': ['JOE HAMM'],
    'DINCHER, MICHAEL A': ['MIKE DINCHER'],
    'BAKER, JACLYN E': ['JACKIE BAKER'],
    'YAW, EMERSON EUGENE': ['GENE YAW'],
    'HENNESSEY, TIMOTHY F.': ['TIM HENNESSEY'],
    'MALAGARI, STEVEN R': ['STEVE MALAGARI'],
    'ARNOTT, ALLAN MILES': ['MILES ARNOTT'],
    'HANBIDGE, LAURA ELIZABETH FRANCES': ['LIZ HANBIDGE'],
    'FRIEBEL, FLORENCE L.': ['LISA FRIEBEL'],
    'BRADFORD, MATTHEW D': ['MATT BRADFORD'],
    'SARING, JAMES COURTLAND': ['JIM SARING'],
    'CIRESI, JOSEPH P': ['JOE CIRESI'],
    'SCANNAPIECO, ANNA MARIE': ['ANNAMARIE SCANNAPIECO'],
    'DALEY, MARY JOSEPHINE': ['MARY JO DALEY'],
    'WEBSTER, JOSEPH': ['JOE WEBSTER'],
    'MAZZA, BETH ANN': ['BETH ANN BITTNER MAZZA'],
    'STEPHENS, WILLIAM TODD': ['TODD STEPHENS'],
    'SANCHEZ, BENJAMIN V': ['BEN SANCHEZ'],
    'SPRIGG WISEHART, GRETCHEN ANDREA': ['GRETCHEN WISEHART'],
    'SOSA, RAYMOND L': ['RAY SOSA'],
    'BOWERS, KATHLEEN': ['KATHY GARRY BOWERS'],
    'BASHIR, HAROON': ['AARON BASHIR'],
    'SAMUELSON, STEPHEN P': ['STEVE SAMUELSON'],
    'EMRICK, JOSEPH T': ['JOE EMRICK'],
    'TARSI, ANTHONY ROBERT': ['TONY TARSI'],
    'SYMONS, PETER JAMES JR.': ['PETER PJ SYMONS JR'],
    'TWARDZIK, TIMOTHY F': ['TIM TWARDZIK'],
    'OWLETT, CLINTON D.': ['CLINT OWLETT'],
    'BROWN, MARGARET SATTERWHITE': ['MARGIE BROWN'],
    'BROWN, JAMES MARK': ['JIM BROWN'],
    'WILLIAMS, ROBERT T SR': ['BOB WILLIAMS'],
    'KAIL, JOSHUA DANIEL': ['JOSHUA D KAIL'],
    'KIRSCH, THOMAS ALAN': ['TOM KIRSCH'],
    'PUSKARIC, MICHAEL JAMES': ['MIKE PUSKARIC'],
    'MIHALEK (STUCK), NATALIE NICOLE': ['NATALIE MIHALEK'],
    'O\'NEAL, TIMOTHY JON': ['TIM O\'NEAL'],
    'IOVINO, PAMELA M': ['PAM IOVINO'],
    'NEFF, JEFFREY WILLIAM': ['JEFF NEFF'],
    'DERMODY, FRANCIS J.': ['FRANK DERMODY'],
    'BROOKS, ROBERT J.': ['BOB BROOKS'],
    'PRAH, ROBERT L JR': ['ROBERT PRAH JR'],
    'REESE, MICHAEL P.': ['MIKE REESE'],
    'BREWSTER, JAMES R': ['JIM BREWSTER'],
    'JONES, PAUL MICHAEL': ['MIKE JONES'],
    'SAYLOR, STANLEY E.': ['STAN SAYLOR'],
    'FRENCH, KATHRYN CORRELL': ['KACEY FRENCH'],
    'KLUNK, KATE ANNE': ['KATE A KLUNK'],
    'GROVE, SETH MICHAEL': ['SETH M GROVE'],
    'WALTZ, JOSEPH': ['JOE WALTZ'],
    'WALTENBAUGH, TAY  R.': ['TAY R WALTENBAUGH'],
    'PASHINSKI, EDWIN': ['EDDIE DAY PASHINSKI'],
    'DRISCOLL, MICHAEL': ['MIKE DRISCOLL'],
    'DEMPSEY, JEFFREY': ['JEFF DEMPSEY'],
    'MURRAY, ANDREW I': ['DREW MURRAY'],
    'MENNA, LOUIS T. IV': ['LOU MENNA IV'],
    'HARRIS, JORDAN ALEXANDER': ['JORDAN A HARRIS'],
    'GREEN, GWENDOLYN VERONICA': ['RONI GREEN'],
    'DOWNING, SAMUEL VAN STONE': ['VAN STONE'],
    'WILLIAMS, BERNARD A.': ['BERNARD A WILLIAMS'],
    'FARNESE, LAWRENCE M JR': ['LARRY FARNESE'],
    'BOYLE, BRENDAN F.': ['BRENDAN F BOYLE'],
    'MONAHAN, WILLIAM T': ['BILL MONAHAN'],
    'PEIFER, MICHAEL': ['MIKE PEIFER'],
    'ARMANINI, MICHAEL J. MR.': ['MIKE ARMANINI'],
    'SAINATO, CHRISTOPHER': ['CHRIS SAINATO'],
    'RYAN, CAROL LYNNE': ['LYNNE RYAN'],
    'RAPP, KATHY L.': ['KATHY L RAPP'],
    'CAUSER, MARTIN T.': ['MARTIN T CAUSER'],
}


def collect_actual_data(filename, errors):
    candidate_to_votes = defaultdict(int)
    with open(os.path.join(CSV_FILE_PATH, filename)) as f_in:
        csv_to_validate = DictReader(f_in)
        for line in csv_to_validate:
            try:
                candidate = line['candidate'].replace('.', '').replace('(', '').replace(')', '')
                key = line['office'].title(), line['district'], \
                    line['party'], candidate.upper().strip()
                candidate_to_votes[key] += int(line['votes'].replace(',', ''))
            except Exception as e:
                errors.append(f'Unable to parse `{line}`: {e}')
                raise e
    return candidate_to_votes


def collect_expected_data(county):
    response = requests.get(PA_OFFICIAL_COUNTY_RESULTS_URL.format(county))
    expected_results = json.loads(response.json())
    return expected_results['Election'][county.upper().replace('MCK', 'McK')][0]


def process_county(county, filename, errors):
    candidate_to_votes = collect_actual_data(filename, errors)
    county_data = collect_expected_data(county)
    for pa_offical_office in PA_OFFICIAL_OFFICE_TO_OPEN_ELECTIONS_OFFICE:
        open_elections_office = PA_OFFICIAL_OFFICE_TO_OPEN_ELECTIONS_OFFICE[pa_offical_office]
        office_data = county_data.get(pa_offical_office)
        if office_data:
            process_office_data(office_data, open_elections_office, candidate_to_votes, errors)


def process_office_data(office_data, open_elections_office, candidate_to_votes, errors):
    for district_data in office_data[0]['Districts']:
        open_elections_district = get_district(district_data)
        for pa_official_party in district_data['Candidates'][0]:
            party_data = district_data['Candidates'][0][pa_official_party]
            for candidate_data in party_data:
                open_elections_party = candidate_data['PartyName']
                candidate_options = list(get_candidate_options(candidate_data))
                key = None
                for open_elections_candidate in candidate_options:
                    potential_key = open_elections_office, open_elections_district, \
                                    open_elections_party, open_elections_candidate
                    if potential_key in candidate_to_votes:
                        key = potential_key
                if not key:
                    errors.append(f'Missing data for {open_elections_office}, {open_elections_district}, '
                                  f'{open_elections_party}, {candidate_options}')
                else:
                    actual_votes = candidate_to_votes[key]
                    expected_votes = int(candidate_data['Votes'])
                    if expected_votes != actual_votes:
                        errors.append(f'Vote mismatch for {key}: {actual_votes} != {expected_votes}')


def get_district(district_data):
    district = district_data['District']
    open_elections_district = ''
    if district:
        for c in district:
            if c > '9' or c < '0':
                break
            open_elections_district += c
    return open_elections_district


def get_candidate_options(candidate_data):
    candidate_name = candidate_data['CandidateName'].strip().replace(' ,', ',')
    if candidate_name in PA_OFFICIAL_CANDIDATE_TO_OPEN_ELECTIONS_CANDIDATE:
        yield from PA_OFFICIAL_CANDIDATE_TO_OPEN_ELECTIONS_CANDIDATE[candidate_name]
    last_name, first_name = candidate_name.split(', ', 1)
    yield first_name + ' ' + last_name
    if ' ' in first_name:
        first_name, middle_name = first_name.split(' ', 1)
        yield first_name + ' ' + last_name


def main():
    for filename in os.listdir(CSV_FILE_PATH):
        yyyymmdd, state, election_type, county, collation = filename.split('__')
        print(f'Processing county=`{county}`', end=' ')
        errors = []
        try:
            process_county(county, filename, errors)
        except:
            errors.append(f'Failed processing `{county}`, continuing')
        if errors:
            print(f'... {len(errors)} errors found')
            for error in errors:
                print('\t' + error)
        else:
            print(f'... no errors found')
        sleep(QUERY_SPACING_IN_SECONDS)


if __name__ == "__main__":
    main()
