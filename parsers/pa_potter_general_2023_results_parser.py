#!/usr/bin/env python3
"""Parse Potter County 2023 general election official results CSV.

The county publishes a single county-level CSV
(``Potter County Official Results 2023 General.csv``) with columns::

    County,Contest,Party,Candidate,Votes,YesVotes,NoVotes,Percentage,
    ElectionDayVotes,MailInVotes,ProvisionalVotes

There is no precinct or municipality detail in the source, so the output is a
county-level CSV (no ``precinct`` column).  Retention contests in the source
carry Yes/No counts in ``YesVotes``/``NoVotes`` with the judge's name as
``Candidate``; these are expanded to the repo convention
``<office> Retention - <name>`` with ``Yes``/``No`` rows (breakdowns left
empty because the source reports none for retention rows).

Usage:
  python pa_potter_general_2023_results_parser.py <input.csv> <output.csv>
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY_FIELDNAMES = ["county", "office", "district", "party",
                     "candidate", "votes", "election_day", "mail", "provisional"]


def clean_name(raw: str) -> str:
    """'MCCAFFERY, DANIEL  D' -> 'Daniel D McCaffery' (LAST, FIRST M)."""
    s = re.sub(r"\s+", " ", raw).strip()
    if "," in s:
        last, _, first = s.partition(",")
        parts = [p.strip().rstrip(".") for p in first.split() if p.strip()]
        parts.append(last.strip().rstrip("."))
        words = parts
    else:
        words = [w[0].upper() + w[1:].lower() if w else w for w in s.split()]
    out = []
    for w in words:
        lw = w.lower()
        if lw.startswith("mc") and len(w) > 2:
            out.append("Mc" + lw[2].upper() + lw[3:])
        else:
            out.append(w[0].upper() + lw[1:])
    s = " ".join(out)
    for suf in ("Jr", "Sr", "Ii", "Iii", "Iv"):
        s = re.sub(rf"\b{suf}\b", {"Jr": "Jr.", "Sr": "Sr.", "Ii": "II",
                                   "Iii": "III", "Iv": "IV"}[suf], s)
    return s


# candidate-name fixes matching the names used in the other 2023 county files
NAME_OVERRIDES = {
    "Daniel D McCaffery": "Daniel McCaffery",
    "Carolyn T Carluccio": "Carolyn Carluccio",
    "Jill L Beck": "Jill Beck",
    "Timika Lane": "Timika Lane",
    "Maria C Battista": "Maria Battista",
    "Harry F Jr. Smail": "Harry F. Smail Jr.",
    "Harry F Jr Smail.": "Harry F. Smail Jr.",
    "Harry F Smail Jr.": "Harry F. Smail Jr.",
    "Matthew S Wolf": "Matt Wolf",
    "Megan Martin": "Megan Martin",
}


def map_office(contest: str) -> tuple[str, bool]:
    """Return (office_base, is_retention)."""
    c = contest.strip()
    m = re.match(r"^(.*?)\s*\(Retention\)\s*$", c, re.I)
    if m:
        base = m.group(1).strip()
        base = re.sub(r"^Judge of the\s+", "", base, flags=re.I)
        return base, True
    return c, False


def convert(in_path: str, out_path: str, county: str = "Potter") -> list[dict]:
    rows = []
    with open(in_path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            contest = (r.get("Contest") or "").strip()
            if not contest:
                continue
            office, is_retention = map_office(contest)
            party = (r.get("Party") or "").strip().upper()
            party = party if party in ("DEM", "REP", "LBR", "GRN", "CON",
                                       "LIB", "IND") else ""
            raw_name = (r.get("Candidate") or "").strip()
            if is_retention:
                name = clean_name(raw_name)
                office_full = f"{office} Retention - {name}"
                for label, col in (("Yes", "YesVotes"), ("No", "NoVotes")):
                    v = (r.get(col) or "").strip()
                    if v == "" or not v.isdigit():
                        continue
                    rows.append({
                        "county": county, "office": office_full,
                        "district": "", "party": "", "candidate": label,
                        "votes": int(v), "election_day": "", "mail": "",
                        "provisional": "",
                    })
                continue
            name = NAME_OVERRIDES.get(clean_name(raw_name),
                                      clean_name(raw_name))
            v = (r.get("Votes") or "").strip()
            if not v:
                continue
            bd = {}
            for col, key in (("ElectionDayVotes", "election_day"),
                             ("MailInVotes", "mail"),
                             ("ProvisionalVotes", "provisional")):
                val = (r.get(col) or "").strip()
                if val and val.isdigit():
                    bd[key] = int(val)
                else:
                    bd[key] = None
            rows.append({
                "county": county, "office": office, "district": "",
                "party": party, "candidate": name, "votes": int(v),
                "election_day": bd["election_day"], "mail": bd["mail"],
                "provisional": bd["provisional"],
            })
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COUNTY_FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"wrote {len(rows)} county-level rows -> {out_path}")
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2])