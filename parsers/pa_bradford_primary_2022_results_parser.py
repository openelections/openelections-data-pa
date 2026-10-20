#!/usr/bin/env python3
"""Parse Bradford County PA 2022 general primary election "Statement of Votes Cast" PDF.

Source: Bradford PA 2022 Primary SOVC_Jun_1_2022_09_41_11.pdf (272 pages).
Cross-check: Bradford PA 2022-Primary-Results-1.pdf (Election Detail Report,
June 1 8:53 AM, plus a June 2 GOP U.S. Senate recount summary page).

Layout: 272 pages = 34 sessions of 8 pages each. Every session repeats the
full 61-precinct list plus a county "Total" block (label line 'Total',
whose 5th sub-row is labeled 'Total'). A session holds one or more contest
sections side by side; each section prints a (Reg. Voters, Total Votes)
leading pair, then a (votes, percent) pair per candidate. Session 1
additionally carries a Turnout section (Reg., Ballots, %); its Ballots
value per sub-row is the precinct's ballots by type.

Some contests span two sessions (Governor Republican: 8 named candidates in
session 4, Jake Corman / Josh Shapiro (W) / Write-in in session 5; Lt
Governor Republican similarly sessions 6-7); the parser merges parts.
Contests outside a precinct's district print an all-dash placeholder
section, which is skipped (House 68 applies to 24 precincts, House 110 to
37). "No Candidate Filed" committee sections print no named column, only
the Write-in column; sections whose every value is dash are emitted for
their designated precinct only (each "Democratic Committee - <place>" race
covers exactly one precinct).

The per-session section/candidate configuration below was transcribed from
the cross-check Detail Report and is asserted against the SOVC page
geometry: every sub-row's token count must equal turnout(0|3) + sum over
sections of (2 + 2*candidates), and every contest's precinct sums and
vote-breakdown sums must match both the SOVC county Total blocks and the
cross-check printed county totals (when the cross-check file is passed as
a third argument).

Column mapping: Normal -> election_day, Absentee + Mail-In -> mail,
Provisional -> provisional, votes = their sum. Registered Voters / Ballots
Cast come from session 1's Turnout section (party "", as in the 2022
general Bradford file). Declared write-in candidates printed "Name (W)"
become candidate rows (suffix stripped) with the contest's party; the
"Write-in" column becomes candidate "Write Ins" with the contest's party.

Usage:
    uv run python parsers/pa_bradford_primary_2022_results_parser.py \
        <input_pdf> <output_csv> [cross_check_pdf]
"""
from __future__ import annotations

import csv
import re
import sys

COUNTY = "Bradford"

SUBROW_LABELS = {"Normal", "Absentee", "Mail-In", "Provisional", "Total"}
SUBROWS = ("Normal", "Absentee", "Mail-In", "Provisional")

W = "Write Ins"  # the printed "Write-in" column

# Contest meta: key -> (office, district, party)
CONTEST_META = {
    "SEN_DEM":    ("U.S. Senate", "", "DEM"),
    "SEN_REP":    ("U.S. Senate", "", "REP"),
    "GOV_DEM":    ("Governor", "", "DEM"),
    "GOV_REP":    ("Governor", "", "REP"),
    "LTG_DEM":    ("Lieutenant Governor", "", "DEM"),
    "LTG_REP":    ("Lieutenant Governor", "", "REP"),
    "H9_DEM":     ("U.S. House", "9", "DEM"),
    "H9_REP":     ("U.S. House", "9", "REP"),
    "SHA68_DEM":  ("State House", "68", "DEM"),
    "SHA68_REP":  ("State House", "68", "REP"),
    "SHA110_DEM": ("State House", "110", "DEM"),
    "SHA110_REP": ("State House", "110", "REP"),
    "DSC8":       ("Member of Democratic State Committee", "8", "DEM"),
    "RSC8":       ("Member of Republican State Committee", "8", "REP"),
    "Q1":         ("Question 1", "", ""),
    "Q2":         ("Question 2", "", ""),
}


def dc(place):
    """Contest key (and meta) for a "DEMOCRATIC COMMITTEE - <place>" race."""
    CONTEST_META.setdefault(
        "DC_" + place, ("Democratic Committee - " + place, "", "DEM"))
    return "DC_" + place


