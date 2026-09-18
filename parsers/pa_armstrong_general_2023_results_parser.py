#!/usr/bin/env python3
"""
Parser for Armstrong County 2023 General Election precinct results.

Input: the zip file "Armstrong County Precinct Results 2023 General.zip"
(containing 62 per-precinct Electionware "Election Summary Report" PDFs,
1.pdf..62.pdf) OR a directory of the extracted PDFs.

Each PDF is one precinct's summary with per-contest Election Day / Mail /
Provisional / Total vote columns and write-in ("Scatter" / named WRITE-IN /
"Unresolved Write-In") rows. Text is extracted with pdftotext -layout, which
must be on PATH (as `pdftotext`).

Usage:
    python parsers/pa_armstrong_general_2023_results_parser.py \
        <zip_or_dir> <output_csv> [--county Armstrong]

All write-in rows of a contest (Scatter/Scattered/Scatters, identified
WRITE-IN candidates and Unresolved Write-In) are aggregated into a single
candidate row `Write-ins` with an empty party, per the repo convention.
Registered Voters / Ballots Cast metadata rows are emitted per precinct
(Ballots Cast keeps its ED/mail/provisional breakdown).
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

PARTIES = ["DEM/REP", "DEM", "REP", "LIB", "GRN", "CON", "IND"]

CAND_RE = re.compile(
    r"^(?P<name>\S.*?)\s{2,}(?P<party>" + "|".join(re.escape(p) for p in PARTIES) + r")\s+"
    r"(?P<ed>\d+)\s+(?P<mail>\d+)\s+(?P<prov>\d+)\s+(?P<total>\d+)\s*$"
)
YESNO_RE = re.compile(
    r"^(?P<name>YES|NO)\s+(?P<ed>\d+)\s+(?P<mail>\d+)\s+(?P<prov>\d+)\s+(?P<total>\d+)\s*$",
    re.IGNORECASE,
)
TOTALVOTES_RE = re.compile(
    r"^Total Votes\s+(?P<ed>\d+)\s+(?P<mail>\d+)\s+(?P<prov>\d+)\s+(?P<total>\d+)\s*$"
)
WRITEIN_RE = re.compile(
    r"^(?P<name>\S.*?)\s+WRITE-IN\s+(?P<ed>\d+)\s+(?P<mail>\d+)\s+(?P<prov>\d+)\s+(?P<total>\d+)\s*$",
    re.IGNORECASE,
)
UNRESOLVED_RE = re.compile(
    r"^Unresolved Write-In\s+(?P<ed>\d+)\s+(?P<mail>\d+)\s+(?P<prov>\d+)\s+(?P<total>\d+)\s*$",
    re.IGNORECASE,
)
SUMMARY_FOR_RE = re.compile(r"Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators")
BALLOTS_CAST_RE = re.compile(r"^Ballots Cast:\s*(\d+)\s*$")
NUM = r"(?P<num>[\d,]+)"
GROUP_TOTAL_START_RE = re.compile(
    r"^\s*Total\s+Election Day\s+" + NUM.replace("<num>", "<ed>") + r"\s+" + NUM + r""
)
GROUP_ROW_RE = re.compile(
    r"^\s*(?P<label>Election Day|Mail|Provisional)\s+"
    + NUM.replace("<num>", "<ballots>")
    + r"\s+"
    + NUM.replace("<num>", "<voters>")
)
GROUP_FINAL_RE = re.compile(
    r"^\s*Total\s+"
    + NUM.replace("<num>", "<ballots>")
    + r"\s+"
    + NUM.replace("<num>", "<voters>")
    + r"\s+(?P<rv>[\d,]+|N/A)\s+(?P<turnout>[\d.]+|N/A)%"
)
CONTEST_END_RE = re.compile(r"\(Vote for \d+\)\s*$")
METHOD_KEYS = {
    "Election Day": "election_day",
    "Mail": "mail",
    "Provisional": "provisional",
}


def pdf_to_text(pdf_path, workdir):
    """Convert one PDF to layout text, returning the text."""
    txt_path = os.path.join(workdir, os.path.basename(pdf_path) + ".txt")
    subprocess.run(
        ["pdftotext", "-layout", pdf_path, txt_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(txt_path, encoding="utf-8", errors="replace") as f:
        return f.read()


def merge_wrapped_headers(lines):
    """Join contest headers that wrap across lines.

    Patterns seen: '... Term' / '(Vote for 5)' on the next line, and
    '... (Vote for' / '5)'. Returns a new line list with headers joined.
    """
    out = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if re.fullmatch(r"\(Vote for \d+\)", s) and out:
            # standalone '(Vote for N)' — attach to the previous line
            out[-1] = out[-1].rstrip() + " " + s
            i += 1
            continue
        if re.search(r"\(Vote for \d*\)\s*$", s) and not re.search(
            r"\(Vote for \d+\)\s*$", s
        ):
            # ends with '(Vote for' — append following lines until ')'
            merged = s
            while not re.search(r"\(Vote for \d+\)\s*$", merged) and i + 1 < n:
                i += 1
                merged += " " + lines[i].strip()
            out.append(merged)
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def split_office(office):
    """Split an office header into (office, district)."""
    m = re.match(r"^Magisterial District Judge\s+(\S+)$", office, re.IGNORECASE)
    if m:
        return "Magisterial District Judge", m.group(1)
    m = re.match(
        r"^School Director(?: at Large)? in the (.+?) School District\s*(.*)$",
        office,
        re.IGNORECASE,
    )
    if m:
        suffix = m.group(2).strip()
        return ("School Director " + suffix).strip(), (m.group(1) + " School District")
    return office, ""


def parse_text(text, fallback_name):
    """Parse one precinct's report text into standardized row dicts."""
    lines = merge_wrapped_headers(text.splitlines())
    precinct = fallback_name
    for line in lines:
        m = SUMMARY_FOR_RE.search(line)
        if m:
            precinct = m.group(1).strip()
            break

    meta = []
    registered_voters = None
    bc_total = None
    bc_breakdown = {}

    rows = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        if registered_voters is None and GROUP_TOTAL_START_RE.match(line):
            # Elector-group totals: Election Day / Mail / Provisional / Total
            ed_b = int(GROUP_TOTAL_START_RE.match(line).group("ed").replace(",", ""))
            i += 1
            breakdown = {"election_day": ed_b}
            while i < n:
                m = GROUP_ROW_RE.match(lines[i])
                if m:
                    breakdown[METHOD_KEYS[m.group("label")]] = int(m.group("ballots").replace(",", ""))
                    i += 1
                    continue
                m = GROUP_FINAL_RE.match(lines[i])
                if m:
                    rv_txt = m.group("rv")
                    if rv_txt.isdigit():
                        registered_voters = int(rv_txt)
                    elif "," in rv_txt:
                        registered_voters = int(rv_txt.replace(",", ""))
                    bc_total = int(m.group("ballots").replace(",", ""))
                    bc_breakdown = breakdown
                    i += 1
                    break
                if lines[i].strip():
                    break
                i += 1
            continue

        if bc_total is None:
            m = BALLOTS_CAST_RE.search(line)
            if m:
                bc_total = int(m.group(1))

        # Contest header (may wrap over two lines: '... (Vote' / 'for 5)')
        if CONTEST_END_RE.search(stripped) and not stripped.startswith("Precincts Reported"):
            header = stripped
            while not CONTEST_END_RE.search(header) and i + 1 < n:
                i += 1
                nxt = lines[i].strip()
                if not nxt or nxt.startswith("Precincts Reported"):
                    break
                header += " " + nxt
            office_raw = re.sub(r"\s+", " ", re.sub(r"\(Vote for \d+\)\s*$", "", header)).strip()

            writeins = [0, 0, 0, 0]
            i += 1
            while i < n:
                s = lines[i].strip()
                if CONTEST_END_RE.search(s) and not s.startswith("Precincts Reported"):
                    break
                if BALLOTS_CAST_RE.search(s):
                    break
                if re.match(r"^(Candidate\s+Party|Election Day\s+Mail\s+Provisional\s+Total)", s):
                    i += 1
                    continue
                m = TOTALVOTES_RE.match(s)
                if m:
                    i += 1
                    continue
                m = CAND_RE.match(s)
                if m:
                    d = m.groupdict()
                    rows.append(
                        {
                            "office": office_raw,
                            "district": "",
                            "party": d["party"],
                            "candidate": d["name"].strip(),
                            "votes": int(d["total"]),
                            "election_day": int(d["ed"]),
                            "mail": int(d["mail"]),
                            "provisional": int(d["prov"]),
                        }
                    )
                    i += 1
                    continue
                m = YESNO_RE.match(s)
                if m:
                    d = m.groupdict()
                    rows.append(
                        {
                            "office": office_raw,
                            "district": "",
                            "party": "",
                            "candidate": d["name"].strip().title(),
                            "votes": int(d["total"]),
                            "election_day": int(d["ed"]),
                            "mail": int(d["mail"]),
                            "provisional": int(d["prov"]),
                        }
                    )
                    i += 1
                    continue
                m = WRITEIN_RE.match(s) or UNRESOLVED_RE.match(s)
                if m:
                    d = m.groupdict()
                    writeins[0] += int(d["ed"])
                    writeins[1] += int(d["mail"])
                    writeins[2] += int(d["prov"])
                    writeins[3] += int(d["total"])
                    i += 1
                    continue
                i += 1
            if any(writeins):
                rows.append(
                    {
                        "office": office_raw,
                        "district": "",
                        "party": "",
                        "candidate": "Write-ins",
                        "votes": writeins[3],
                        "election_day": writeins[0],
                        "mail": writeins[1],
                        "provisional": writeins[2],
                    }
                )
            continue

        i += 1

    if registered_voters is not None:
        meta.append(
            {
                "precinct": precinct,
                "office": "Registered Voters",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": registered_voters,
                "election_day": "",
                "mail": "",
                "provisional": "",
            }
        )
    if bc_total is not None:
        meta.append(
            {
                "precinct": precinct,
                "office": "Ballots Cast",
                "district": "",
                "party": "",
                "candidate": "",
                "votes": bc_total,
                "election_day": bc_breakdown.get("election_day", ""),
                "mail": breakdown.get("mail", ""),
                "provisional": breakdown.get("provisional", ""),
            }
        )

    for row in rows:
        row["precinct"] = precinct
    return meta + rows


