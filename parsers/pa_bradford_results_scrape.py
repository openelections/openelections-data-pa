import pdfreader
from pdfreader import PDFDocument, SimplePDFViewer
import csv


def double_race_page(text_on_page, csv_writer):
    office = " "
    party = " "
    candidates = []
    races = []
    for i in range(len(text_on_page)):
        if text_on_page[i] in offices or "CONGRESS" in text_on_page[i]:
            office = text_on_page[i]
            party = text_on_page[i + 1][1:4]
            if "GENERAL" in text_on_page[i]:
                if "ASSEMBLY" not in text_on_page[i] or "ASSEMBLY" not in text_on_page[i + 1]:
                    continue
            if party not in parties:
                party = " "
            if text_on_page[i][-7:] == "GENERAL":
                office = office = text_on_page[i] + " " + text_on_page[i + 1]
            races.append([office, party])
        if text_on_page[i] == "Votes":
            j = 1
            while j < 1000:
                if "Write-in" in text_on_page[i + j] or "Write-in" in text_on_page[i + j + 1]:
                    candidate_name = "Write-in"
                    j = 1001
                    candidates.append(candidate_name)
                    break

                candidate_name = text_on_page[i + j] + " " + text_on_page[i + j + 1]
                #print(text_on_page[i + j])
                if " " in text_on_page[i + j]:
                    candidate_name = text_on_page[i + j]
                    j -= 1

                j += 2
                #print(candidate_name)
                candidates.append(candidate_name)

        if "BOROUGH" in text_on_page[i] or "TOWNSHIP" in text_on_page[i]:
            district = text_on_page[i]

            for j in range(len(candidates)):
                if j <= candidates.index("Write-in"):
                    election_day = text_on_page[i + (j * 2) + 4]
                    absentee = text_on_page[i + (j * 2) + 4 + 5 + (len(candidates) * 2)]
                    mail_in = text_on_page[i + (j * 2) + 4 + 5 + (len(candidates) * 2) + 5 + (len(candidates) * 2)]
                    try:
                        provisional = text_on_page[i + (j * 2) + 4 + 5 + (len(candidates) * 2) + 5 + (len(candidates) * 2)+ 5 + (len(candidates) * 2)]
                    except IndexError:
                        provisional = " "
                    if len(races) > 0:
                        office = races[0][0]
                        party = races[0][1]
                else:
                    election_day = text_on_page[i + (j * 2) + 6]
                    absentee = text_on_page[i + (j * 2) + 6 + 5 + (len(candidates) * 2)]
                    mail_in = text_on_page[i + (j * 2) + 6 + 5 + (len(candidates) * 2) + 5 + (len(candidates) * 2)]
                    try:
                        provisional = text_on_page[i + (j * 2) + 6 + 5 + (len(candidates) * 2) + 5 + (len(candidates) * 2) + 5 + (len(candidates) * 2)]
                    except IndexError:
                        provisional = " "
                    if len(races) > 1:
                        office = races[1][0]
                        party = races[1][1]
                line = ["Bradford", district, office, " ", party, candidates[j], election_day, absentee, mail_in, provisional]
                #print(line)
                if office != " ":
                    csv_writer.writerow(line)
    """
    print(races)
    print(candidates)
    """

def presidental_race(text_on_page, csv_writer, office, party):
    candidates = []
    for i in range(len(text_on_page)):
        #candidates = []
        if text_on_page[i] == "Votes":
            j = 1
            while j < 1000:
                candidate_name = text_on_page[i + j] + " " + text_on_page[i + j + 1]
                #print(text_on_page[i + j])
                #print(candidate_name)
                if text_on_page[i + j] == "Write-in":
                    candidate_name = "Write-in"
                    j = 1001
                j += 2
                candidates.append(candidate_name)

        #print(candidates)
        if "BOROUGH" in text_on_page[i] or "TOWNSHIP" in text_on_page[i]:
            district = text_on_page[i]

            for j in range(len(candidates)):
                election_day = text_on_page[i + (j * 2) + 7]
                absentee = text_on_page[i + (j * 2) + 23]
                mail_in = text_on_page[i + (j * 2) + 39]
                try:
                    provisional = text_on_page[i + (j * 2) + 55]
                except IndexError:
                    provisional = " "
                line = ["Bradford", district, office, " ", party, candidates[j], election_day, absentee, mail_in, provisional]
                ##print(line)
                if office != " ":
                    csv_writer.writerow(line)

            for l in range(4):
                cast = text_on_page[i + 3 + (l * 16)]
                if cast == "-":
                    cast = "0"
                line = ["Bradford", district, office, " ", party, "Total", "0", "0", "0", "0"]
                line[6 + l] = cast
                ##print(line)
                if office != " ":
                    csv_writer.writerow(line)

