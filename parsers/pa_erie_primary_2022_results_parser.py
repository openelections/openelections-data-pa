#!/usr/bin/env python3
"""Parse Erie County 2022 general primary election results (Dominion SOVC Excel export).

Input: "Erie PA 2022 Primary Precinct-ELECTION-RESULTS-1-1.xlsx" — 468 sheets:
  * Sheet1: per-precinct turnout table (Registered Voters, Cards Cast /
    Voters Cast by counting group, all parties combined; this is a closed
    primary and the DO NOT split turnout by party) — source for
    Registered Voters / Ballots Cast rows.
  * Sheets 2-468: one contest(-party) per sheet.  Sheet title (row 2, col A)
    e.g. "UNITED STATES SENATOR-REP (Vote for 1) \nREP" — contest name with a
    -DEM/-REP suffix and the party repeated at the end.  Header row 4 has two
    column blocks: left [Precinct, Times Cast, Registered Voters] (that
    party's ballots / registered voters) and right [Precinct, <candidates
    "(PARTY)">, Total Votes, <"<name> Qualified Write In"> columns,
    "Unresolved Write-In"].  Each precinct contributes 5 rows (precinct
    header, Election Day, Mail-In, Provisional, Total).  Trailing
    "PA County - Total" / "Cumulative" rows repeat and carry per-candidate
    county totals ("County - Total" row, used for verification).
  * Sheets 22+ are local Democratic Committeeman/Committeewoman and
    Republican Committeeperson races; several list no named candidate and
    only write-in columns (emitted as "Write Ins" rows when nonzero).

The file was generated 2022-06-03 and carries the PRE-RECOUNT counts for the
Republican U.S. Senate contest (Oz 7,545 / McCormick 8,207).  The June
recount ("Erie PA 2022 Primary RECOUNT-FINAL-1.pdf" — county totals only, no
per-precinct data) gives Oz 7,548 / McCormick 8,208 / Barnette 6,248 /
Bartos 1,316.  This parser therefore keeps the pre-recount per-precinct
Senate-REP rows; the per-candidate residual vs the post-recount anchor is
Oz -3, McCormick -1, Barnette +1, Bartos +1 (see verification).

Output: OpenElections precinct CSV
  county,precinct,office,district,party,candidate,votes,election_day,mail,provisional
  (mail carries the source Mail-In votes).

Usage:
    uv run python pa_erie_primary_2022_results_parser.py <input.xlsx> <output.csv>
"""

import csv
import re
import sys

import pandas as pd

COUNTY = "Erie"

GROUPS = {"Election Day": "election_day", "Mail-In": "mail", "Provisional": "provisional"}
SKIP_ROW_STARTS = ("PA County", "Cumulative", "County - Total")
SKIP_LABELS = {"County", "PA County", "Precinct"}

ORDINAL_RE = re.compile(r"(\d+)(St|Nd|Rd|Th)\b")
ROMAN = {"I", "II", "III", "IV", "VII", "VIII", "VI", "IX", "XI", "XII", "XIII"}


def smart_title(name):
    """Title-case an ALL-CAPS name, keeping ordinals lowercase and Roman numerals caps."""
    out = []
    for w in name.split():
        if w.upper() in ROMAN:
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def titlecase(name):
    name = re.sub(r"\s+", " ", name).strip()
    if name.isupper():
        name = smart_title(name)
        name = ORDINAL_RE.sub(lambda m: m.group(1) + m.group(2).lower(), name)
        # fix digit+dotted-suffix like "1St" already handled; also "Ward 1St" odd cases
        name = re.sub(r"\b(\d+)(Sr|Nd|Rd|St|Th)\b", lambda m: m.group(1) + m.group(2).lower(), name)
    return name