def iter_pdfs(input_path, tmpdir):
    if os.path.isdir(input_path):
        yield from sorted(
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.lower().endswith(".pdf")
        )
        return
    with zipfile.ZipFile(input_path) as zf:
        zf.extractall(tmpdir)
    yield from sorted(
        os.path.join(tmpdir, f) for f in os.listdir(tmpdir) if f.lower().endswith(".pdf")
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_path", help="zip of per-precinct PDFs, or a directory of them")
    ap.add_argument("output_path", help="output standardized CSV path")
    ap.add_argument("--county", default="Armstrong")
    args = ap.parse_args(argv)

    if shutil.which("pdftotext") is None:
        print("error: pdftotext must be installed and on PATH", file=sys.stderr)
        return 1

    all_rows = []
    with tempfile.TemporaryDirectory() as tmp:
        workdir = os.path.join(tmp, "txt")
        os.makedirs(workdir, exist_ok=True)
        for pdf in iter_pdfs(args.input_path, tmp):
            text = pdf_to_text(pdf, workdir)
            fallback = os.path.splitext(os.path.basename(pdf))[0]
            rows = parse_text(text, fallback)
            if not rows:
                print("warning: no rows parsed from %s" % pdf, file=sys.stderr)
            all_rows.extend(rows)

    for row in all_rows:
        row["county"] = args.county
        office, district = split_office(row["office"])
        row["office"], row["district"] = office, district

    out_dir = os.path.dirname(os.path.abspath(args.output_path))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print("wrote {} rows to {}".format(len(all_rows), args.output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())