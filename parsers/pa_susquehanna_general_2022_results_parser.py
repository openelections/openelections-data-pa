#!/usr/bin/env python3
"""Parse Susquehanna County 2022 General Election Precinct Summary Report
(Electionware-style two-column per-precinct summary) into the OpenElections
standardized CSV format.

Source: "Susquehanna PA 2022 General Precinct Results.pdf" - 41 pages, one
per election district. Each page is a two-column "Precinct Summary Report"
with contests, a Total Votes line, and candidate rows of the form
    NAME (PARTY)   votes  pct%
with no election-day/absentee/mail/provisional breakdown columns, so a
7-column output is produced.

Usage:
    python parsers/pa_susquehanna_general_2022_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys

import pdfplumber

SPLIT_X = 300  # left column ends before this, right column starts at ~315

# Office header detection and normalization
OFFICE_PATTERNS = [
    (re.compile(r"UNITED STATES SENATOR", re.I), "U.S. Senate", ""),
    (re.compile(r"REPRESENTATIVE IN CONGRESS\s+(\d+)"), "U.S. House", None),
    (re.compile(r"GOVERNOR AND LT\s*GOVERNOR", re.I), "Governor", ""),
    (re.compile(r"SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)"), "State Senate", None),
    (re.compile(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)"), "State House", None),
    (re.compile(r"SENATOR IN THE GENERAL ASSEMBLY"), "State Senate", ""),
    (re.compile(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY"), "State House", ""),
]

CAND_RE = re.compile(r"^(.*?)\s+(-?\d+)\s+(-?[\d.]+)%\s*$")
TOTAL_RE = re.compile(r"^Total Votes\s+([\d,]+)\s*$", re.I)
VOTEFOR_RE = re.compile(r"^Vote For\s+\d+\s*$", re.I)
RV_RE = re.compile(r"Registered Voters\s+([\d,]+)\s*-\s*Total Ballots\s+([\d,]+)")


def detect_office(line):
    """Return (office, district) if line is an office header, else None."""
    for pattern, office, dist in OFFICE_PATTERNS:
        m = pattern.search(line)
        if m:
            if dist == "" and not m.lastindex:
                return office, ""
            dist = m.group(1) if (dist is None and m.lastindex) else ""
            return office, dist
    return None


def title_name(name):
    """Title-case a candidate name sensibly."""
    small = {"and", "or", "of"}
    out = []
    for word in name.split():
        if len(word) == 1:
            out.append(word.upper())
        elif word.lower() in small:
            out.append(word.lower())
        elif re.match(r"^[A-Za-z]\.$", word):
            out.append(word[0].upper() + ".")
        elif word.isupper() or word.islower():
            out.append(word[0].upper() + word[1:].lower())
        else:
            out.append(word)
    s = " ".join(out)
    s = re.sub(r"\s*/\s*", " / ", s)
    return s


def parse_column(lines, rows, precinct):
    """Parse one column of office/candidate lines, appending rows to `rows`.

    Candidate names can wrap across lines; the vote count appears on the first
    line and the rest of the name (and often the party tag) follows. A pending
    record absorbs continuation lines until the next structured line.
    """
    office, district = "", ""
    contest_totals = {}
    pending = None

    def flush():
        nonlocal pending
        if pending is not None:
            name = pending["name"].strip()
            party = pending["party"]
            if re.match(r"^write[\s-]*in", name, re.I):
                name, party = "Write Ins", ""
            rows.append([
                precinct,
                pending["office"],
                pending["district"],
                party,
                title_name(name),
                pending["votes"],
            ])
            pending = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        hit = detect_office(line)
        if hit:
            flush()
            office, district = hit
            continue
        if VOTEFOR_RE.match(line):
            flush()
            continue
        tm = TOTAL_RE.match(line)
        if tm:
            flush()
            contest_totals.setdefault(
                (office, district), int(tm.group(1).replace(",", ""))
            )
            continue
        cm = CAND_RE.match(line)
        if cm:
            flush()
            name, votes = cm.group(1).strip(), int(cm.group(2))
            party = ""
            pm = re.search(r"\(([A-Za-z]{3})\)\s*$", name)
            if pm:
                party = pm.group(1).upper()
                name = re.sub(r"\s*\([A-Za-z]{3}\)\s*$", "", name).strip()
            pending = {
                "office": office,
                "district": district,
                "party": party,
                "name": (name + " ").strip(),
                "votes": votes,
            }
            continue
        # continuation line of a wrapped candidate name
        if pending is not None:
            text = line
            pm = re.search(r"\(([A-Za-z]{3})\)\s*$", text)
            if pm and not pending["party"]:
                pending["party"] = pm.group(1).upper()
                text = re.sub(r"\s*\([A-Za-z]{3}\)\s*$", "", text).strip()
            if text:
                pending["name"] = (pending["name"] + " " + text).strip()
        # else: ignore stray text
    flush()
    return {f"{o} {d}".strip(): v for (o, d), v in contest_totals.items()}


def cluster_lines(words, tol=3):
    """Group words into lines by vertical position, sorted by x."""
    rows = {}
    for w in words:
        key = None
        for k in rows:
            if abs(k - w["top"]) <= tol:
                key = k
                break
        if key is None:
            key = w["top"]
        rows.setdefault(key, []).append(w)
    out = []
    for k in sorted(rows):
        ws = sorted(rows[k], key=lambda w: w["x0"])
        out.append(" ".join(w["text"] for w in ws))
    return out


def main():
    if len(sys.argv) != 3:
        print("Usage: pa_susquehanna_general_2022_results_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    src, dest = sys.argv[1], sys.argv[2]

    rows = []  # [precinct, office, district, party, candidate, votes]
    verification = []  # per precinct: {contest: printed Total Votes}
    with pdfplumber.open(src) as pdf:
        for page in pdf.pages:
            words = [w for w in page.extract_words() if 115 < w["top"] < 760]
            left = [w for w in words if w["x0"] < SPLIT_X]
            right = [w for w in words if w["x0"] >= SPLIT_X]

            # precinct name: line between RESULTS and UNOFFICAL RESULTS
            name_words = [w for w in page.extract_words() if 59 < w["top"] < 90]
            name_lines = cluster_lines(name_words)
            precinct = re.sub(r"UNOFFICAL.*", "", name_lines[0]).strip() if name_lines else ""

            # registered voters / ballots cast
            txt = page.extract_text() or ""
            mv = RV_RE.search(txt)
            if mv:
                rows.append([precinct, "Registered Voters", "", "", "",
                             int(mv.group(1).replace(",", ""))])
                rows.append([precinct, "Ballots Cast", "", "", "",
                             int(mv.group(2).replace(",", ""))])

            left_lines = cluster_lines(left)
            right_lines = cluster_lines(right)

            def after_rv(lines):
                for i, l in enumerate(lines):
                    if "Registered Voters" in l:
                        return lines[i + 1:]
                return lines

            sums_l = parse_column(after_rv(left_lines), rows, precinct)
            sums_r = parse_column(after_rv(right_lines), rows, precinct)
            verification.append((precinct, {**sums_l, **sums_r}))

    with open(dest, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["county", "precinct", "office", "district", "party", "candidate", "votes"])
        for precinct, office, district, party, candidate, votes in rows:
            wtr.writerow(["Susquehanna", precinct, office, district, party, candidate, votes])
    print(f"wrote {len(rows)} rows to {dest}")

    # emit source-printed Total Votes per precinct/contest for verification
    vpath = dest.rsplit(".", 1)[0] + "_source_totals.csv"
    with open(vpath, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["precinct", "contest", "source_total"])
        for precinct, sums in verification:
            for contest, total in sums.items():
                wtr.writerow([precinct, contest, total])
    print(f"wrote source totals to {vpath}")


if __name__ == "__main__":
    main()