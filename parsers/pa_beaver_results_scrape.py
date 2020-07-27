import pdfreader
from pdfreader import PDFDocument, SimplePDFViewer
import csv

def scraping_one_page(text_on_page, csv_writer):
    county = text_on_page[4][:-7]
    precint = text_on_page[5]
    save = False
    name = False
    for i in range(len(viewer.canvas.strings) - 3):
        if text_on_page[i] == "STATISTICS":
            #print(text_on_page[i])
            votes = text_on_page[i + 2]
            office = text_on_page[i + 3]
            line = [county, precint, office, " ", " ", office, votes]
            csv_writer.writerow(line)
            votes = text_on_page[i + 4]
            office = text_on_page[i + 5]
            line = [county, precint, office, " ", "DEM", office, votes]
            csv_writer.writerow(line)
            votes = text_on_page[i + 6]
            office = text_on_page[i + 7]
            line = [county, precint, office, " ", "REP", office, votes]
            csv_writer.writerow(line)
            votes = text_on_page[i + 8]
            office = text_on_page[i + 9]
            line = [county, precint, office, " ", "NPA", office, votes]
            csv_writer.writerow(line)
            votes = text_on_page[i + 10]
            office = text_on_page[i + 11]
            line = [county, precint, office, " ", " ", office, votes]
            csv_writer.writerow(line)


        if text_on_page[i] == "Total Votes Cast":
            save = False

        if text_on_page[i][0:3] in parties:
            party = text_on_page[i][0:3]
            save = True
            name = True
            office = text_on_page[i][4:]
            continue

        if save and text_on_page[i] != "TOTAL" and name:
            if office not in offices and office[0:3] != "REP":
                continue
            else:
                candidate = text_on_page[i]
                votes = text_on_page[i + 1]
                line = [county, precint, office, " ", party, candidate, votes]
                csv_writer.writerow(line)
                name = False
        elif not name:
            name = True


if __name__ == "__main__":
    fd = open("beaver_results_2020.pdf", "rb")
    doc = PDFDocument(fd)
    viewer = SimplePDFViewer(fd)
    parties = ["DEM", "REP", "NPA"]
    offices = ["PRESIDENT OF THE UNITED STATES", "ATTORNEY GENERAL", "AUDITOR GENERAL", "STATE TREASURER"]
    all_pages = [p for p in doc.pages()]
    with open('20200602__pa__primary__beaver__precinct.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["county", "precint", "office", "district", "party", "candidate", "votes"])
        for i in range(len((all_pages))):
            viewer.navigate(i + 1)
            viewer.render()
            text_on_page = viewer.canvas.strings
            scraping_one_page(text_on_page, writer)
            print("We are " + str((i / len(all_pages)) * 100) + "% done.")