def parse_title(raw):
    """Split a sheet title into (contest_name, vote_for_n, party)."""
    t = " ".join(str(raw).split())  # collapse newlines/multi-spaces
    m = re.match(r"^(?P<name>.*?)\s*\(Vote for\s*(\d+)\)\s*(?P<tail>DEM|REP)?\s*$", t)
    if not m:
        raise ValueError("unparsed sheet title: %r" % raw)
    name, n, tail = m.group("name").strip(), int(m.group(2)), m.group("tail")
    if name.endswith("-DEM") or name.endswith("-REP"):
        tail = name[-3:]
        name = name[:-4].strip()
    return name, n, tail


def office_for(name, party):
    """Map a contest name (without -DEM/-REP suffix) to (office, district)."""
    n = name
    m = re.match(r"^REPRESENTATIVE IN CONGRESS\s*[–-]?\s*(\d+)(ST|ND|RD|TH)\s+DISTRICT$", n, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(ST|ND|RD|TH)\s+LEGISLATIVE DISTRICT$", n, re.I)
    if m:
        return "State House", m.group(1)
    if re.match(r"^UNITED STATES SENATOR$", n, re.I):
        return "U.S. Senate", ""
    if re.match(r"^GOVERNOR$", n, re.I):
        return "Governor", ""
    if re.match(r"^LT\.?\s+GOVERNOR$", n, re.I):
        return "Lieutenant Governor", ""
    if re.match(r"^MEMBERS? OF THE DEMOCRATIC STATE COMMITTEE$", n, re.I):
        return "Member of Democratic State Committee", ""
    if re.match(r"^MEMBERS? OF THE REPUBLICAN STATE COMMITTEE$", n, re.I):
        return "Member of Republican State Committee", ""
    return titlecase(n), ""


def parse(input_path, output_path):
    xl = pd.ExcelFile(input_path)
    rows = []
    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    problems = []

    # ---- Sheet1: turnout per precinct (all parties) ------------------------
    bc = {}  # precinct -> dict(rv, votes, election_day, mail, provisional)
    s1 = xl.parse("Sheet1", header=None)
    cur = None
    for _, r in s1.iterrows():
        c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
        if c0.startswith("Page:"):
            continue
        if c0 in GROUPS:
            if cur:
                col = GROUPS[c0]
                bc[cur][col] = int(float(r[5])) if pd.notna(r[5]) else 0
        elif c0 == "Total":
            if cur:
                bc[cur]["votes"] = int(float(r[5])) if pd.notna(r[5]) else 0
                bc[cur]["rv"] = int(float(r[1])) if pd.notna(r[1]) else 0
        elif c0 in SKIP_LABELS or c0.startswith(SKIP_ROW_STARTS):
            cur = None
        elif c0:
            cur = c0
            bc.setdefault(cur, {})

    # ---- Contest sheets ----------------------------------------------------
    for s in xl.sheet_names[1:]:
        df = xl.parse(s, header=None)
        title_name, vote_for, party = parse_title(df.iloc[1, 0])
        office, district = office_for(title_name, party)

        # candidate columns from header row (row index 3, right block i>=4)
        named = []      # (col, candidate)
        writeins = []   # col list aggregated to "Write Ins"
        cand_headers = {}
        for i, v in enumerate(df.iloc[3]):
            if pd.isna(v) or i < 4:
                continue
            t = str(v).strip()
            if t in ("Precinct",) or "Registered" in t or "Times Cast" in t:
                continue
            if t.startswith("Total Votes"):
                continue
            if "Qualified Write In" in t or ("Unresolved" in t and "Write" in t):
                writeins.append(i)
                continue
            cname = re.sub(r"\s+", " ", t.split("\n")[0]).strip()
            m = re.match(r"^(.*?)\s*\((DEM|REP)\)$", cname)
            ctag = ""
            if m:
                cname, ctag = m.group(1).strip(), m.group(2)
            named.append((i, titlecase(cname), ctag or party, t))
        cur = None
        accum = {}  # col -> {group: value}
        sheet_sum = {}  # col -> votes summed over precincts (County-Total check)

        def flush():
            if cur is None:
                return
            merged = {}
            for col, cand, cparty, rawhead in named:
                d = accum.get(col, {})
                votes = int(float(d.get("votes", 0) or 0))
                ed = int(float(d.get("election_day", 0) or 0))
                ml = int(float(d.get("mail", 0) or 0))
                pv = int(float(d.get("provisional", 0) or 0))
                key = (cand, cparty)
                agg = merged.setdefault(key, [0, 0, 0, 0])
                agg[0] += votes; agg[1] += ed; agg[2] += ml; agg[3] += pv
            wtot, wed, wml, wpv = 0, 0, 0, 0
            for col in writeins:
                d = accum.get(col, {})
                wtot += int(float(d.get("votes", 0) or 0))
                wed += int(float(d.get("election_day", 0) or 0))
                wml += int(float(d.get("mail", 0) or 0))
                wpv += int(float(d.get("provisional", 0) or 0))
            if wtot:
                merged[( "Write Ins", party)] = [wtot, wed, wml, wpv]
            for (cand, cparty), (votes, ed, ml, pv) in merged.items():
                if ed + ml + pv != votes:
                    problems.append((office, cand, cur, ed, ml, pv, votes))
                rows.append([COUNTY, cur, office, district, cparty, cand, votes, ed, ml, pv])
            for col in list(accum.keys()):
                if "votes" in accum[col]:
                    sheet_sum[col] = sheet_sum.get(col, 0) + int(float(accum[col]["votes"] or 0))

        for _, r in df.iterrows():
            c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
            if c0.startswith("Page:") or "(Vote for" in c0:
                continue
            if c0 in SKIP_LABELS or c0.startswith(SKIP_ROW_STARTS):
                if c0:
                    flush()
                    cur = None
                    accum = {}
                continue
            if c0 in GROUPS:
                if cur is not None:
                    g = GROUPS[c0]
                    for i in list(range(len(df.columns))):
                        if pd.isna(r[i]):
                            continue
                        if r[i] == c0:  # right block repeats the group label
                            continue
                        accum.setdefault(i, {})[g] = r[i]
            elif c0 == "Total":
                if cur is not None:
                    for i in list(range(len(df.columns))):
                        if pd.isna(r[i]):
                            continue
                        try:
                            v = float(r[i])
                        except (TypeError, ValueError):
                            continue
                        accum.setdefault(i, {})["votes"] = r[i]
            elif c0:
                flush()
                cur = c0
                accum = {}
        flush()

        # verify precinct sums against the sheet's printed County - Total row
        ct = None
        for _, r in df.iterrows():
            c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
            if c0 == "County - Total":
                ct = r
        if ct is not None:
            for col, cand, cparty, rawhead in named:
                printed = int(float(ct[col])) if pd.notna(ct[col]) else 0
                summed = sheet_sum.get(col, 0)
                if summed != printed:
                    problems.append(("CT-MISMATCH", office, cand, summed, printed))
        for col in writeins:
            printed = int(float(ct[col])) if ct is not None and pd.notna(ct[col]) else 0
            summed = sheet_sum.get(col, 0)
            if summed != printed:
                problems.append(("CT-MISMATCH-WI", office, "Write Ins(%s)" % col, summed, printed))

    # Registered Voters / Ballots Cast rows
    for prec, d in bc.items():
        rows.append([COUNTY, prec, "Registered Voters", "", "", "", d.get("rv", 0), "", "", ""])
        bt = d.get("votes", 0)
        ed, ml, pv = d.get("election_day", 0), d.get("mail", 0), d.get("provisional", 0)
        if ed + ml + pv != bt:
            problems.append(("Ballots Cast", prec, prec, ed, ml, pv, bt))
        rows.append([COUNTY, prec, "Ballots Cast", "", "", "", bt, ed, ml, pv])

    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    for p in problems[:30]:
        sys.stderr.write("BREAKDOWN MISMATCH office=%s cand=%s prec=%s: ed=%s mail=%s prov=%s total=%s\n" % p)
    sys.stderr.write("rows=%d precincts=%d problems=%d\n" % (len(rows), len(bc), len(problems)))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    parse(sys.argv[1], sys.argv[2])