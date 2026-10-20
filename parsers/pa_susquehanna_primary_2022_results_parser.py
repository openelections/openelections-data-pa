#!/usr/bin/env python3
"""Parse Susquehanna County 2022 General Primary "Precinct Summary Report"
(two-column per-precinct summary, same vendor/print family as the county's
2022 general precinct summary) into the OpenElections standardized CSV format.

Source: "Susquehanna PA 2022 Primary Precinct Results.pdf" - 82 pages, two per
election district (41 precincts). Each page is a two-column "Precinct Summary
Report" with contests, a Registered Voters/Total Ballots line, a Party
Distribution block, and per-contest candidate rows of the form
    NAME   votes  pct%
with no election-day/absentee/mail/provisional breakdown columns, so a
7-column output is produced.

Primary vs general differences handled here:
- Contests are per ballot party: the office title line is followed by a
  separate "(REPUBLICAN)" / "(DEMOCRATIC)" line that supplies the row's
  `party` (candidate rows carry no party tag, so WRITE-IN rows inherit the
  contest's party).
- A Party Distribution block sits below the RV/Total Ballots line; its rows
  ("DEMOCRATIC 27 27.00%") are skipped.
- Candidate rows are only accepted once the current contest has both a
  "Vote For" and a "Total Votes" line. This skips the Party Distribution
  rows and the overlapping left-column "Total Ballots" line, which some
  pages extract as a doubled-letter garble (e.g. "TToottaall BVaoltleosts 100").
- "MEMBER OF DEM THE STATE COMMITTEE" maps to the OpenElections
  "Member of Democratic State Committee" (and REP analog), per the
  2022-primary brief. Per-precinct township "COUNTY COMMITTEE" contests are
  kept as local party committee offices ("Democratic County Committee" /
  "Republican County Committee"), Indiana 2022-primary precedent; the
  township name is already the row's precinct.

Usage:
    python parsers/pa_susquehanna_primary_2022_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys

import pdfplumber

SPLIT_X = 300  # left column ends before this, right column starts at ~315
NAME_TOP_LO, NAME_TOP_HI = 55, 75  # precinct-name band in the page header

STATE_COMMITTEE_RE = re.compile(
    r"MEMBER\s+OF\s+(DEM|REP)\s+THE\s+STATE\s+COMMITTEE", re.I)
COUNTY_COMMITTEE_RE = re.compile(r"^(.*?)\s+COUNTY\s+COMMITTEE\s*$", re.I)
CAND_RE = re.compile(r"^(.*?)\s+(-?\d+)\s+(-?[\d.]+)%\s*$")
TOTAL_RE = re.compile(r"^Total Votes\s+([\d,]+)\s*$", re.I)
VOTEFOR_RE = re.compile(r"^Vote For\s+\d+\s*$", re.I)
PARTY_LINE_RE = re.compile(r"^\s*\(\s*(REPUBLICAN|DEMOCRATIC)\s*\)\s*$", re.I)
RV_RE = re.compile(r"Registered Voters\s+([\d,]+)\s*-\s*Total Ballots\s+([\d,]+)")

OFFICE_PATTERNS = [
    (re.compile(r"UNITED STATES SENATOR", re.I), "U.S. Senate"),
    (re.compile(r"REPRESENTATIVE IN CONGRESS\s+(\d+)", re.I), "U.S. House"),
    (re.compile(r"^LIEUTENANT GOVERNOR", re.I), "Lieutenant Governor"),
    (re.compile(r"^GOVERNOR", re.I), "Governor"),
    (re.compile(r"SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)", re.I), "State Senate"),
    (re.compile(r"REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)", re.I), "State House"),
]


def detect_office(line):
    """Return (office, district) if line is an office header, else None."""
    sm = STATE_COMMITTEE_RE.search(line)
    if sm:
        office = ("Member of Democratic State Committee" if sm.group(1).upper() == "DEM"
                  else "Member of Republican State Committee")
        return office, ""
    cm = COUNTY_COMMITTEE_RE.match(line)
    if cm:
        # party is unknown until the (PARTY) line; resolved at record time
        return "COUNTY COMMITTEE", ""
    for pattern, office in OFFICE_PATTERNS:
        m = pattern.search(line)
        if m:
            dist = m.group(1) if m.lastindex else ""
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


def parse_column(lines, precinct, seen):
    """Parse one column of office/candidate lines.

    Appends result dicts to `seen`["rows"] and source-printed contest totals
    to `seen`["totals"]. Candidate rows are valid only inside a complete
    contest section (Vote For AND Total Votes seen since the office header).
    """
    office, district, party = "", "", ""
    pending = None
    seen_votefor = seen_total = False

    def flush():
        nonlocal pending
        if pending is None:
            return
        name = pending["name"].strip()
        if re.match(r"^write[\s-]*in$", name, re.I):
            name = "Write Ins"
        if office == "COUNTY COMMITTEE":
            out_office = ("Democratic County Committee" if party == "DEM"
                          else "Republican County Committee")
        else:
            out_office = office
        seen["rows"].append({
            "precinct": precinct,
            "office": out_office,
            "district": pending["district"],
            "party": pending["party"] or "",
            "candidate": name if name == "Write Ins" else title_name(name),
            "votes": pending["votes"],
        })
        pending = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        pl = PARTY_LINE_RE.match(line)
        if pl:
            flush()
            party = {"REPUBLICAN": "REP", "DEMOCRATIC": "DEM"}[pl.group(1).upper()]
            continue
        hit = detect_office(line)
        if hit:
            flush()
            office, district = hit
            seen_votefor = seen_total = False
            continue
        if re.match(r"^Party Distribution", line, re.I):
            flush()
            continue
        if VOTEFOR_RE.match(line):
            flush()
            seen_votefor = True
            continue
        tm = TOTAL_RE.match(line)
        if tm:
            flush()
            seen_total = True
            tot_office = ("Democratic County Committee" if party == "DEM"
                          else "Republican County Committee") if office == "COUNTY COMMITTEE" else office
            seen["totals"].append((precinct,
                                   f"{tot_office}|{district}|{party}",
                                   int(tm.group(1).replace(",", ""))))
            continue
        cm = CAND_RE.match(line)
        if cm and office and seen_votefor and seen_total:
            flush()
            pending = {
                "office": office,
                "district": district,
                "party": party,
                "name": cm.group(1).strip(),
                "votes": int(cm.group(2)),
            }
            continue
        # continuation line of a wrapped candidate name (none observed, but
        # the print wraps long names on the general's pages)
        if pending is not None and not cm:
            pending["name"] = (pending["name"] + " " + line).strip()
        # else: ignore stray text (RV/garbled "Total Ballots" etc.)
    flush()


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
        print("Usage: pa_susquehanna_primary_2022_results_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    src, dest = sys.argv[1], sys.argv[2]

    seen = {"rows": [], "totals": []}
    precinct_order = []  # first-seen order
    page_precincts = []  # every page's parsed precinct, in page order

    with pdfplumber.open(src) as pdf:
        for page in pdf.pages:
            words = [w for w in page.extract_words() if 110 < w["top"] < 760]
            left = [w for w in words if w["x0"] < SPLIT_X]
            right = [w for w in words if w["x0"] >= SPLIT_X]

            # precinct name: header line between RESULTS and UNOFFICIAL RESULTS
            head_words = [w for w in page.extract_words()
                          if NAME_TOP_LO < w["top"] < NAME_TOP_HI]
            head_lines = cluster_lines(head_words)
            precinct = ""
            for hl in head_lines:
                hl = hl.strip()
                # skip the "RESULTS Page N/2" and "UNOFFICIAL RESULTS" lines
                if re.match(r"^(RESULTS\b|Page\b|UNOFFI)", hl, re.I):
                    continue
                precinct = hl
                break
            if not precinct:
                raise SystemExit(f"no precinct name found on page")
            page_precincts.append(precinct)

            txt = page.extract_text() or ""
            if precinct not in precinct_order:
                mv = RV_RE.search(txt)
                if mv:
                    seen["rows"].extend([
                        {"precinct": precinct, "office": "Registered Voters",
                         "district": "", "party": "", "candidate": "",
                         "votes": int(mv.group(1).replace(",", ""))},
                        {"precinct": precinct, "office": "Ballots Cast",
                         "district": "", "party": "", "candidate": "",
                         "votes": int(mv.group(2).replace(",", ""))},
                    ])
                    precinct_order.append(precinct)

            def after_rv(lines):
                for i, l in enumerate(lines):
                    if "Registered Voters" in l or "Party Distribution" in l:
                        return lines[i + 1:]
                return lines

            for column in (after_rv(cluster_lines(left)),
                           after_rv(cluster_lines(right))):
                parse_column(column, precinct, seen)

    # sanity: RV/BC found for every distinct precinct
    missing = set(page_precincts) - set(precinct_order)
    if missing:
        print("WARNING no RV/BC line parsed for:", sorted(missing))

    with open(dest, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["county", "precinct", "office", "district", "party",
                      "candidate", "votes"])
        n = 0
        for r in seen["rows"]:
            wtr.writerow(["Susquehanna", r["precinct"], r["office"],
                          r["district"], r["party"], r["candidate"], r["votes"]])
            n += 1
    print(f"wrote {n} rows ({len(precinct_order)} precincts) to {dest}")

    # emit source-printed Total Votes per precinct/contest for verification
    vpath = dest.rsplit(".", 1)[0] + "_source_totals.csv"
    with open(vpath, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["precinct", "office", "district", "party", "source_total"])
        for precinct, contest, total in seen["totals"]:
            office, district, party = contest.split("|")
            wtr.writerow([precinct, office, district, party, total])
    print(f"wrote {len(seen['totals'])} source contest totals to {vpath}")


if __name__ == "__main__":
    main()