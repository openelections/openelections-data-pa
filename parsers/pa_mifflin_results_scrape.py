import pdfreader
from pdfreader import PDFDocument, SimplePDFViewer
import csv

def scraping_one_page(text_on_page, csv_writer):
    county = "Mifflin"
    precint = text_on_page[6]
    save = False
    name = 0
    for i in range(len(viewer.canvas.strings) - 3):
        if text_on_page[i] == "STATISTICS":
            #print(text_on_page[i])
            votes = text_on_page[i + 8]
            office = text_on_page[i + 7]
            line = [county, precint, office, " ", " ", office, votes]
            csv_writer.writerow(line)
            #print(line)
            votes = text_on_page[i + 10]
            office = text_on_page[i + 9]
            line = [county, precint, office, " ", "DEM", office, votes]
            csv_writer.writerow(line)
            #print(line)
            votes = text_on_page[i + 12]
            office = text_on_page[i + 11]
            line = [county, precint, office, " ", "REP", office, votes]
            csv_writer.writerow(line)
            #print(line)
            votes = text_on_page[i + 14]
            office = text_on_page[i + 13]
            line = [county, precint, office, " ", "NPA", office, votes]
            csv_writer.writerow(line)
            #print(line)

        if text_on_page[i][:12] == "Ballots Cast":
            office = text_on_page[i]
            votes = text_on_page[i - 1]
            election_day = text_on_page[i + 1]
            mail_in = str(int(text_on_page[i + 3]) + int(text_on_page[i + 4]))
            absentee = text_on_page[i + 2]
            if text_on_page[i][15] == "D":
                party = "DEM"
            elif text_on_page[i][15] == "R":
                party = "REP"
            elif text_on_page[i][15] == "N":
                party = "NPA"
            else:
                party = " "
            line = [county, precint, office, " ", party, office, votes, mail_in, election_day, " ", absentee]
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

        if save and text_on_page[i] != "TOTAL" and name == 6:
            if office not in offices and office[0:3] != "REP":
                continue
            else:
                candidate = text_on_page[i]
                if candidate[-1:] == "%":
                    continue
                votes = text_on_page[i + 1]
                if votes[0] not in numbers :
                    continue
                election_day = text_on_page[i + 2]
                mail_in = str(int(text_on_page[i + 4]) + int(text_on_page[i + 5]))
                absentee = text_on_page[i + 3]
                line = [county, precint, office, " ", party, candidate, votes, mail_in, election_day, " ", absentee]
                csv_writer.writerow(line)
                #print(line)
                name  = 1
        elif not name == 6:
            name += 1


if __name__ == "__main__":
    fd = open("mifflin_results_2020.pdf", "rb")
    doc = PDFDocument(fd)
    viewer = SimplePDFViewer(fd)
    parties = ["DEM", "REP", "NPA"]
    offices = ["PRESIDENT OF THE UNITED STATES", "ATTORNEY GENERAL", "AUDITOR GENERAL", "STATE TREASURER"]
    numbers = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']
    all_pages = [p for p in doc.pages()]
    with open('20200602__pa__primary__mifflin__precinct.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["county", "precint", "office", "district", "party", "candidate", "votes", "early_voting", "election_day", "provisional", "absentee"])
        for i in range(len((all_pages))):
        #for i in range(5):
            viewer.navigate(i + 1)
            viewer.render()
            text_on_page = viewer.canvas.strings
            #print(text_on_page)
            scraping_one_page(text_on_page, writer)
            print("We are " + str((i / len(all_pages)) * 100) + "% done.")
