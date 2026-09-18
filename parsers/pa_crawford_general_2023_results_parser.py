#!/usr/bin/env python3
"""Parser for Crawford County 2023 General Election - Certified Local Races Results.

Source: "Crawford County Certified Local Races Results 2023 General" (86-page
certificate, "General Certificate of Result of the Votes Cast for Ward, Borough
and Township Offices").  The pre-extracted, layout-preserving text of that PDF
(pdftotext -layout) is the expected input.

The certificate is organized as one section per municipality:

    RESULTS OF VOTES CAST IN Athens Twp
    November 7, 2023 General Election
        OFFICE OF              CANDIDATE'S NAME           PARTY MACHINE   HAND   TOTALS
     SCHOOL DIRECTOR   ALLISON BEERS                       D/R     62      0      62
    PENNCREST SCHOOL   RANDY STYBORSKI                     D/R     60      0      60
         DISTRICT      TIFFANY A DONOR                      D      51      0      51
     FOUR YEAR TERM    SCATTERED                                    5       0       5
                       ...

The office label sits in a left column (wrapping over several rows) and each
candidate row carries MACHINE / HAND / TOTALS counts.  Only the TOTALS value is
emitted (machine/hand are not election-day/mail/provisional breakdowns), so the
breakdown columns of the output are left empty.

Municipality sections repeat their header on continuation pages; the trailing
pages of the certificate are summary/rollup tables (multi-column per-precinct
tables and per-school-district tables) that duplicate the per-municipality
data and are skipped.

The statewide/county-contest rows come from the separate scanned
"Certified State and County Results" certificate and are NOT produced by this
parser (see work2023/validate/crawford.md).

Usage:
    python parsers/pa_crawford_general_2023_results_parser.py <input_txt> <output_csv> [--county Crawford]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import OrderedDict

FIELDNAMES = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

# Fragments that BEGIN a contest (vs. continuation fragments like
# "FOUR YEAR TERM" / "PENNCREST SCHOOL").
PRIMARY_LABELS = {
    "SCHOOL DIRECTOR", "SUPERVISOR", "AUDITOR", "COUNCIL", "TREASURER",
    "CONTROLLER", "CONSTABLE", "MAYOR", "TAX COLLECTOR",
    "JUDGE OF ELECTION", "INSPECTOR OF ELECTION",
}

ROW = re.compile(
    r"^(?P<text>.*?)\s+(?:(?P<party>D/R|D\\R|[DRIL])\s+)?"
    r"(?P<mac>\d[\d,]*)\s+(?P<hand>\d[\d,]*)\s+(?P<tot>\d[\d,]*)\s*$")
MUNI = re.compile(r"RESULTS OF VOTES CAST IN (.+?)\s*$")

TERM_WORDS = {"SIX": 6, "FOUR": 4, "TWO": 2}

SCHOOL_DISTRICT_TOKENS = ("SCHOOL", "CENTRAL", "AREA", "REGION")


def full_label(frag: str) -> str:
    """Expand a left-column office fragment that the scan truncated
    (e.g. 'AUDITO' for 'AUDITOR') to the unique matching primary label."""
    f = frag.strip().upper()
    if len(f) < 4 or f in PRIMARY_LABELS:
        return frag
    matches = [p for p in PRIMARY_LABELS if p.startswith(f)]
    return matches[0] if len(matches) == 1 else frag


def parse_txt(path: str):
    """Yield (municipality, label, candidate, party, votes) tuples."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    muni = None
    label_parts: list[str] = []
    current: list[tuple] = []  # rows of the open contest
    results: list[tuple] = []

    def flush():
        nonlocal current, label_parts
        if current and muni is not None:
            results.append((muni, " ".join(label_parts), list(current)))
        current = []
        label_parts = []

    i = 0
    n = len(lines)
    while i < n:
        raw = lines[i]

        # Page break: decide whether the next page is another municipality
        # section (continue parsing) or a summary/rollup page (stop).
        if raw.startswith("\x0c"):
            body = raw.lstrip("\x0c")
            nxt = lines[i + 1] if i + 1 < n else ""
            if "RESULTS OF VOTES CAST IN" in body:
                pass  # header on the same line, handled below
            elif "RESULTS OF VOTES CAST IN" not in nxt and not MUNI.search(body):
                break  # rollup pages begin; everything after is duplicate data

        m = MUNI.search(raw)
        if m:
            new_muni = m.group(1).strip()
            if new_muni != muni:
                flush()
                muni = new_muni
            i += 1
            continue

        if muni is None:
            i += 1
            continue
        # Skip repeated table headers / date lines
        if "CANDIDATE" in raw and "NAME" in raw.upper():
            i += 1
            continue
        if "November 7, 2023 General Election" in raw or "Nov. 7, 2023" in raw:
            i += 1
            continue
        if not raw.strip() or raw.startswith("\x0c"):
            i += 1
            continue

        rm = ROW.match(raw.rstrip())
        if rm and rm.group("text").strip():
            text = rm.group("text")
            groups = [g for g in re.split(r"\s{2,}", text.strip()) if g]
            if len(groups) >= 2:
                frag, name = groups[0], groups[-1]
            else:
                frag, name = None, groups[0]

            if name.isdigit():
                # trailing-zero filler row that carries a label fragment
                if frag is not None:
                    frag = full_label(frag)
                    if not label_parts or label_parts[-1] != frag:
                        label_parts.append(frag)
                    if not current:
                        current = []
                i += 1
                continue

            if frag is not None:
                frag = full_label(frag)
            if frag is not None and frag.upper() in PRIMARY_LABELS and current:
                flush()  # a new contest begins
            if frag is not None:
                if not label_parts or label_parts[-1] != frag:
                    label_parts.append(frag)
                if not current:
                    current = []

            party = (rm.group("party") or "").strip()
            cand = name.strip()
            upper = cand.upper()
            if upper == "SCATTERED":
                cand, party = "Write-ins", ""
            party_map = {"D": "DEM", "R": "REP", "D/R": "D/R", "L": "LIB",
                         "I": "IND"}
            party = party_map.get(party, party)
            votes = int(rm.group("tot").replace(",", ""))
            current.append((cand, party, votes))
        else:
            # non-candidate line: keep any office-label fragment in the left
            # column (candidate rows may be absent because the row only holds
            # a label and the zero filler columns)
            frag = raw[:19].strip()
            if frag and not raw.startswith("\x0c"):
                frag = full_label(frag)
                if not label_parts or label_parts[-1] != frag:
                    label_parts.append(frag)
                if not current:
                    current = []
        i += 1
    flush()
    return results