# Named candidate columns per "Democratic Committee" race (as printed on the
# cross-check Detail Report; declared write-ins carry "(W)" which the parser
# strips). The Write-in column always comes after the named columns.
DC_NAMES = {
    dc("Asylum Township"): ["Roy E. Woodruff, Jr."],
    dc("Sheshequin Township"): ["Fran Brown"],
    dc("Smithfield Township"): ["Marcia S. Kesten", "Alice B. Moyer"],
    dc("Towanda Borough 2nd Ward"): ["Lisa Kay LaBarre", "Richard B. Lewis"],
    dc("Towanda Borough 3rd Ward"): ["Fred N. Smith", "Margueriet M. Smith"],
    dc("Towanda Township"): ["Jeffrey M. Gonzalez", "Diane Gonzalez"],
    dc("Troy Borough"): ["Zachary R. Gates", "Sarah Gates"],
    dc("Wells Township"): ["Jeff Warner", "Lynda Warner"],
    dc("Windham Township"): ["George Vivino-Hintze", "Anne Vivino-Hintze"],
    dc("Wyalusing Borough"): ["Christine Heyman"],
    dc("Alba Borough"): [],
    dc("Albany Township"): ["Karl D Heath (W)"],
    dc("Armenia Township"): [],
    dc("Athens Borough 1st Ward"): [],
    dc("Athens Borough 2nd Ward"): ["Elizabeth Owen (W)", "Gabe Felton (W)"],
    dc("Athens Borough 3rd Ward"): [],
    dc("Athens Borough 4th Ward"): ["David Fortune (W)"],
    dc("Athens Township 1st District"): [],
    dc("Athens Township 2nd District"): ["Cynthia Baer (W)"],
    dc("Burlington Borough"): [],
    dc("Burlington Township"): [],
    dc("West Burlington Township"): [],
    dc("Canton Borough"): ["Non Compliant (W)"],
    dc("Canton Township"): [],
    dc("Columbia Township"): [],
    dc("Franklin Township"): [],
    dc("Granville Township"): [],
    dc("Herrick Township"): [],
    dc("Leraysville Borough"): [],
    dc("Leroy Township"): [],
    dc("Litchfield Township"): [],
    dc("Monroe Borough"): ["Dawn Innes (W)"],
    dc("Monroe Township"): [],
    dc("New Albany Borough"): [],
    dc("Orwell Township"): [],
    dc("Overton Township"): [],
    dc("Pike Township"): [],
    dc("Ridgebury Township"): [],
    dc("Rome Borough"): [],
    dc("Rome Township"): [],
    dc("Sayre Borough 1st Ward"): [],
    dc("Sayre Borough 2nd Ward"): ["Joann Sabatura (W)"],
    dc("Sayre Borough 3rd Ward"): [],
    dc("Sayre Borough 4th Ward"): ["Melissa Post (W)"],
    dc("Sayre Borough 5th Ward"): [],
    dc("South Creek Township"): [],
    dc("South Waverly Borough"): ["Stephen Tubbs (W)"],
    dc("Springfield Township"): ["Robert Hugg (W)", "Valerie Hugg (W)"],
    dc("Standing Stone Township"): [],
    dc("Stevens Township"): [],
    dc("Sylvania Borough"): [],
    dc("Terry Township"): [],
    dc("Towanda Borough 1st Ward"): [],
    dc("North Towanda Township"): ["Janet Lewis (W)"],
    dc("Troy Township"): [],
    dc("Tuscarora Township"): [],
    dc("Ulster Township"): [],
    dc("Warren Township"): [],
    dc("Wilmot Township"): ["Kelli Benjamin (W)", "Mark Potter (W)"],
    dc("Wyalusing Township"): [],
    dc("Wysox Township"): ["Martha Eldredge Martin (W)"],
}

