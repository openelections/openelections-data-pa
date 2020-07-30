import pdfreader
from pdfreader import PDFDocument, SimplePDFViewer
import csv

def ches_scraping_one_page(text_on_page, csv_writer, county):
    #some reference information to save
    parties = ["DEM", "REP", "NPA"]
    offices = ["PRESIDENT OF THE UNITED STATES", "ATTORNEY GENERAL", "AUDITOR GENERAL", "STATE TREASURER"]
    numbers = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']
    precint = text_on_page[6]
    save = False
    name = 0
    #for every item on the page
    for i in range(len(text_on_page) - 3):
        #main voter stuff, usuallly once every 5 pages
        if text_on_page[i] == "STATISTICS":
            #total registered voters
            votes = text_on_page[i + 6]
            office = text_on_page[i + 5]
            line = [county, precint, office, " ", "", office, votes]
            csv_writer.writerow(line)
            #print(line)
            #dem registered voters
            #print(text_on_page[i])
            votes = text_on_page[i + 8]
            office = text_on_page[i + 7]
            line = [county, precint, office, " ", "DEM", office, votes]
            csv_writer.writerow(line)
            #print(line)
            #rep registered voters
            votes = text_on_page[i + 10]
            office = text_on_page[i + 9]
            line = [county, precint, office, " ", "REP", office, votes]
            csv_writer.writerow(line)
            #print(line)
            #non partisan registered voters
            votes = text_on_page[i + 12]
            office = text_on_page[i + 11]
            line = [county, precint, office, " ", "NPA", office, votes]
            csv_writer.writerow(line)
            #print(line)
        #how many ballots were cast and how
        if text_on_page[i][:12] == "Ballots Cast":
            office = text_on_page[i]
            votes = text_on_page[i - 1]
            election_day = text_on_page[i + 1]
            #mail_in = text_on_page[i + 4]
            absentee_mail = text_on_page[i + 2]
            #absentee = text_on_page[i + 2]
            if text_on_page[i][15] == "D":
                party = "DEM"
            elif text_on_page[i][15] == "R":
                party = "REP"
            elif text_on_page[i][15] == "N":
                party = "NPA"
            else:
                party = " "
            if absentee_mail == "Voter Turnout - Total":
                absentee_mail = "0"
            line = [county, precint, office, " ", party, " ", votes, absentee_mail, election_day]
            csv_writer.writerow(line)
            #print(line)

        if text_on_page[i] == "Total Votes Cast":
            save = False

        if text_on_page[i][0:3] in parties:
            party = text_on_page[i][0:3]
            save = True
            name = 0
            office = text_on_page[i][4:]
            continue

        #for every candidate
        if save and text_on_page[i] != "TOTAL" and name == 4:
            #making sure it's an office we want to look at
            if office not in offices and office[0:3] != "REP":
                continue
            else:
                candidate = text_on_page[i]
                #safeguard
                if candidate[-1:] == "%":
                    continue
                votes = text_on_page[i + 1]
                #safeguard
                if votes[0] not in numbers :
                    continue
                election_day = text_on_page[i + 2]
                #mail_in = text_on_page[i + 4]
                absentee_mail = text_on_page[i + 3]
                #absentee = text_on_page[i + 3]
                line = [county, precint, office, " ", party, candidate, votes, absentee_mail, election_day]
                csv_writer.writerow(line)
                #print(line)
                name  = 1
        elif not name == 4:
            name += 1


if __name__ == "__main__":
    #loading i the pdf
    fd = open("chester_results_2020.pdf", "rb")
    doc = PDFDocument(fd)
    viewer = SimplePDFViewer(fd)
    all_pages = [p for p in doc.pages()]
    #opening the csv
    with open('20200602__pa__primary__chester__precinct.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        #writing header
        writer.writerow(["county", "precint", "office", "district", "party", "candidate", "votes", "absentee_mail", "election_day"])
        #for every page in the file
        for i in range(len((all_pages))):
        #for i in range(5):
            viewer.navigate(i + 1)
            viewer.render()
            text_on_page = viewer.canvas.strings
            #print(text_on_page)
            ches_scraping_one_page(text_on_page, writer, "Chester")
            print("We are " + str((i / len(all_pages)) * 100) + "% done.")
