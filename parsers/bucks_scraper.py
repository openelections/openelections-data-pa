import pdfreader
from pdfreader import PDFDocument, SimplePDFViewer
import csv

precinct = " "
partial = False
office = " "
party = " "

def scraping_one_page(text_on_page, writer):
    global office
    global partial
    global precinct
    global party
    #print(text_on_page)
    parties = ["DEM", "REP", "NPA"]
    offices = ["PRESIDENT OF THE UNITED STATES", "ATTORNEY GENERAL", "AUDITOR GENERAL", "STATE TREASURER", "REPRESENTATIVE IN CONGRESS", "REPRESENTATIVE IN THE GENERAL ASSEMBLY"]
    county = "Bucks"
    if text_on_page[13][:8] == "Precinct":
        precinct = text_on_page[13][9:]
    save = False
    name = False
    #print(precinct)
    if partial:
        i = 12
        race = True
        j = 1
        while race:
            candidate = text_on_page[i + j]
            if candidate[:5] == "Page:":
                partial = True
                break
            if " - " in candidate:
                candidate = candidate[:candidate.index(" - ")]
            votes = text_on_page[i + j + 1]
            absentee = text_on_page[i + j + 3]
            early = text_on_page[i + j + 4]
            provisional = text_on_page[i + j + 5]
            line = [county, precinct, office, " ", party, candidate, votes, absentee, early, provisional]
            if candidate == "Total":
                race = False
                partial = False
            #print(line)
            #writer.writerow(line)
            j += 6
    for i in range(len(text_on_page)):
        if " - " in text_on_page[i]:
            holding = text_on_page[i][:text_on_page[i].index(" - ")]
            if holding in offices or holding[:-6] in offices or holding[:-5] in offices:
                office = holding
                j = 1
                race = True
                if "Democr" in text_on_page[i]:
                    party = "DEM"
                elif "Republ" in text_on_page[i]:
                    party = "REP"
                else:
                    party = "NPA"
                while race:
                    candidate = text_on_page[i + j]
                    if candidate[:5] == "Page:":
                        partial = True
                        break
                    if " - " in candidate:
                        candidate = candidate[:candidate.index(" - ")]
                    votes = text_on_page[i + j + 1]
                    absentee = text_on_page[i + j + 3]
                    early = text_on_page[i + j + 4]
                    provisional = text_on_page[i + j + 5]
                    line = [county, precinct, office, " ", party, candidate, votes, absentee, early, provisional]
                    if candidate == "Total":
                        race = False
                    #print(line)
                    writer.writerow(line)
                    j += 6


if __name__ == "__main__":
    fd = open("bucks_results_2020.pdf", "rb")
    doc = PDFDocument(fd)
    viewer = SimplePDFViewer(fd)
    all_pages = [p for p in doc.pages()]
    with open('20200602__pa__primary__bucks__precinct.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["County", "Precinct", "Office", "District", "Party", "Candidate", "Votes", "Absentee", "Early", "Provisional"])
        for i in range(len((all_pages))):
        #for i in range(7):
            viewer.navigate(i + 1)
            viewer.render()
            text_on_page = viewer.canvas.strings
            scraping_one_page(text_on_page, writer)
            print("We are " + str((i / len(all_pages)) * 100) + "% done.")