# Sessions: 0-indexed; PDF pages 8*i .. 8*i+7. (turnout flag,
# [(contest key, [candidate columns left to right])]). Split contest parts
# appear in consecutive sessions under the same key and are merged in
# output.
SESSIONS = [
    (True,  [("SEN_DEM", ["Alex Khalil", "Conor Lamb", "Malcolm Kenyatta",
                          "John Fetterman", W])]),                       # 0
    (False, [("SEN_REP", ["Kathy Barnette", "Mehmet Oz", "George Bochetto",
                          "Jeff Bartos", "Dave McCormick", "Sean Gale",
                          "Carla Sands", W])]),                          # 1
    (False, [("GOV_DEM", ["Josh Shapiro", W])]),                          # 2
    (False, [("GOV_REP", ["Lou Barletta", "Douglas V. Mastriano",
                          "Nche Zama", "Dave White", "Melissa Hart",
                          "Bill McSwain", "Charlie Gerow", "Joe Gale"])]),  # 3
    (False, [("GOV_REP", ["Jake Corman", "Josh Shapiro (W)", W])]),       # 4
    (False, [("LTG_DEM", ["Austin Davis", "Ray Sosa", "Brian Sims",
                          "Teddy Daniels (W)", W])]),                     # 5
    (False, [("LTG_REP", ["Clarice Schillinger", "James Earl Jones",
                          "Rick Saccone", "John Brown", "Chris Frye",
                          "Jeff Coleman", "Russ Diamond",
                          "Carrie Lewis DelRosso"])]),                    # 6
    (False, [("LTG_REP", ["Teddy Daniels", "Brian Sims (W)", W]),
             ("H9_DEM", ["Amanda R. Waldman", "Dan Meuser (W)", W])]),    # 7
    (False, [("H9_REP", ["Dan Meuser", "Jim Bognet (W)", W]),
             ("SHA68_DEM", ["Clint Owlett (W)", W])]),                    # 8
    (False, [("SHA68_REP", ["Clint Owlett", W]),
             ("SHA110_DEM", ["Tina Pickett (W)", W])]),                   # 9
    (False, [("SHA110_REP", ["Tina Pickett", W]),
             ("DSC8", ["Jeffrey Gonzalez (W)", W])]),                     # 10
    (False, [("RSC8", ["Eric Matthews", "Krystle Bristol",
                       "Jason Krise (W)", W]),
             (dc("Asylum Township"), DC_NAMES[dc("Asylum Township")])]),  # 11
    (False, [(dc("Sheshequin Township"), DC_NAMES[dc("Sheshequin Township")]),
             (dc("Smithfield Township"),
              DC_NAMES[dc("Smithfield Township")])]),                     # 12
    (False, [(dc("Towanda Borough 2nd Ward"),
              DC_NAMES[dc("Towanda Borough 2nd Ward")]),
             (dc("Towanda Borough 3rd Ward"),
              DC_NAMES[dc("Towanda Borough 3rd Ward")])]),                # 13
    (False, [(dc("Towanda Township"), DC_NAMES[dc("Towanda Township")]),
             (dc("Troy Borough"), DC_NAMES[dc("Troy Borough")])]),        # 14
    (False, [(dc("Wells Township"), DC_NAMES[dc("Wells Township")]),
             (dc("Windham Township"), DC_NAMES[dc("Windham Township")])]),  # 15
    (False, [(dc("Wyalusing Borough"), DC_NAMES[dc("Wyalusing Borough")]),
             (dc("Alba Borough"), DC_NAMES[dc("Alba Borough")]),
             (dc("Albany Township"), DC_NAMES[dc("Albany Township")])]),  # 16
    (False, [(dc("Armenia Township"), DC_NAMES[dc("Armenia Township")]),
             (dc("Athens Borough 1st Ward"),
              DC_NAMES[dc("Athens Borough 1st Ward")]),
             (dc("Athens Borough 2nd Ward"),
              DC_NAMES[dc("Athens Borough 2nd Ward")])]),                 # 17
    (False, [(dc("Athens Borough 3rd Ward"),
              DC_NAMES[dc("Athens Borough 3rd Ward")]),
             (dc("Athens Borough 4th Ward"),
              DC_NAMES[dc("Athens Borough 4th Ward")]),
             (dc("Athens Township 1st District"),
              DC_NAMES[dc("Athens Township 1st District")])]),            # 18
    (False, [(dc("Athens Township 2nd District"),
              DC_NAMES[dc("Athens Township 2nd District")]),
             (dc("Burlington Borough"), DC_NAMES[dc("Burlington Borough")]),
             (dc("Burlington Township"),
              DC_NAMES[dc("Burlington Township")])]),                     # 19
    (False, [(dc("West Burlington Township"),
              DC_NAMES[dc("West Burlington Township")]),
             (dc("Canton Borough"), DC_NAMES[dc("Canton Borough")]),
             (dc("Canton Township"), DC_NAMES[dc("Canton Township")])]),  # 20
    (False, [(dc("Columbia Township"), DC_NAMES[dc("Columbia Township")]),
             (dc("Franklin Township"), DC_NAMES[dc("Franklin Township")]),
             (dc("Granville Township"), DC_NAMES[dc("Granville Township")]),
             (dc("Herrick Township"), DC_NAMES[dc("Herrick Township")])]),  # 21
    (False, [(dc("Leraysville Borough"),
              DC_NAMES[dc("Leraysville Borough")]),
             (dc("Leroy Township"), DC_NAMES[dc("Leroy Township")]),
             (dc("Litchfield Township"),
              DC_NAMES[dc("Litchfield Township")])]),                     # 22
    (False, [(dc("Monroe Borough"), DC_NAMES[dc("Monroe Borough")]),
             (dc("Monroe Township"), DC_NAMES[dc("Monroe Township")]),
             (dc("New Albany Borough"),
              DC_NAMES[dc("New Albany Borough")])]),                      # 23
    (False, [(dc("Orwell Township"), DC_NAMES[dc("Orwell Township")]),
             (dc("Overton Township"), DC_NAMES[dc("Overton Township")]),
             (dc("Pike Township"), DC_NAMES[dc("Pike Township")]),
             (dc("Ridgebury Township"),
              DC_NAMES[dc("Ridgebury Township")])]),                      # 24
    (False, [(dc("Rome Borough"), DC_NAMES[dc("Rome Borough")]),
             (dc("Rome Township"), DC_NAMES[dc("Rome Township")]),
             (dc("Sayre Borough 1st Ward"),
              DC_NAMES[dc("Sayre Borough 1st Ward")])]),                  # 25
    (False, [(dc("Sayre Borough 2nd Ward"),
              DC_NAMES[dc("Sayre Borough 2nd Ward")]),
             (dc("Sayre Borough 3rd Ward"),
              DC_NAMES[dc("Sayre Borough 3rd Ward")]),
             (dc("Sayre Borough 4th Ward"),
              DC_NAMES[dc("Sayre Borough 4th Ward")])]),                  # 26
    (False, [(dc("Sayre Borough 5th Ward"),
              DC_NAMES[dc("Sayre Borough 5th Ward")]),
             (dc("South Creek Township"),
              DC_NAMES[dc("South Creek Township")]),
             (dc("South Waverly Borough"),
              DC_NAMES[dc("South Waverly Borough")])]),                   # 27
    (False, [(dc("Springfield Township"),
              DC_NAMES[dc("Springfield Township")]),
             (dc("Standing Stone Township"),
              DC_NAMES[dc("Standing Stone Township")]),
             (dc("Stevens Township"), DC_NAMES[dc("Stevens Township")])]),  # 28
    (False, [(dc("Sylvania Borough"), DC_NAMES[dc("Sylvania Borough")]),
             (dc("Terry Township"), DC_NAMES[dc("Terry Township")]),
             (dc("Towanda Borough 1st Ward"),
              DC_NAMES[dc("Towanda Borough 1st Ward")])]),                # 29
    (False, [(dc("North Towanda Township"),
              DC_NAMES[dc("North Towanda Township")]),
             (dc("Troy Township"), DC_NAMES[dc("Troy Township")]),
             (dc("Tuscarora Township"),
              DC_NAMES[dc("Tuscarora Township")])]),                      # 30
    (False, [(dc("Ulster Township"), DC_NAMES[dc("Ulster Township")]),
             (dc("Warren Township"), DC_NAMES[dc("Warren Township")]),
             (dc("Wilmot Township"), DC_NAMES[dc("Wilmot Township")])]),  # 31
    (False, [(dc("Wyalusing Township"),
              DC_NAMES[dc("Wyalusing Township")]),
             (dc("Wysox Township"), DC_NAMES[dc("Wysox Township")]),
             ("Q1", ["YES", "NO"])]),                                     # 32
    (False, [("Q2", ["YES", "NO"])]),                                     # 33
]

