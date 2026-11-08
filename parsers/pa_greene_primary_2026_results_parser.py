#!/usr/bin/env python3
"""Greene County PA 2026 General Primary (2026-05-19) precinct results parser.

Source: "Greene County 2026 Primary Precinct Results.csv" (Greene County's own
per-precinct CSV export), a flat 6-column file:
  county,precinct,office,party,candidate,votes
where the source `office` combines the ballot party and contest, e.g.
"Dem Governor", "Rep Senator In The General Assembly 46th Senatorial Dist"
(office names are truncated at 50 chars in the source).

Mapping:
- office / party are split; party mapped to 3-letter codes (DEM/REP).
- "Dem Governor"                        -> Governor
- "Dem/Rep Lieutenant Governor"         -> Lieutenant Governor
- "Representative In Congress 14th ..." -> U.S. House (district 14)
- "Senator In The General Assembly 46th Senatorial Dist"
                                        -> State Senate (district 46)
- "Representative In The General Assembly 50th Legislat"
                                        -> State House (district 50)
- "Member Of [Democratic|Republican] State Committee"
        -> "Member of Democratic State Committee" /
           "Member of Republican State Committee" (district blank), matching
           the other 2026 precinct files.
- County party committee races ("Dem Member Of County Democratic Committee
  <subdistrict> (Vote for 2)") are kept as printed, minus the leading "Dem"/
  "Rep" token and with "Member of" casing (same family as Bedford's "Member Of
  The Democratic County Committee ..."): district blank, party from contest.

No write-in rows, Registered Voters or Ballots Cast rows, and no vote
breakdown columns exist in the source; none invented. All five statewide DOS
contests shown for Greene in work2026p/dos/greene__dos_precinctreturns.txt
(GOV, LTG, USC 14, STS 46, STH 50) are present in the county's own source, so
the DOS fallback was NOT needed and no rows were filled from DOS. The source
also carries the two State Committee contests and county party committee
races that DOS does not include; those come from the county source alone (no
countywide anchor totals exist for them).

Column order note: the source row order is office-major; output rows keep
source order grouped per office contest, which is harmless for the tests.

Usage:
  uv run python parsers/pa_greene_primary_2026_results_parser.py \
      <input.csv> <output.csv>
"""

import csv
import re
import sys

COUNTY = "Greene"

# Contest-keyed normalization: keyed off the stripped office text after the
# leading Dem/Rep token.
def normalize_office(office):
    """Return (office, district) in standard form, or raise on unknown office."""
    o = office.strip()
    m = re.match(r"^(Dem|Rep)\b\s*(.*)$", o, re.I)
    if m:
        core = m.group(2)
    else:
        core = o

    core_l = core.lower()

    if core_l == "governor":
        return "Governor", ""
    if core_l == "lieutenant governor":
        return "Lieutenant Governor", ""

    m = re.match(r"representative in congress\s*(.*)$", core_l)
    if m:
        dm = re.search(r"(\d+)", m.group(1))
        if not dm:
            raise ValueError("U.S. House contest without district number")
        return "U.S. House", dm.group(1)

    m = re.match(r"senator in the general assembly\s*(.*)$", core_l)
    if m:
        dm = re.search(r"(\d+)", m.group(1))
        if not dm:
            raise ValueError("State Senate contest without district number")
        return "State Senate", dm.group(1)

    m = re.match(r"representative in the general assembly\s*(.*)$", core_l)
    if m:
        dm = re.search(r"(\d+)", m.group(1))
        if not dm:
            raise ValueError("State House contest without district number")
        return "State House", dm.group(1)

    m = re.match(r"member of (democratic|republican) state committee\b", core_l)
    if m:
        return "Member of %s State Committee" % m.group(1).title(), ""

    # County party committee races: keep as printed, minus the leading
    # "Dem"/"Rep" token ("Member Of County Democratic Committee Waynesburg 3
    # (Vote for 2)"), normalizing "Member Of" -> "Member of".
    # The source prints the word order differently for the two parties:
    # Dem rows as "Member Of County Democratic Committee <sub>", Rep rows as
    # "Member Of Republican County Committee <sub>".
    m = re.match(r"member of county (democratic|republican) committee\b(.*)$",
                 core, re.I)
    if m:
        return ("Member of County %s Committee%s"
                % (m.group(1).title(), m.group(2))), ""
    m = re.match(r"member of (democratic|republican) county committee\b(.*)$",
                 core, re.I)
    if m:
        return ("Member of %s County Committee%s"
                % (m.group(1).title(), m.group(2))), ""

    raise ValueError("Unrecognized office: %r" % office)


PARTY = {
    "Democrat": "DEM",
    "Republican": "REP",
}


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: %s <input.csv> <output.csv>" % sys.argv[0])
    src, out = sys.argv[1], sys.argv[2]

    with open(src, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out_rows = 0
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["county", "precinct", "office", "district", "party",
             "candidate", "votes"]
        )
        for r in rows:
            if r.get("county", "").strip() != COUNTY:
                raise ValueError("unexpected county value: %r" % r.get("county"))
            office, district = normalize_office(r["office"])
            party = PARTY[r["party"].strip()]
            w.writerow([
                COUNTY,
                r["precinct"].strip(),
                office,
                district,
                party,
                r["candidate"].strip(),
                r["votes"].strip(),
            ])
            out_rows += 1

    print("wrote %d rows to %s" % (out_rows, out))


if __name__ == "__main__":
    main()
