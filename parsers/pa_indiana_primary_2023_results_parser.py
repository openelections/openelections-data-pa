#!/usr/bin/env python3
"""Indiana County PA 2023 Municipal Primary — county-level results.

Source: "Indiana County Summary Report 2023 Primary.pdf" (122 pages,
Electionware "Summary Results Report", county summary only).  No
precinct-level source exists for the 2023 primary, so this produces ONLY the
county-level file; every section is a whole contest, not a precinct.

Structure handled (beyond the shared parse_esr engine):
  * Contest headers carry the ballot party as a prefix: "DEM JUSTICE OF THE
    SUPREME COURT" / "REP SHERIFF" ("SMALL GAMES OF CHANCE BUFFINGTON TWP"
    has none — a primary-only Yes/No question).
  * Write-ins / Overvotes / Undervotes rows carry the contest's party
    (DEM/REP): in a primary the same office+district has separate DEM and
    REP contests, and leaving these rows party-empty collides the two
    parties' rows into exact duplicates (the repo's duplicate_entries data
    test hashes all non-vote columns and would flag them).
  * Pages continue a contest: write-in detail lists and footers split across
    page breaks repeat the same header WITHOUT a fresh "Total Votes Cast".
  * Some townships/boroughs had TWO seats of the same office up in 2023 and
    Electionware prints both sections under an identical (or near-identical)
    header with no term suffix.  Each such section has its own "Total Votes
    Cast".  The two instances are disambiguated by ballot position, with the
    term labels taken from the county's own 2023 general file
    (2023/counties/20231107__pa__general__indiana__precinct.csv), verified by
    candidate continuity between primary winners and general seat labels:
      - Supervisor second seats: Burrell/Cherryhill/West Mahoning/Young
        "(4 Year)" (general winners Hilty/Howells/Lightner/McClure-Missy
        Johnson), East Wheatfield "(2 Year)" (first seat's candidate Jerry
        Lych == general (6 Year) D/R winner).
      - Auditor second seats: plain "Township Auditor" for Armstrong /
        Buffington / Rayne (general-file naming), "(4 Year)" for Armagh
        Borough, Blacklick, Burrell, North Mahoning, Pine, Smicksburg, White.
      - Council second seats "(2 Year)" (general: Cherry Tree, Clymer,
        Ernest, Glen Campbell, Marion Center, Plumville).
    Candidate continuity verified: e.g. REP Auditor Blacklick instance 2 =
    Rebecca A. Meyer == general "Township Auditor (4 Year)" REP; REP
    Supervisor Young instance 1 = Bob Sosnick == general "(6 Year)"; instance
    2 = Missy Johnson == general "(4 Year)".

Usage:
    python parsers/pa_indiana_primary_2023_results_parser.py \\
        <input.txt-or-.pdf> <output.csv>
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pa_2023_summary_common import (Rows, clean_name, is_junk,
                                    nums_from, split_candidate,
                                    text_from_pdf, vals_from,
                                    write_csv)

COUNTY = "Indiana"

EXACT_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "PROTHONOTARY & CLERK OF COURTS": ("Prothonotary and Clerk of Courts", ""),
    "SHERIFF": ("Sheriff", ""),
}

MDJ_RE = re.compile(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})$")
SGC_RE = re.compile(r"^SMALL GAMES OF CHANCE (.+)$")

# header tail -> canonical district name (matches the county's 2023 general
# file, fixing the summary report's truncated/typo spellings)
MUNI = {
    "ARMSTRONG TOWNSHIP": "Armstrong Township",
    "BANKS": "Banks Township",
    "BLACKLICK": "Blacklick Township",
    "BRUSHVALLEY": "Brushvalley Township",
    "BUFFINGTON TOWNSHIP": "Buffington Township",
    "BUFFINGTON TWP": "Buffington Township",
    "BURRELL TOWNSHIP": "Burrell Township",
    "CANOE TOWNSHIP": "Canoe Township",
    "CENTER TOWNSHIP": "Center Township",
    "CHERRYHILL TOWNSHIP": "Cherryhill Township",
    "CONEMAUGH TOWSHIP": "Conemaugh Township",
    "GRANT TOWNSHIP": "Grant Township",
    "GREEN TOWNSHIP": "Green Township",
    "EAST MAHONING TOWNSHIP": "East Mahoning Township",
    "NORTH MAHONING TOWNSHIP": "North Mahoning Township",
    "SOUTH MAHONING TWP": "South Mahoning Township",
    "WEST MAHONING TOWNSHIP": "West Mahoning Township",
    "MONTGOMERY TONSHIP": "Montgomery Township",
    "PINE TOWNSHIP": "Pine Township",
    "RAYNE TOWNSHIP": "Rayne Township",
    "WASHINGTON TOWNSHIP": "Washington Township",
    "EAST WHEATFIELD TOWNSHIP": "East Wheatfield Township",
    "EAST WHEATFIELD TWP": "East Wheatfield Township",
    "WEST WHEATFIELD TOWNSHIP": "West Wheatfield Township",
    "WHITE TOWNSHIP": "White Township",
    "YOUNG TOWNSHIP": "Young Township",
    "ARMAGH BORO": "Armagh Borough",
    "CREEKSIDE BOROUGH": "Creekside Borough",
    "SMIKSBURG BOROUGH": "Smicksburg Borough",
    "SMICKSBURG BOROUGH": "Smicksburg Borough",
    "PLUMVILLE": "Plumville Borough",
    "BLAIRSVILLE WARD #1": "Blairsville Ward #1",
    "BLAIRSVILLE WARD #2": "Blairsville Ward #2",
    "BLAIRSVILLE WARD #3": "Blairsville Ward #3",
    "INDIANA EAST": "Indiana East",
    "INDIANA WEST #1": "Indiana West",
    "CHERRY TREE BOROUGH": "Cherry Tree Borough",
    "GLEN CAMPBELL BORO": "Glen Campbell Borough",
    "GLEN CAMPBELL BOROUGH": "Glen Campbell Borough",
    "CLYMER BOROUGH": "Clymer Borough",
    "ERNEST BOROUGH": "Ernest Borough",
    "HOMER CITY BOROUGH": "Homer City Borough",
    "MARION CENTER BORO": "Marion Center Borough",
    "MARION CENTER BOROUGH": "Marion Center Borough",
    "SALTSBURG BOROUGH": "Saltsburg Borough",
    "SHELOCTA BOROUGH": "Shelocta Borough",
    "HOMER-CENTER": "Homer-Center",
    "APOLLO RIDGE": "Apollo Ridge",
    "PUNXSUTAWNEY": "Punxsutawney",
    "PURCHASE LINE": "Purchase Line",
    "INDIANA": "Indiana",
    "HARMONY": "Harmony",
    "PENNS MANOR": "Penns Manor",
    "RIVER VALLEY": "River Valley",
    "UNITED": "United",
    "ARMSTRONG CO": "Armstrong Co",
    "MARION CENTER": "Marion Center",
}



def contest_tail(header):
    """(office family, canonical district or '', vote-independent base)

    header has already had its DEM/REP prefix stripped.
    """
    h = re.sub(r"\s+", " ", header).strip()
    if h in EXACT_OFFICES:
        return ("exact",) + EXACT_OFFICES[h]
    m = MDJ_RE.match(h)
    if m:
        return ("exact", "Magisterial District Judge", m.group(1))
    m = SGC_RE.match(h)
    if m:
        return ("sgc", "Small Games of Chance", MUNI.get(m.group(1).strip(),
                                                         m.group(1).strip()))
    m = re.match(r"^SCHOOL DIRECTOR (.+)$", h)
    if m:
        tail = m.group(1).strip()
        mm = re.match(r"^(ARMSTRONG CO) REG(?:ION)?\s?(\d)$", tail)
        if mm:
            return ("school", f"School Director Region {mm.group(2)}",
                    MUNI[mm.group(1)])
        mm = re.match(r"^(.+?)\s+REG(?:ION)?\s?(\d)$", tail)
        if mm:
            dist = MUNI.get(mm.group(1).strip())
            if dist:
                return ("school", f"School Director Region {mm.group(2)}", dist)
        mm = re.match(r"^(.+?) AT LARGE$", tail)
        if mm and mm.group(1).strip() in MUNI:
            return ("school", "School Director At Large",
                    MUNI[mm.group(1).strip()])
        if tail in MUNI:
            return ("school", "School Director At Large", MUNI[tail])
        return ("unknown", h, "")
    m = re.match(r"^(AUDITOR|SUPERVISOR|MEMBER OF COUNCIL|MEMBER COUNCIL|"
                 r"MEM OF COUNCIL|MAYOR|TAX COLLECTOR)\s+(.+)$", h)
    if m:
        base, tail = m.group(1), m.group(2).strip()
        dist = MUNI.get(tail)
        if dist is None:
            return ("unknown", h, "")
        if base == "AUDITOR":
            fam = "auditor"
        elif base == "SUPERVISOR":
            fam = "supervisor"
        elif base.endswith("COUNCIL"):
            fam = "council"
        elif base == "MAYOR":
            fam = "mayor"
        else:
            fam = "tax_collector"
        return (fam, base, dist)
    return ("unknown", h, "")


# second-seat office labels, taken from the county's 2023 general file and
# verified by primary->general candidate continuity (see module docstring)
AUDITOR_SEAT2 = {
    "Armstrong Township": "Township Auditor",
    "Buffington Township": "Township Auditor",
    "Rayne Township": "Township Auditor",
    "Armagh Borough": "Township Auditor (4 Year)",
    "Blacklick Township": "Township Auditor (4 Year)",
    "Burrell Township": "Township Auditor (4 Year)",
    "North Mahoning Township": "Township Auditor (4 Year)",
    "Pine Township": "Township Auditor (4 Year)",
    "Smicksburg Borough": "Township Auditor (4 Year)",
    "White Township": "Township Auditor (4 Year)",
}
SUPERVISOR_SEAT2 = {
    "Burrell Township": "Township Supervisor (4 Year)",
    "Cherryhill Township": "Township Supervisor (4 Year)",
    "West Mahoning Township": "Township Supervisor (4 Year)",
    "Young Township": "Township Supervisor (4 Year)",
    "East Wheatfield Township": "Township Supervisor (2 Year)",
}
COUNCIL_SEAT2 = "Borough Council (2 Year)"
MAYOR_OFFICE = "Mayor (2 Year)"
TAX_OFFICE = "Tax Collector"


def seat_office(fam, base, dist, seat_index):
    """office label for seat seat_index (0-based) of a contest instance."""
    if seat_index == 0:
        return {"auditor": "Township Auditor (6 Year)",
                "supervisor": "Township Supervisor (6 Year)",
                "council": "Borough Council (4 Year)",
                "mayor": MAYOR_OFFICE,
                "tax_collector": TAX_OFFICE}[fam]
    if fam == "auditor":
        return AUDITOR_SEAT2[dist]
    if fam == "supervisor":
        return SUPERVISOR_SEAT2[dist]
    if fam == "council":
        return COUNCIL_SEAT2
    raise ValueError(f"unexpected second seat for {fam} {dist}")


def parse(text_path, county):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    vote_idx = [k for k, l in enumerate(lines)
                if re.match(r"^vote for \d+$", l.strip(), re.I)]
    hdr_lines = set()
    for k in vote_idx:
        j = k - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0:
            hdr_lines.add(j)
    secs = []
    for n, k in enumerate(vote_idx):
        j = k - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        hdr = re.sub(r"\s+", " ", lines[j].strip())
        end = vote_idx[n + 1] if n + 1 < len(vote_idx) else len(lines)
        block = lines[k + 1:end]
        has_tvc = any(re.match(r"^total votes cast", b.strip(), re.I)
                      for b in block)
        secs.append(dict(k=k, line=k + 1, end=end, hdr=hdr, j=j,
                         block=block, tvc=has_tvc))

    # statistics block (first page)
    rv = None
    bc = None
    bc_blank = None
    for l in lines[:60]:
        s = l.strip()
        if re.match(r"^registered voters - total", s, re.I):
            n = nums_from(s.split())
            rv = int(n[0]) if n else None
        elif re.match(r"^ballots cast - total", s, re.I):
            bc = nums_from(s.split())
        elif re.match(r"^ballots cast - blank", s, re.I):
            n = nums_from(s.split())
            if n:
                bc_blank = (n[0], n[1] if len(n) > 1 else "",
                            n[2] if len(n) > 2 else "",
                            n[3] if len(n) > 3 else "")

    # ---- group sections into contest instances -----------------------------
    # key: (party, family/base, district); an occurrence without its own
    # "Total Votes Cast" continues the last instance; a TVC occurrence starts
    # a new instance only if the last one already has a TVC (two-seat towns).
    from collections import OrderedDict
    groups = OrderedDict()
    for s in secs:
        hdr = s["hdr"]
        party = ""
        tail = hdr
        m = re.match(r"^(DEM|REP)\s+(.+)$", hdr)
        if m:
            party, tail = m.group(1), m.group(2)
        fam, base, dist = contest_tail(tail)
        if fam == "unknown":
            groups.setdefault(("UNKNOWN", hdr), {"secs": [], "party": ""})
            groups[("UNKNOWN", hdr)]["secs"].append(s)
            groups[("UNKNOWN", hdr)]["party"] = ""
            continue
        if fam in ("exact", "sgc", "school"):
            key = (party, tail)
            groups.setdefault(key, {"secs": [], "party": party,
                                    "fam": fam, "base": base, "dist": dist})
            groups[key]["secs"].append(s)
        else:
            key = (party, fam, dist)
            groups.setdefault(key, {"secs": [], "party": party,
                                    "fam": fam, "base": base, "dist": dist})
            groups[key]["secs"].append(s)

    R = Rows(county)
    warnings = []
    ncontests = 0
    contest_stats = []   # one dict per contest instance, for reconciliation

    for key, g in groups.items():
        fam = g.get("fam")
        dist = g.get("dist", "")
        instances = []
        for s in g["secs"]:
            if s["tvc"] and instances and instances[-1]["has_tvc"]:
                instances.append({"secs": [s], "has_tvc": True})
            else:
                if not instances:
                    instances.append({"secs": [], "has_tvc": False})
                if s["tvc"]:
                    instances[-1]["has_tvc"] = True
                instances[-1]["secs"].append(s)
        for idx, inst in enumerate(instances):
            ncontests += 1
            if fam is None:
                raise ValueError(f"unrecognized contest header: {key[1]}")
            if fam in ("exact", "school"):
                office, odist = g["base"], dist
            elif fam == "sgc":
                office, odist = "Small Games of Chance", dist
            else:
                office = seat_office(fam, g["base"], dist, idx)
                odist = dist
                if idx:
                    warnings.append(f"seat 2 of {g['party']} {g['base']} "
                                    f"{dist}: labeled '{office}' (ballot "
                                    f"position; term from general file)")
            st = dict(office=office, dist=odist, party=g["party"],
                      cand=0, wri=0, wri_n=0, det=0, det_e=0, det_m=0,
                      det_p=0, na=0, tvc=None, over=0, under=0, n_rows=0)
            for s in inst["secs"]:
                emit_section(R, lines, s, hdr_lines, office, odist,
                             g["party"], st)
            if not st["wri_n"] and st["det"]:
                # write-in details with no aggregate row: fold into one
                # Write-ins row (repo convention)
                R.add(office, odist, st["party"], "Write-ins", st["det"],
                      st["det_e"], st["det_m"], st["det_p"])
                st["wri"] = st["det"]
                warnings.append(f"{office} | {odist} ({st['party']}): "
                                f"no Write-In Totals row; summed "
                                f"{st['det']} from detail rows")
            contest_stats.append(st)

    # ---- reconciliation (per contest instance: DEM/REP sections of the same
    # office/district must be checked separately, so the shared check_totals
    # (which keys on office+district only) is not usable here) --------------
    bad = []
    for st in contest_stats:
        agg = st["cand"] + st["wri"]
        if st["tvc"] is None:
            bad.append((st["office"], st["dist"], st["party"],
                        agg, None, "no Total Votes Cast line"))
        elif agg != st["tvc"]:
            bad.append((st["office"], st["dist"], st["party"],
                        agg, st["tvc"], "rows != Total Votes Cast"))
        if st["wri_n"] and st["det"] + st["na"] != st["wri"]:
            bad.append((st["office"], st["dist"], st["party"],
                        st["det"] + st["na"], st["wri"],
                        "write-in details != Write-In Totals"))

    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc:
        R.add_meta("Ballots Cast", bc[0],
                   bc[1] if len(bc) > 1 else "",
                   bc[2] if len(bc) > 2 else "",
                   bc[3] if len(bc) > 3 else "")
    if bc_blank:
        R.add_meta("Ballots Cast - Blank", *bc_blank)

    R.contests = [(st["office"], st["dist"], st["party"])
                  for st in contest_stats]
    R.warnings = warnings
    return R, ncontests, contest_stats, bad


def emit_section(R, lines, s, hdr_lines, office, dist, party, st):
    """Emit rows for one section's block (parse_esr row logic).

    Accumulates per-contest-instance reconciliation stats into st:
    cand = candidate row totals; wri = Write-In Totals aggregate;
    det = named write-in detail totals; na = Not Assigned; tvc = the
    source's Total Votes Cast.  Overvotes/Undervotes are excluded from
    the reconciliation aggregate (the source's Total Votes Cast excludes
    them), matching the shared check_totals().
    """
    for i in range(s["k"] + 1, s["end"]):
        if i in hdr_lines:
            # the next section's header line (last line of this block)
            continue
        stripped = lines[i].strip()
        if not stripped:
            continue
        if is_junk(stripped):
            continue
        if re.match(r"^precincts reporting", stripped, re.I):
            continue
        upper = stripped.upper()
        tokens = stripped.split()
        nums = nums_from(tokens[1:]) if tokens else []
        if not any(c.isdigit() for c in stripped):
            continue
        lead = tokens[0].upper().rstrip(":") if tokens else ""
        if upper.startswith("WRITE-IN TOTALS"):
            total, ed, mi, pr = vals_from(nums)
            if st["wri_n"]:
                raise ValueError(f"second Write-In Totals row for "
                                 f"{office} | {dist} ({party}) at "
                                 f"line {s['line']}")
            st["wri_n"] += 1
            st["wri"] = int(total)
            R.add(office, dist, party, "Write-ins", total, ed, mi, pr)
        elif upper.startswith("WRITE-IN:"):
            head, _p, allnums = split_candidate(tokens[1:])
            if allnums:
                total, ed, mi, pr = vals_from(allnums)
                st["det"] += int(total)
                st["det_e"] += int(ed or 0)
                st["det_m"] += int(mi or 0)
                st["det_p"] += int(pr or 0)
        elif re.match(r"^not assigned", stripped, re.I):
            total, _ed, _mi, _pr = vals_from(nums)
            st["na"] += int(total) if total else 0
        elif re.match(r"^total votes cast", stripped, re.I):
            tvc = int(nums[0]) if nums else None
            if st["tvc"] is not None and st["tvc"] != tvc:
                raise ValueError(f"two different Total Votes Cast values "
                                 f"({st['tvc']}, {tvc}) for {office} | "
                                 f"{dist} ({party}) at line {s['line']}")
            st["tvc"] = tvc
        elif re.match(r"^contest totals", stripped, re.I):
            pass
        elif re.match(r"^over ?votes", stripped, re.I):
            total, ed, mi, pr = vals_from(nums)
            st["over"] += int(total)
            R.add(office, dist, party, "Overvotes", total, ed, mi, pr)
        elif re.match(r"^under ?votes", stripped, re.I):
            total, ed, mi, pr = vals_from(nums)
            st["under"] += int(total)
            R.add(office, dist, party, "Undervotes", total, ed, mi, pr)
        elif lead in ("YES", "NO"):
            total, ed, mi, pr = vals_from(nums)
            st["cand"] += int(total)
            st["n_rows"] += 1
            R.add(office, dist, "", lead.capitalize(), total, ed, mi, pr)
        else:
            head, cand_party, allnums = split_candidate(tokens)
            name = clean_name(" ".join(head))
            if name and allnums:
                total, ed, mi, pr = vals_from(allnums)
                st["cand"] += int(total)
                st["n_rows"] += 1
                R.add(office, dist, party, name, total, ed, mi, pr)


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt-or-.pdf> <output.csv>")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    R, ncontests, contest_stats, bad = parse(src, COUNTY)
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {ncontests}")
    if bad:
        print(f"RECONCILIATION: {len(bad)} problem(s)")
        for office, dist, party, a, t, why in bad:
            print(f"  WARNING {party} {office} | {dist}: {why} "
                  f"({a} vs {t})")
    else:
        print("reconciliation: all contests match "
              "(candidates+write-ins == Total Votes Cast; write-in details "
              "== Write-In Totals)")


if __name__ == "__main__":
    main()