# Normalize: every "Democratic Committee" race's column list ends with the
# Write-in column (races with no filed candidates have ONLY that column).
SESSIONS = [
    (has_turnout, [(key, list(cands) + [W] if key in DC_NAMES else cands)
                   for key, cands in sections])
    for has_turnout, sections in SESSIONS
]

ORDINAL_RE = re.compile(r"^(\d+)(ST|ND|RD|TH)$")


def titlecase_precinct(label: str) -> str:
    out = []
    for word in label.split():
        m = ORDINAL_RE.match(word)
        if m:
            out.append(f"{m.group(1)}{m.group(2).lower()}")
        else:
            out.append(word.capitalize())
    return " ".join(out)


def to_num(tok: str) -> int:
    if tok in ("-", ""):
        return 0
    return int(tok.replace(",", ""))


def clean_name(cand: str) -> str:
    """Drop the declared-write-in "(W)" marker from a candidate name."""
    return re.sub(r"\s*\(W\)\s*$", "", cand).strip()


def read_pages(pdf_path):
    """Parse all pages into [(blocks, county_total)]; length 272.

    blocks: {precinct: {subrow: [raw tokens]}} for precinct label lines
    (x0 < 49, top > 158); county_total: {subrow: [raw tokens]} for the
    county Total block (label line 'Total'); 'Jurisdiction Wide' label
    lines are anchors only in this file (sub-rows after them, if any, are
    skipped, as in the 2022 general parser).
    """
    import pdfplumber

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for pi, page in enumerate(pdf.pages):
            lines = {}
            for w in page.extract_words():
                lines.setdefault(round(w["top"]), []).append(w)
            blocks = {}
            county_total = {}
            current = None
            for top in sorted(lines):
                ws = sorted(lines[top], key=lambda w: w["x0"])
                x0 = ws[0]["x0"]
                top = ws[0]["top"]
                if x0 < 49 and top > 158:
                    label = " ".join(w["text"] for w in ws)
                    if label == "Jurisdiction Wide":
                        current = None
                        continue
                    if label == "Total":
                        current = "__COUNTY__"
                        continue
                    current = titlecase_precinct(label)
                    blocks.setdefault(current, {})
                    continue
                if 49 <= x0 <= 58 and ws[0]["text"] in SUBROW_LABELS \
                        and len(ws) > 1:
                    subrow = ws[0]["text"]
                    vals = [w["text"] for w in ws[1:]]
                    target = county_total if current == "__COUNTY__" \
                        else blocks.get(current)
                    if target is None:
                        continue  # Jurisdiction Wide block (no data rows)
                    if subrow in target:
                        raise ValueError(
                            f"page {pi + 1}: duplicate sub-row {subrow!r} "
                            f"for {current!r}")
                    target[subrow] = vals
            pages.append((blocks, county_total))
    return pages


