import csv

COUNTIES = {1: 'Adams', 2: 'Allegheny', 3: 'Armstrong', 4: 'Beaver', 5: 'Bedford', 6: 'Berks', 7: 'Blair', 8: 'Bradford', 9: 'Bucks', 10: 'Butler', 11: 'Cambria', 12: 'Cameron', 13: 'Carbon', 14: 'Centre', 15: 'Chester', 16: 'Clarion', 17: 'Clearfield', 18: 'Clinton', 19: 'Columbia', 20: 'Crawford', 21: 'Cumberland', 22: 'Dauphin', 23: 'Delaware', 24: 'Elk', 25: 'Erie', 26: 'Fayette', 27: 'Forest', 28: 'Franklin', 29: 'Fulton', 30: 'Greene', 31: 'Huntingdon', 32: 'Indiana', 33: 'Jefferson', 34: 'Juniata', 35: 'Lackawanna', 36: 'Lancaster', 37: 'Lawrence', 38: 'Lebanon', 39: 'Lehigh', 40: 'Luzerne', 41: 'Lycoming', 42: 'McKean', 43: 'Mercer', 44: 'Mifflin', 45: 'Monroe', 46: 'Montgomery', 47: 'Montour', 48: 'Northampton', 49: 'Northumberland', 50: 'Perry', 51: 'Philadelphia', 52: 'Pike', 53: 'Potter', 54: 'Schuylkill', 55: 'Snyder', 56: 'Somerset', 57: 'Sullivan', 58: 'Susquehanna', 59: 'Tioga', 60: 'Union', 61: 'Venango', 62: 'Warren', 63: 'Washington', 64: 'Wayne', 65: 'Westmoreland', 66: 'Wyoming', 67: 'York'}

OFFICES = {"USP": 'President', "ATT": "Attorney General", "AUD": "Auditor General", 'USS': 'U.S. Senate', 'GOV': 'Governor', 'LTG': 'Lieutenant Governor', 'TRE': 'State Treasurer', 'USC': 'U.S. House', 'STS': 'State Senate', 'STH': 'State Representative'}

results = []

with open("2016/20161108__pa__general__precinct.csv", 'r') as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        if row['office_code'] in OFFICES.keys():
            county = COUNTIES[int(row['county_code'])]
            office = OFFICES[row['office_code']]
            if row['office_code'] == 'USC':
                district = row['congress_district']
            elif row['office_code'] == 'STS':
                district = row['senate_district']
            elif row['office_code'] == 'STH':
                district = row['house_district']
            else:
                district = None
            if row['breakdown1'] == 'D':
                if row['breakdown2'] != '':
                    precinct = row['municipality']+ ' District ' + row['name1'] + '-' + row['name2']
                else:
                    precinct = row['municipality']+ ' District ' + row['name1']
            elif row['breakdown1'] == 'P':
                if row['breakdown2'] != '':
                    precinct = row['municipality']+ ' Precinct ' + row['name1'] + '-' + row['name2']
                else:
                    precinct = row['municipality']+ ' Precinct ' + row['name1']
            elif row['breakdown1'] == 'W':
                if row['breakdown2'] != '':
                    precinct = row['municipality']+ ' Ward ' + row['name1'] + '-' + row['name2']
                else:
                    precinct = row['municipality']+ ' Ward ' + row['name1']
            elif row['breakdown1'] == 'X':
                if row['breakdown2'] != '':
                    precinct = row['municipality']+ ' ' + row['name1'] + ' ' + row['name2']
                else:
                    precinct = row['municipality']+ ' ' + row['name1']
            else:
                precinct = row['municipality']
            results.append([county, precinct, office, district, row['candidate'], row['party'], row['votes']])

    with open("test.csv", "w") as csv_outfile:
        outfile = csv.writer(csv_outfile)
        outfile.writerow(['county','precinct', 'office', 'district', 'candidate', 'party', 'votes'])
        outfile.writerows(results)