def single_race(text_on_page, csv_writer):
    office = " "
    party = " "
    candidates = []
    for i in range(len(text_on_page)):
        if text_on_page[i] in offices or "CONGRESS" in text_on_page[i]:
            office = text_on_page[i]
            party = text_on_page[i + 1][1:4]
            if "GENERAL" in text_on_page[i]:
                if "ASSEMBLY" not in text_on_page[i] or "ASSEMBLY" not in text_on_page[i + 1]:
                    continue
            if party not in parties:
                party = " "
            if text_on_page[i][-7:] == "GENERAL":
                office = office = text_on_page[i] + " " + text_on_page[i + 1]
        if text_on_page[i] == "Votes":
            j = 1
            while j < 1000:
                candidate_name = text_on_page[i + j] + " " + text_on_page[i + j + 1]
                #print(text_on_page[i + j])
                if " " in text_on_page[i + j]:
                    candidate_name = text_on_page[i + j]
                    j -= 1

                if "Write-in" in text_on_page[i + j] or "Write-in" in text_on_page[i + j + 1] :
                    candidate_name = "Write-in"
                    j = 1001
                j += 2
                #print(candidate_name)
                if candidate_name != "H  (W)":
                    candidates.append(candidate_name)

        if "BOROUGH" in text_on_page[i] or "TOWNSHIP" in text_on_page[i]:
            district = text_on_page[i]

            for j in range(len(candidates)):
                election_day = text_on_page[i + (j * 2) + 4]
                absentee = text_on_page[i + (j * 2) + 4 + 3 + (len(candidates) * 2)]
                mail_in = text_on_page[i + (j * 2) + 4 + 3 + (len(candidates) * 2) + 3 + (len(candidates) * 2)]
                try:
                    provisional = text_on_page[i + (j * 2) + 4 + 3 + (len(candidates) * 2) + 3 + (len(candidates) * 2) + 3 + (len(candidates) * 2)]
                except IndexError:
                    provisional = " "
                line = ["Bradford", district, office, " ", party, candidates[j], election_day, absentee, mail_in, provisional]
                #print(line)
                if office != " ":
                    csv_writer.writerow(line)

    #print(candidates)


def scraping_one_page(text_on_page, csv_writer):
    number_of_races = 0
    candidates = []
    for i in range(len(text_on_page)):
        if text_on_page[i] in offices or "CONGRESS" in text_on_page[i]:
            office = text_on_page[i]
            party = text_on_page[i + 1][1:4]
            number_of_races += 1
        if "GENERAL" in text_on_page[i]:
            if "ASSEMBLY" in text_on_page[i] or "ASSEMBLY" in text_on_page[i + 1]:
                office = text_on_page[i]
                party = text_on_page[i + 1][1:4]
                number_of_races += 1


    #print(number_of_races)
    if "PRESIDENT OF THE UNITED STATES" in text_on_page:
        presidental_race(text_on_page, csv_writer, office, party)
    elif number_of_races == 1:
        single_race(text_on_page,csv_writer)
    else:
        double_race_page(text_on_page, csv_writer)

if __name__ == "__main__":
    fd = open("bradford_results_2020.pdf", "rb")
    doc = PDFDocument(fd)
    viewer = SimplePDFViewer(fd)
    parties = ["DEM", "REP", "NPA"]
    offices = ["PRESIDENT OF THE UNITED STATES", "ATTORNEY GENERAL", "AUDITOR GENERAL", "STATE TREASURER"]
    all_pages = [p for p in doc.pages()]
    with open('20200602__pa__primary__bradford__precinct.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["county", "district", "office", "vote type", "party", "candidate", "election_day", "absentee", "mail-in", "provisional"])
        for i in range(len((all_pages))):
        #for i in range(3):
            viewer.navigate(i + 1)
            viewer.render()
            text_on_page = viewer.canvas.strings
            #print(text_on_page)
            scraping_one_page(text_on_page, writer)
            print("We are " + str((i / len(all_pages)) * 100) + "% done.")