def parse_subrow(tokens, has_turnout, sections, where):
    """Split a raw sub-row token list.

    Each section prints Reg., Total, then one (votes, percent) pair per
    candidate; a zero percent prints '-' and a zero value prints '0' (an
    inapplicable section prints all dashes). So reg/total are two
    consecutive tokens and each candidate's votes is the first member of
    its 2-token pair.

    Returns ((reg, ballots) or None,
             [(key, carries_data, total, [candidate votes])]).
    carries_data: False when the section's every value (leading + pairs)
    is the all-dash placeholder.
    """
    expected = (3 if has_turnout else 0) + sum(
        2 + 2 * len(c) for _, c in sections)
    if len(tokens) != expected:
        raise ValueError(
            f"{where}: expected {expected} tokens, got {len(tokens)}: "
            f"{tokens}")
    turnout_vals = None
    lead = 0
    if has_turnout:
        lead = 3
        turnout_vals = (to_num(tokens[0]), to_num(tokens[1]))
    out = []
    pos = lead
    for key, cands in sections:
        end = pos + 2 + 2 * len(cands)
        sl = tokens[pos:end]
        carries = any(t != "-" for t in sl)
        total = to_num(sl[1])
        vals = [to_num(sl[2 + 2 * ci]) for ci in range(len(cands))]
        out.append((key, carries, total, vals))
        pos = end
    return turnout_vals, out


def parse(pdf_path, out_csv):
    pages = read_pages(pdf_path)
    if len(pages) != 272:
        raise ValueError(f"expected 272 pages, got {len(pages)}")

    # candidate columns per contest (merged over split parts); contest keys
    # once each, in first-scene session order
    contest_cands = {}
    ordered = []
    for has_turnout, sections in SESSIONS:
        for key, cands in sections:
            if key not in ordered:
                ordered.append(key)
            cur = contest_cands.setdefault(key, [])
            for c in cands:
                if c not in cur:
                    cur.append(c)

    turnout = {}        # {precinct: {subrow: (reg, ballots)}}
    contest_data = {}   # {precinct: {key: {subrow: [votes]}}}
    contest_carries = {}  # {precinct: {key: bool}}
    county_totals = {}  # {session: {subrow: [(key, total, [votes])]}}
    precincts_all = None

    for si, (has_turnout, sections) in enumerate(SESSIONS):
        session_precincts = set()
        for offset in range(8):
            pno = si * 8 + offset
            blocks, ct = pages[pno]
            for precinct, vals in blocks.items():
                if not vals:
                    continue
                session_precincts.add(precinct)
                for subrow in SUBROWS:
                    if subrow not in vals:
                        raise ValueError(
                            f"page {pno + 1}: precinct {precinct!r} missing "
                            f"sub-row {subrow!r}")
                    tv, secs = parse_subrow(
                        vals[subrow], has_turnout, sections,
                        f"page {pno + 1} {precinct} {subrow}")
                    if has_turnout:
                        old = turnout.get(precinct, {}).get(subrow)
                        if old is not None:
                            raise ValueError(
                                f"page {pno + 1}: duplicate turnout for "
                                f"{precinct!r} {subrow}")
                        turnout.setdefault(precinct, {})[subrow] = tv
                    votes_by_name = {}
                    for (key, carries, total, cvals), cands in zip(
                            [s for s in secs], [c for _, c in sections]):
                        merged = CONTEST_CANDS[key]
                        start = merged.index(cands[0])
                        for j, cv in enumerate(cvals):
                            cand = merged[start + j]
                            votes_by_name[cand if cand == W
                                          else clean_name(cand)] = cv
                        contest_data.setdefault(precinct, {}).setdefault(
                            key, {}).setdefault(
                            subrow, {}).update(votes_by_name)
                        contest_carries.setdefault(precinct, {})[key] = \
                            contest_carries.get(precinct, {}).get(
                                key, False) or carries
            if ct:
                got = {}
                for subrow in SUBROWS + ("Total",):
                    if subrow not in ct:
                        raise ValueError(
                            f"page {pno + 1}: county Total block missing "
                            f"sub-row {subrow!r}")
                    _, secs = parse_subrow(
                        ct[subrow], has_turnout, sections,
                        f"page {pno + 1} county Total {subrow}")
                    got[subrow] = secs
                if si in county_totals:
                    if county_totals[si] != got:
                        raise ValueError(
                            f"session {si + 1}: conflicting county Total "
                            f"blocks")
                else:
                    county_totals[si] = got
        if len(session_precincts) != 61:
            raise ValueError(
                f"session {si + 1}: expected 61 precincts, got "
                f"{len(session_precincts)}: {sorted(session_precincts)}")
        if precincts_all is None:
            precincts_all = set(session_precincts)
        elif session_precincts != precincts_all:
            raise ValueError(
                f"session {si + 1}: precincts differ from session 1: "
                f"{sorted(session_precincts ^ precincts_all)}")

    precincts = sorted(turnout)
    print(f"Precincts found: {len(precincts)}")
    if len(precincts) != 61:
        raise ValueError(f"expected 61 precincts, got {len(precincts)}")

    warnings = []
    # per contest: the precincts that carry data (for layout checks and
    # single-precinct race handling)
    actives = {}
    for key in ordered:
        actives[key] = [p for p in precincts
                        if contest_carries.get(p, {}).get(key)]
    for key in ordered:
        if key in DC_NAMES:
            if len(actives[key]) > 1:
                raise ValueError(
                    f"contest {key}: the source must show exactly one "
                    f"active precinct, got {len(actives[key])}: "
                    f"{actives[key]}")
            if actives[key] and actives[key][0] != key[len("DC_"):]:
                warnings.append(
                    f"'{CONTEST_META[key][0]}' active precinct is "
                    f"{actives[key][0]!r}, name-derived is "
                    f"{key[len('DC_'):]!r}")
        elif key in ("Q1", "Q2") and len(actives[key]) > 1:
            raise ValueError(
                f"contest {key}: expected one active precinct, got "
                f"{actives[key]}")

    rows = []
    for precinct in precincts:
        t = turnout[precinct]
        reg = t["Normal"][0]
        for sub, (r, _) in t.items():
            if r != reg:
                raise ValueError(
                    f"{precinct}: registered-voters mismatch ({sub}: {r} "
                    f"vs Normal {reg})")
        rows.append([COUNTY, precinct, "Registered Voters", "", "", "",
                     reg, "", "", ""])
        rows.append([COUNTY, precinct, "Ballots Cast", "", "", "",
                     sum(b for _, b in t.values()),
                     t["Normal"][1],
                     t["Absentee"][1] + t["Mail-In"][1],
                     t["Provisional"][1]])

        for key in ordered:
            office, district, party = CONTEST_META[key]
            cands = contest_cands[key]
            if contest_carries.get(precinct, {}).get(key):
                active = True
            elif key in DC_NAMES or key in ("Q1", "Q2"):
                # all-zero single-precinct race: emit at its designated
                # precinct so the (zero) row is still reported
                one = actives[key][0] if actives[key] \
                    else (key[len("DC_"):] if key in DC_NAMES else None)
                if one is None:
                    raise ValueError(f"contest {key}: no data anywhere")
                active = (precinct == one)
            else:
                active = False  # all-dash placeholder section for precinct
            if not active:
                continue
            per = contest_data.get(precinct, {}).get(key, {})
            for cand in cands:
                name = cand if cand == W else clean_name(cand)
                v = [per.get(sub, {}).get(name, 0) for sub in SUBROWS]
                rows.append([
                    COUNTY, precinct, office, district, party, name,
                    sum(v), v[0], v[1] + v[2], v[3],
                ])

    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "mail",
                    "provisional"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {out_csv}")
    for msg in warnings:
        print("WARNING: " + msg)
    return rows, county_totals


