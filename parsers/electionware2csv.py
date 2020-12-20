import csv
import sys

from pdfminer.converter import PDFPageAggregator
from pdfminer.layout import LAParams
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
import pdfminer

def y(obj):
    return (obj.bbox[3], obj.bbox[2])

all_data = {}

with open(sys.argv[1], 'rb') as in_file:
    parser = PDFParser(in_file)
    doc = PDFDocument(parser)
    rsrcmgr = PDFResourceManager()
    device = PDFPageAggregator(rsrcmgr, laparams=LAParams())
    interpreter = PDFPageInterpreter(rsrcmgr, device)
    current_precinct = None
    columns = None
    column_names = None
    for page in PDFPage.create_pages(doc):
        interpreter.process_page(page)
        layout = device.get_result()
        objects = []
        for obj in layout._objs:
            # if it's a textbox, print text and location
            if isinstance(obj, pdfminer.layout.LTTextBoxHorizontal):
                objects.append(obj)
            elif isinstance(obj, pdfminer.layout.LTRect):
                objects.append(obj)
            # else:
            #     print(obj)
        objects.sort(key=y, reverse=True)
        line_items = False
        last_value = None
        last_heading = None
        county = objects[2].get_text().strip()
        #print(county)
        precinct = objects[4].get_text().strip()
        if current_precinct != precinct:
            #print("precinct", precinct)
            pass
        current_precinct = precinct
        current_office = None
        boxes = []
        headers = []
        for obj in objects:
            if isinstance(obj, pdfminer.layout.LTRect):
                if obj.bbox[0] == 20:
                    # Background of statistics
                    continue
                # print("box", obj)
                line_items = False
                boxes.append(obj.bbox)
                continue
            textbox = obj
            text = textbox.get_text().strip()
            if text == "TOTAL":
                if columns == None:
                    columns = len(boxes)

                headers.append(textbox)
                # print()
                # print(headers)
                last_heading = headers[-columns - 1].get_text()
                these_column_names = headers[-columns:]
                these_column_names.sort(key=lambda x: x.bbox[0])
                these_column_names = [x.get_text().strip().replace("\n", " ") for x in these_column_names]
                if not column_names:
                    column_names = these_column_names
                elif column_names != these_column_names:
                    print(column_names, these_column_names)
                    raise RuntimeError("Column names change")
                current_office = last_heading
                if current_office not in all_data:
                    all_data[current_office] = {}
                if current_precinct not in all_data[current_office]:
                    all_data[current_office][current_precinct] = {}
                #print(last_heading.replace("\n", "-"))
                line_items = True
                line = 0
                values = [None] * columns
            elif line_items:
                if textbox.bbox[0] == 20:
                    if all((x is None for x in values)):
                        line_items = False
                        boxes = []
                        headers = [textbox]
                    else:
                        all_data[current_office][current_precinct][text] = values
                        #print("\t", text, list(reversed(values)))
                        values = [None] * columns
                else:
                    i = 0
                    while boxes[i][0] > textbox.bbox[0]:
                        i += 1
                    if i >= len(boxes):
                        line_items = False
                        boxes = []
                        headers = [text]
                    else:
                        values[i] = text
            else:
                headers.append(textbox)

            #print(textbox.bbox, textbox.get_text().replace('\n', '_'))
        # break

with open('20200602__pa__primary__tioga__precinct.csv', 'wt') as output_file:
    writer = csv.writer(output_file)
    writer.writerow(["county","precinct","office","candidate"] + column_names)
    for key in sorted(all_data.keys()):
        if key.strip() != "STATISTICS":
            office, _ = key.strip().split("\n")
        else:
            office = key.strip()
        precincts = all_data[key]
        candidates = all_data[key][list(precincts.keys())[0]].keys()
        for candidate in sorted(candidates):
            for precinct in sorted(precincts.keys()):
                votes = all_data[key][precinct][candidate]
                writer.writerow([county,precinct,office,candidate] + votes)