def muni_type(muni: str) -> str:
    u = muni.upper()
    if "TWP" in u:
        return "Township"
    if "BORO" in u:
        return "Borough"
    return "City"


def split_label(label: str, muni: str):
    """Map an accumulated label like 'SCHOOL DIRECTOR PENNCREST SCHOOL DISTRICT
    FOUR YEAR TERM (VOTE FOR NOT MORE THAN FIVE)' to (office, district)."""
    lab = " ".join(label.split())
    up = lab.upper()

    term = None
    for word, num in TERM_WORDS.items():
        if f"{word} YEAR TERM" in up:
            term = num
            break

    m = re.search(r"\((VOTE FOR NOT\s+.+?|VOTE FOR ONE)\)", up)
    vote_for_txt = m.group(1) if m else None

    if up.startswith("SCHOOL DIRECTOR"):
        rest = up[len("SCHOOL DIRECTOR"):].strip()
        # strip trailing term / vote-for fragments
        for word, _num in TERM_WORDS.items():
            rest = re.sub(rf"\s*{word} YEAR TERM\b", "", rest)
        rest = re.split(r"\(?VOTE FOR", rest)[0].strip()
        district = None
        if rest:
            district = re.sub(r"\s+", " ", rest.title())
            district = re.sub(r"\s*\(.*$", "", district).strip()
        return ("School Director", district)

    if up.startswith("SUPERVISOR"):
        office = "Township Supervisor"
    elif up.startswith("AUDITOR"):
        office = f"{muni_type(muni)} Auditor"
    elif up.startswith("COUNCIL"):
        office = f"{muni_type(muni)} Council"
    elif up.startswith("TREASURER"):
        office = f"{muni_type(muni)} Treasurer"
    elif up.startswith("CONTROLLER"):
        office = "City Controller"
    elif up.startswith("CONSTABLE"):
        office = "Constable"
    elif up.startswith("MAYOR"):
        office = "Mayor"
    elif up.startswith("TAX COLLECTOR"):
        office = "Tax Collector"
    elif up.startswith("JUDGE OF ELECTION"):
        office = "Judge of Elections"
    elif up.startswith("INSPECTOR"):
        office = "Inspector of Elections"
    else:
        office = lab.title()

    return (office, muni)


def normalize_muni(muni: str) -> str:
    return re.sub(r"\s+", " ", muni.strip())


def build_rows(results, county="Crawford"):
    # first pass: collect (office, district) counts to decide term suffixes
    contests = OrderedDict()
    for muni, label, rows in results:
        muni_n = normalize_muni(muni)
        office, district = split_label(label, muni_n)
        if office == "School Director":
            key = (office, district)
        else:
            key = (office, district)
        contests.setdefault(key, []).append((muni_n, label, rows))

    # detect office/district keys with several term variants
    # (each key may hold multiple term variants of the same contest)
    def label_term(label):
        up = label.upper()
        for word, num in TERM_WORDS.items():
            if f"{word} YEAR TERM" in up:
                return num
        return None

    out = []
    for (office, district), entries in contests.items():
        term_counts = {}
        for _, label, _ in entries:
            t = None
            for word, num in TERM_WORDS.items():
                if f"{word} YEAR TERM" in label.upper():
                    t = num
                    break
            term_counts[t] = term_counts.get(t, 0) + 1
        need_term = len(term_counts) > 1
        for muni_n, label, rows in entries:
            t = None
            for word, num in TERM_WORDS.items():
                if f"{word} YEAR TERM" in label.upper():
                    t = num
                    break
            off = office
            if need_term and t is not None:
                off = f"{office} ({t} Year)"
            elif t == 2 and not need_term:
                # lone two-year variant keeps its suffix for clarity only if
                # the label says TWO YEAR TERM and other years exist elsewhere
                pass
            for cand, party, votes in rows:
                out.append({
                    "county": county,
                    "precinct": muni_n,
                    "office": off,
                    "district": district or "",
                    "party": party,
                    "candidate": cand,
                    "votes": votes,
                    "election_day": "",
                    "mail": "",
                    "provisional": "",
                })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input_txt")
    ap.add_argument("output_csv")
    ap.add_argument("--county", default="Crawford")
    args = ap.parse_args(argv)

    results = parse_txt(args.input_txt)
    rows = build_rows(results, args.county)
    with open(args.output_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows "
          f"({len({(r['precinct']) for r in rows})} municipalities, "
          f"{len({(r['office'], r['district']) for r in rows})} contests) "
          f"-> {args.output_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()