# ------------------------------------------------------------------ checks

def build_cand_index():
    """contest key -> merged candidate column list (same order as rows)."""
    contest_cands = {}
    for has_turnout, sections in SESSIONS:
        for key, cands in sections:
            cur = contest_cands.setdefault(key, [])
            for c in cands:
                if c not in cur:
                    cur.append(c)
    return contest_cands


CONTEST_CANDS = build_cand_index()


def contest_order():
    seen = []
    for has_turnout, sections in SESSIONS:
        for key, _ in sections:
            if key not in seen:
                seen.append(key)
    return seen


def cand_rows(rows, key, name):
    office, district, party = CONTEST_META[key]
    return [r for r in rows
            if r[2] == office and r[3] == district and r[4] == party
            and r[5] == name]


def cand_sums(rows, key, name):
    """(votes, ED, mail, provisional) summed over a candidate's rows."""
    rws = cand_rows(rows, key, name)
    out = []
    for col in (6, 7, 8, 9):
        vals = [int(r[col]) for r in rws if r[col] != ""]
        out.append(sum(vals))
    return tuple(out)


def part_offset(key, cands):
    """First column index of a session-part's candidates in the merged list."""
    return CONTEST_CANDS[key].index(cands[0])


def verify_sovc(rows, county_totals):
    """Per contest candidate: compare precinct sums with the SOVC county
    Total blocks. Source sub-rows: Normal (ED votes), Absentee, Mail-In
    (both folded into 'mail'), Provisional; the 'Total' sub-row is the
    countywide per-candidate total.
    """
    problems = []
    # expected per candidate per component, accumulated over session parts
    expect = {}  # key -> name -> {'total','ed','abs','mi','prov'}
    for si, per in county_totals.items():
        sections = SESSIONS[si][1]
        for idx, (key, cands) in enumerate(sections):
            merged = CONTEST_CANDS[key]
            start = part_offset(key, cands)
            for subrow in tuple(SUBROWS) + ("Total",):
                if subrow not in per:
                    continue
                secs = per[subrow]
                skey, _carries, total, cvals = secs[idx]
                if skey != key:
                    problems.append(
                        f"session {si + 1}: county Total block columns out "
                        f"of order ({skey} where {key} expected)")
                    continue
                for j, cv in enumerate(cvals):
                    name = merged[start + j]
                    name = name if name == W else clean_name(name)
                    e = expect.setdefault(key, {}).setdefault(
                        name, {"total": 0, "ed": 0, "abs": 0, "mi": 0,
                               "prov": 0})
                    which = {"Total": "total", "Normal": "ed",
                             "Absentee": "abs", "Mail-In": "mi",
                             "Provisional": "prov"}[subrow]
                    e[which] += cv
    for key, names in expect.items():
        for name, e in names.items():
            got = cand_sums(rows, key, name)
            if got[0] != e["total"]:
                problems.append(
                    f"{CONTEST_META[key]} {name}: precinct sum {got[0]} != "
                    f"SOVC county Total {e['total']}")
            if got[1] != e["ed"]:
                problems.append(
                    f"{CONTEST_META[key]} {name}: ED sum {got[1]} != SOVC "
                    f"county Normal {e['ed']}")
            if got[2] != e["abs"] + e["mi"]:
                problems.append(
                    f"{CONTEST_META[key]} {name}: ABS+M-I sum {got[2]} != "
                    f"SOVC county {e['abs']} + {e['mi']}")
            if got[3] != e["prov"]:
                problems.append(
                    f"{CONTEST_META[key]} {name}: provisional sum "
                    f"{got[3]} != SOVC county Provisional {e['prov']}")
    return problems


