# PA Election Results  
# Takes downloaded file and converts to csv file usable by OpenElections
import sys
import csv
import re
import os

# fuctions
def convert_to_full(name):
    """Takes candidate name in the form of last, first etc. and converts to first middle last suffix"""

    # function template
    # initialize variables
    first = ''
    last = ''
    middle = ''
    suffix = ''

    # strip periods from name
    name = name.replace('.','')

    # in the 2012 file, the format is different for suffixes such as Jr.
    # instead of putting them at the end of the name, the file has them
    # comma separated between the last and first name
    # this code changes that to make the rest of the function work
    if name.count(', ') > 1:
        name = name.strip().split(', ')
        name = name[0] + ", " + name[2] + " " + name[1]

    # split at comma to get last name and rest of name
    last, rest = name.strip().split(', ')

    # change last to title case
    last = last.title()
    # check for McNames
    if last[:2] == 'Mc':
        last = last[:2] + last[2:].title()

    # get rid of extra internal spaces in rest of name
    extra_spaces = re.compile('  *')
    rest = re.sub('  *',' ',rest)

    # split rest at spaces
    rest = rest.split(' ')

    # get first name and convert to title case, remove it from rest variable
    first = rest[0]
    first = first.title()
    rest = rest[1:]

    # if first name is only initial, add a period
    if len(first) == 1:
        first = first + "."

    # check for possible suffix
    if len(rest) > 0:
        possible_suffix = rest[len(rest)-1]
        if possible_suffix in ['II','III','JR','SR']:
            suffix = possible_suffix
            rest = rest[:-1]
        else:
            suffix = ''

    # add period to suffixes JR and SR and conver to title case
    if suffix in ['JR','SR']:
        suffix = suffix.title() + "."

    # check for middle initial or names
    if len(rest) > 0:
        for element in rest:
            if len(element) == 1:
                element = element + "."
            else:
                element = element.title()

            if len(middle) == 0:
                middle = element
            else:
                middle = middle + " " + element

        rest = []

    full = " ".join([first, middle, last, suffix]).strip()
    full = re.sub("  *"," ",full)

    return full


def main(in_file, out_file, *args):

    # declare dictionaries used for transforming data in offices and parties columns
    offices_to_keep = {'Representative in the General Assembly':'State House',
                       'President of the United States': 'President',
                       'Representative in Congress': 'U.S. House',
                       'United States Senator': 'U.S. Senate',
                       'Attorney General': 'Attorney General',
                       'Senator in the General Assembly': 'State Senate',
                       'Governor':'Governor'}

    parties = {'Democratic':'DEM',
               'Republican':'REP',
               'Independent':'IND',
               'Democratic / Republican':'DEMREP',
               'Constitution Party':'CNST',
               'Constitution':'CNST',
               'Green':'GRN',
               'Libertarian':'LIB',
               'usaminutemen':'OTH',
               'No Affiliation':'IND',
               'Kate McGraw Independent':'IND',
               'Reform':'REF',
               'Republican / Democratic':'DEMREP',
               'COA':'OTH',
               'POV':'OTH',
               'CFM':'OTH',
               'DBP':'OTH',
               'D/G':'OTH',
               'HFR':'OTH',
               'GFL':'OTH',
               'SOS':'OTH',
               'Healthcare':'OTH',
               'Randolph for Congress':'OTH',
               '51st Independent Delegation':'OTH',
               'Action and Accountability':'OTH',
               'Socialist Workers':'OTH',
               'Growth Management':'OTH',
               'None':'IND',
               'Vote for Cash':'OTH',
               'Socialist Party USA':'OTH',
               'New American Independent':'OTH',
               'SWP':'OTH',
               'Blasko for Representative':'OTH',
               'No Party Affiliation':'IND',
               'Independent Patriots':'OTH',
               'Victory For Vybiral':'OTH',
               'Towne For Congress':'OTH',
               'Fagan For 145th':'OTH',
               'Unaffiliated Independent':'IND',
               'American Congress':'OTH',
               'For the 89th':'OTH',
               'Warren Bloom Party':'OTH',
               'BEDNARSKI FOR CONGRESS':'OTH',
               'YORK LIBERTARIAN PARTY':'LIB',
               'Families 4 Brentley':'OTH',
               'McAteer For House':'OTH',
               'Vote For Ines':'OTH'}

    # ---- Error Checking
    # the __main__ statement will check whether user provided input and
    # ouput file names
    # currently there's no extra arguments so *args is just a place holder

    
    # check to see if the out_file already exists
    # if so, change out_file to new name
    # set ext to csv if user didn't include an extension
    try:
        fn, ext = out_file.split('.')
    except ValueError:
        fn = out_file
        ext = 'csv'

    version = 1
    test_name = fn + '.' + ext
    while os.path.isfile(test_name):
        print("File {} already exists, adding/incrementing version number.".format(test_name))
        test_name = fn + '_v' + str(version) + '.' + ext
        version += 1

    out_file = test_name

    # try to open input file, if it doesn't work, forget the rest
    try:
        orig = open(in_file, 'r')
    except FileNotFoundError:
        print("Input file {} not found.".format(in_file))
        return

    # since that worked, if you've reached this far, open the new output file
    rev = open(out_file,'w',newline='')


    # ---- Get ready to rumble
    # set up the reader and writer and sent the header row to the output file
    reader = csv.reader(orig)

    writer = csv.writer(rev)
    writer.writerow(['county','office','district','party','candidate','votes'])

    # skip the header row in the input file
    next(reader, None)

    # ----- Main loop
    for row in reader:
        # skip rows for offices not included in OpenElections data
        
        if row[2] in offices_to_keep:
            # get county name and convert to title case
            county = row[1].title()

            # get office and convert to standard labels
            office = offices_to_keep[row[2]]
            
            # get district and extract numbers, discard the rest
            old_district = row[3]
            district = ""
            for char in old_district:
                if char.isdigit():
                    district = district + char
            
            # get party and convert to abbreviation
            party = row[4]
            
            if party in parties:
                party = parties[party]
            else:
                print("Unknown party: {}", party)
                parties[party] = 'UNK'
                party = parties[party]
            
            # get candidate name and process to full name (first middle last suffix)        
            candidate = convert_to_full(row[5])

            votes = row[6].replace(',','')
            
            writer.writerow([county,office,district, party,candidate,votes])

    orig.close()
    rev.close()

# Heres where you make the program able to run from the command line
if __name__ == "__main__":
    if len(sys.argv) < 3:
        # first value is the python file, so need three values to have
        # enough arguments
        raise SyntaxError("Need input and output file names")
    if len(sys.argv) != 3:
        # if there are keyword arguments
        main(sys.argv[1], sys.argv[2], *sys.argv[3:])
    else:
        # No keyword arguments
        main(sys.argv[1], sys.argv[2])