def parse_cross_check(path):
    """Parse the Election Detail Report into ordered contest blocks.

    Returns a list of dicts {title, party, rows: {name: (votes, ED, ABS, P,
    M-I)}, total_votes}. The final GOP U.S. Senate block (June 2 recount
    totals) appears last.
    """
    import pdfplumber

    lines_all = []
    with pdfplumber.open(path) as pdf:
        lines_all = [x.strip() for x in
                     "\n".join(p.extract_text() or "" for p in pdf.pages)
                     .split("\n")]
    row_re = re.compile(
        r"^(.+?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$")
    blocks = []
    cur = None
    want_rows = False
    title_re = re.compile(r"^[A-Z][A-Z0-9 (),\-\.]+$")

    def is_title(i, s):
        if not title_re.match(s) or s.startswith("Total ED"):
            return False
        nxt = lines_all[i + 1] if i + 1 < len(lines_all) else ""
        if nxt.startswith("(") or nxt.startswith("Number of Precincts"):
            return True
        # party/hype lines can wrap before the "(PARTY)" line
        return title_re.match(nxt) and i + 2 < len(lines_all) and (
            lines_all[i + 2].startswith("(")
            or lines_all[i + 2].startswith("Number of Precincts"))

    for li, s in enumerate(lines_all):
        if not s or s.startswith(("Page ", "Election Detail Report",
                                  "Date:", "MAY 17, 2022",
                                  "GENERAL PRIMARY ELECTION",
                                  "BRADFORD COUNTY", "Registered Voters",
                                  "Election Day", "Final Report", "Time:",
                                  "RECOUNT PREFORMED")):
            continue
        if is_title(li, s):
            cur = {"title": s, "party": "", "rows": {}, "total_votes": None}
            blocks.append(cur)
            want_rows = False
            continue
        if cur is None:
            continue
        if s.startswith("(") and s.endswith(")"):
            cur["party"] = s.strip("()")
            continue
        m = re.match(r"^Total Votes ([\d,]+)$", s)
        if m:
            cur["total_votes"] = int(m.group(1).replace(",", ""))
            want_rows = True
            continue
        if want_rows:
            m = row_re.match(s)
            if m:
                name = m.group(1).strip()
                if name in ("Undervote", "Overvote", "No Candidate Filed"):
                    continue
                cur["rows"][name] = tuple(
                    int(x.replace(",", "")) for x in m.groups()[1:])
            elif s == "No Candidate Filed":
                pass  # placeholder row; the Write-in row still follows
            elif s.startswith(("Undervote", "Overvote")):
                want_rows = False
            elif s.startswith(("Number of Precincts", "Precincts Reporting",
                               "Vote For", "Total ED")):
                pass
            else:
                want_rows = False
    # the wrapped line 'RECOUNT PREFORMED JUNE 1-2, 2022' is not a contest:
    # fold its "(PARTY)" tag into the preceding block and drop it
    out = []
    for b in blocks:
        if b["title"].startswith("RECOUNT PREFORMED"):
            if b["party"] and out and not out[-1]["party"]:
                out[-1]["party"] = b["party"]
            continue
        out.append(b)
    return out


def title_from_block(title, party):
    """Map a cross-check block title to our contest key, or None."""
    t = title.upper()
    pt = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}.get(party.upper(), "")
    if t.endswith("RECOUNT PREFORMED JUNE 1-2, 2022"):
        t = t.replace("RECOUNT PREFORMED JUNE 1-2, 2022", "").strip()
    if t.startswith("US SENATOR"):
        key = "SEN_" + pt
    elif t.startswith("GOVERNOR"):
        key = "GOV_" + pt
    elif t.startswith("LT GOVERNOR"):
        key = "LTG_" + pt
    elif t.startswith("REPRESENTATIVE IN CONGRESS"):
        key = "H9_" + pt
    elif "68TH" in t:
        key = "SHA68_" + pt
    elif "110TH" in t:
        key = "SHA110_" + pt
    elif t.startswith("DEMOCRATIC STATE COMMITTEE"):
        key = "DSC8"
    elif t.startswith("REPUBLICAN STATE COMMITTEE"):
        key = "RSC8"
    elif t.startswith("DEMOCRATIC COMMITTEE - "):
        key = dc(t[len("DEMOCRATIC COMMITTEE - "):].strip().title())
    elif t == "QUESTION 1":
        key = "Q1"
    elif t == "QUESTION 2":
        key = "Q2"
    else:
        return None
    return key


def verify_cross_check(rows, cross_path):
    """Strictly consume the cross-check Detail Report blocks in order and
    compare every contest's per-candidate county totals (votes, ED,
    ABS+M-I, P) with our sums. The trailing GOP U.S. Senate recount block
    is reported informationally."""
    contest_cands = build_cand_index()
    ordered = contest_order()
    blocks = parse_cross_check(cross_path)
    problems, info = [], []

    # the recount block: a SECOND 'US SENATOR REPUBLICAN' block at the end
    main_blocks = []
    recount = None
    seen_titles = set()
    for b in blocks:
        if b["title"].upper().startswith("US SENATOR REPUBLICAN") and \
                "US SENATOR REPUBLICAN" in seen_titles:
            recount = b
            continue
        seen_titles.add(b["title"].upper())
        main_blocks.append(b)

    if len(main_blocks) != len(ordered):
        problems.append(
            f"cross-check has {len(main_blocks)} contest blocks, expected "
            f"{len(ordered)}: {[t for t in (b['title'] for b in main_blocks)]}")
        return problems, info
    for b, key in zip(main_blocks, ordered):
        office, district, party = CONTEST_META[key]
        expected = contest_cands[key]
        xnames = list(b["rows"].keys())
        if len(xnames) != len(expected):
            problems.append(
                f"{b['title']}: {len(xnames)} candidate rows, expected "
                f"{len(expected)} ({xnames} vs {expected})")
            continue
        for i, xname in enumerate(xnames):
            our = expected[i]
            our_name = our if our == W else clean_name(our)
            x_name = W if xname.lower() == "write-in" \
                else clean_name(xname)
            if our_name != x_name:
                problems.append(
                    f"{b['title']} col {i + 1}: candidate {our_name!r} != "
                    f"printed {x_name!r}")
                continue
            xv, xed, xabs, xp, xmi = b["rows"][xname]
            got = cand_sums(rows, key, our_name)
            if got[0] != xv:
                problems.append(
                    f"{b['title']}/{party} {x_name}: votes {got[0]} != "
                    f"printed {xv}")
            if got[1] != xed:
                problems.append(
                    f"{b['title']}/{party} {x_name}: ED {got[1]} != "
                    f"printed {xed}")
            if got[2] != xabs + xmi:
                problems.append(
                    f"{b['title']}/{party} {x_name}: ABS+M-I {got[2]} != "
                    f"printed {xabs + xmi}")
            if got[3] != xp:
                problems.append(
                    f"{b['title']}/{party} {x_name}: Provisional "
                    f"{got[3]} != printed {xp}")
    if recount:
        key = title_from_block(recount["title"], recount["party"])
        office, district, party = CONTEST_META[key]
        info.append("GOP U.S. Senate recount block (June 2, printed) vs ours:")
        for xname, vals in recount["rows"].items():
            our_name = W if xname.lower() == "write-in" \
                else clean_name(xname)
            got = cand_sums(rows, key, our_name)
            info.append(f"  {xname}: printed {vals[0]}, ours {got[0]} "
                        f"(delta {got[0] - vals[0]:+d})")
    return problems, info


def contest_order():
    seen = []
    for has_turnout, sections in SESSIONS:
        for key, _ in sections:
            if key not in seen:
                seen.append(key)
    return seen


def main():
    if len(sys.argv) not in (3, 4):
        print(__doc__)
        sys.exit(1)
    rows, county_totals = parse(sys.argv[1], sys.argv[2])
    problems = verify_sovc(rows, county_totals)
    if problems:
        print("SOVC COUNTY TOTAL BLOCK PROBLEMS:")
        for p in problems:
            print("  " + p)
    else:
        print("SOVC CHECK: all contest precinct sums match county Total "
              "blocks (totals, ED, ABS+M-I, provisional)")
    if len(sys.argv) == 4:
        problems, info = verify_cross_check(rows, sys.argv[3])
        for line in info:
            print(line)
        if problems:
            print("CROSS-CHECK PROBLEMS:")
            for p in problems:
                print("  " + p)
        else:
            print("CROSS-CHECK: every candidate's county sums (votes, ED, "
                  "ABS+M-I, P) match the printed Detail Report totals")


if __name__ == "__main__":
    main()