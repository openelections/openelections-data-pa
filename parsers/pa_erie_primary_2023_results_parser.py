#!/usr/bin/env python3
"""Erie County 2023 primary — county-level results from the official
"Municipal Primary Official Results" county summary (Dominion
Election Summary Report, Closed Primary, "Summary for: All Contests").

Contest headers are ALL CAPS with a party suffix:
    "JUSTICE OF THE SUPREME COURT-DEM (Vote for 1)"
    "COUNTY COUNCIL DISTRICT 1 -REP (Vote for 1)"
The suffix party goes in the party column of every row of that contest.

Office/district conventions mirror the county's 2023 general parser
(pa_erie_general_2023_results_parser.py): the full qualified header is
title-cased into `office` (district empty) except County Council districts,
which use office "County Council" with the district number.

Write-ins: the source itemizes named write-ins in detail blocks after the
contest's "Total Votes" row and reports a separate "Unresolved Write-In"
row.  The source's Total Votes includes the named write-ins but EXCLUDES
"Unresolved Write-In" (verified arithmetically across all 248 contests),
so Unresolved is excluded from the Write-ins aggregate here.  pdftotext
line-wraps (one write-in detail row and four candidate rows split around
their numbers line) are re-joined before parsing.

A per-precinct source also exists: "Erie County Official Results by
Precinct 2023 Primary.xlsx" (Dominion SOVC, 249 sheets: Sheet1 turnout +
248 contest sheets, same layout as the county's 2023 general export).
Give the .xlsx as input to produce the 10-column precinct CSV instead;
the sheet-level arithmetic there confirms the same write-in model
(Total Votes = candidates + Qualified Write In columns, Unresolved
Write-In excluded) verified on all 248 "PA County - Total" rows.

Usage: pa_erie_primary_2023_results_parser.py <input.pdf|txt|xlsx> <output.csv>
"""

import csv
import re
import sys
import tempfile

from pa_2023_summary_common import is_junk, parse_esr2, text_from_pdf, write_csv

COUNTY = "Erie"

# Contest title cleanups, matching the 2023 general parser.
SPECIAL = {
    "COUNTY COUNCIL DISTRICT 1": ("County Council", "1"),
    "COUNTY COUNCIL DISTRICT 3": ("County Council", "3"),
    "COUNTY COUNCIL DISTRICT 5": ("County Council", "5"),
    "COUNTY COUNCIL DISTRICT 7": ("County Council", "7"),
}


def fix_office(s):
    return s.replace(" Of The ", " of the ").replace(" Of ", " of ")


def titleize(s):
    s = re.sub(r"\s+", " ", s.strip())
    out = []
    for w in s.split(" "):
        if w.isupper() or w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())
    h = re.sub(r"\s*\(Vote for \d+\)\s*$", "", h).strip()
    party = ""
    m = re.search(r"[-\s]+(DEM|REP)$", h)
    if m:
        party = m.group(1).upper()
        h = h[:m.start()].rstrip(" -").strip()

    if h in SPECIAL:
        office, district = SPECIAL[h]
    else:
        h = (h
             .replace("ERIE TREASURER", "ERIE CITY TREASURER")
             .replace("ERIE COUNCIL", "ERIE CITY COUNCIL")
             .replace("CORRY COUNCIL", "CORRY CITY COUNCIL"))
        office, district = fix_office(titleize(h)), ""
    return office, district, party


def fix_wrapped_rows(text):
    """Re-join pdftotext line-wraps that split result rows.

    Two patterns occur:

    1. A write-in detail row whose Total cell wrapped to the next line
       (one occurrence, Girard School Director-REP):
           "amanda shellenburger   WRITE-IN   1   0   0"
           "                              1"            <- wrapped Total cell
           "adams"                                       <- wrapped name part

    2. A candidate row whose name wrapped around the party/numbers line
       (four occurrences, Northwestern School Director blocks):
           "MELISSA LINVILLE"
           "        DEM              72        25             0      97"
           "THATCHER"
       merged back to "MELISSA LINVILLE THATCHER DEM 72 25 0 97".
    """
    lines = text.splitlines()
    out = []
    i = 0
    nums_in = re.compile(r"[\d,]+")
    while i < len(lines):
        s = lines[i].strip()
        # case 1: write-in row missing its Total cell
        if re.search(r"write-in", s, re.I):
            after = s.upper().split("WRITE-IN", 1)[-1]
            nums = [n.replace(",", "") for n in nums_in.findall(after)]
            if len(nums) == 3 and i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                if re.fullmatch(r"[\d,]+", nxt):
                    out.append(s + " " + nxt)
                    i += 2
                    continue
        # case 2: candidate name wrapped around the party/numbers line
        if s and not nums_in.search(s) \
                and not re.search(r"write-in|\(vote for|^candidate\b"
                                  r"|^election day|^precincts reported"
                                  r"|^undervotes|^overvotes|^total votes"
                                  r"|^times cast|^unresolved|^page:",
                                  s, re.I) \
                and i + 2 < len(lines):
            nxt = lines[i + 1].strip()
            n3 = lines[i + 2].strip()
            m = re.match(r"^(DEM|REP)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+"
                         r"([\d,]+)$", nxt)
            if m and n3 and not nums_in.search(n3) \
                    and not re.search(r"write-in|\(vote for|^candidate\b"
                                      r"|^election day|^precincts reported"
                                      r"|^undervotes|^overvotes"
                                      r"|^total votes|^times cast"
                                      r"|^unresolved|^page:",
                                      n3, re.I):
                out.append(f"{s} {n3} {nxt}")
                i += 3
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def drop_unresolved(text):
    """Remove "Unresolved Write-In" rows (the source excludes them from
    contest totals); returns (clean_text, unresolved vote totals in file
    order)."""
    keep = []
    unres = []
    for l in text.splitlines():
        s = l.strip()
        if re.search(r"write-in", s, re.I) and s.upper().startswith("UNRESOLVED"):
            nums = [n.replace(",", "") for n in
                    re.findall(r"[\d,]+", s.upper().split("WRITE-IN", 1)[-1])]
            unres.append(int(nums[3]) if len(nums) >= 4 else
                         (int(nums[0]) if nums else 0))
            continue
        keep.append(l)
    return "\n".join(keep), unres


def parse_elector_block(text):
    """Ballot totals / Registered Voters from the page-1 Elector Group block.

    Returns {group: {"ed", "mail", "prov", "ballots", "rv"}} for
    Democratic, Republican and Total.
    """
    out = {}
    grp = None
    sub = {}
    for l in text.splitlines():
        s = l.strip()
        if s.startswith("Precincts Reported"):
            break
        m = re.match(r"^(Democratic|Republican|Total)\b", s)
        if m and "Election Day" in s:
            grp = m.group(1)
            sub = {"ed": int(re.findall(r"[\d,]+",
                                        s.split("Election Day", 1)[1])[0]
                             .replace(",", ""))}
        elif grp:
            if s.startswith("Mail-In"):
                sub["mail"] = int(re.findall(r"[\d,]+",
                                             s.split("Mail-In", 1)[1])[0]
                                  .replace(",", ""))
            elif s.startswith("Provisional"):
                sub["prov"] = int(re.findall(r"[\d,]+",
                                             s.split("Provisional", 1)[1])[0]
                                   .replace(",", ""))
            elif re.match(r"^Total\b", s):
                nums = [n.replace(",", "") for n in
                        re.findall(r"[\d,]+", s.split("Total", 1)[1])]
                if nums:
                    sub["ballots"] = int(nums[0])
                if len(nums) >= 3:
                    sub["rv"] = int(nums[2])
                out[grp] = sub
                grp, sub = None, {}
    return out


def _total_of(nums):
    """Total column of a row whose numbers run Election Day, Mail-In,
    Provisional, Total."""
    if len(nums) >= 4:
        return int(nums[3])
    if nums:
        return int(nums[0])
    return None


def reconcile(text):
    """Independent per-contest totals check, keyed by full contest identity.

    For each contest (text must already have Unresolved rows removed and
    wrapped write-in rows merged): candidate rows + named write-in detail
    rows compared against the contest's "Total Votes" line.
    Undervotes/Overvotes rows are excluded (the source's Total Votes excludes
    them too).  Returns (problems, sums); sums is
    [(key, vote_for_n, cand+win), ...] for every contest with a "Total
    Votes" line.
    """
    votefor = re.compile(r"\(vote for (\d+)\)", re.I)
    problems = []
    sums = []
    cur = None

    def finish():
        if cur is None or cur["total"] is None:
            return
        s = cur["cand"] + cur["win"]
        sums.append((cur["key"], cur["n"], s))
        if s != cur["total"]:
            problems.append((cur["key"], s, cur["total"],
                             cur["cand"], cur["win"]))

    for raw in text.splitlines():
        s = raw.strip()
        if not s or is_junk(s):
            continue
        if votefor.search(s):
            finish()
            cur = {"key": map_contest(s), "cand": 0, "win": 0, "total": None,
                   "n": int(votefor.search(s).group(1))}
            continue
        if cur is None:
            continue
        low = s.lower()
        if low.startswith(("precincts reported", "candidate", "election day",
                           "undervotes", "overvotes")):
            continue
        if re.search(r"write-in", s, re.I):
            after = s.upper().split("WRITE-IN", 1)[-1]
            nums = [n.replace(",", "") for n in re.findall(r"[\d,]+", after)]
            if len(nums) >= 4:
                cur["win"] += int(nums[3])
            elif len(nums) == 1:
                cur["win"] += int(nums[0])
            continue
        if low.startswith("total votes"):
            cur["total"] = _total_of(
                [n.replace(",", "") for n in re.findall(r"[\d,]+", s)])
            continue
        nums = [n.replace(",", "") for n in re.findall(r"[\d,]+", s)]
        if not nums:
            continue
        cur["cand"] += _total_of(nums)
    finish()
    return problems, sums


# ---- precinct CSV (SOVC .xlsx) -------------------------------------------

GROUPS = {"Election Day": "election_day", "Mail-In": "mail", "Provisional": "provisional"}
SKIP_PRECINCT = {
    "County", "PA County", "Cumulative", "PA County - Total", "Cumulative - Total",
    "County - Total", "Precinct",
}

# Candidate name fixes after .title() mangling (source is ALL CAPS),
# matching the county's 2023 general parser.
NAME_FIX = {
    "Daniel Mccaffery": "Daniel McCaffery",
    "Harry F Smail Jr": "Harry F. Smail Jr.",
    "Brian M Mcgowan": "Brian M McGowan",
    "Patricia A Mccullough": "Patricia A McCullough",
    "Denise Mccumber": "Denise McCumber",
    "Dennis Buzz Mcnally": "Dennis Buzz McNally",
    "James Jim Dwyer Iv": "James Jim Dwyer IV",
}


def contest_from_title(title):
    """Normalize an SOVC sheet title ("NAME-DEM (Vote for  1) \\nDEM  ") to
    the county-summary header shape, then delegate to map_contest."""
    t = re.sub(r"\s+", " ", str(title).strip())
    t = re.sub(r"\s*\(Vote for\s*\d+\)\s*", " ", t).strip()
    # the title ends with a duplicated party token from the second line
    m = re.search(r"\s+(DEM|REP)$", t)
    if m and t[:m.start()].rstrip().endswith(m.group(1)):
        t = t[:m.start()].rstrip()
    return map_contest(t)


def parse_precinct(input_path, output_path):
    """Parse the Dominion SOVC per-precinct .xlsx into a 10-column CSV."""
    import pandas as pd

    xl = pd.ExcelFile(input_path)

    # ---- Sheet1: turnout per precinct ------------------------------------
    bc = {}  # precinct -> dict
    s1 = xl.parse("Sheet1", header=None)
    cur = None
    for _, r in s1.iterrows():
        c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
        if c0.startswith("Page:"):
            continue
        if c0 in GROUPS:
            if cur:
                bc[cur][GROUPS[c0]] = int(r[5]) if pd.notna(r[5]) else 0
        elif c0 == "Total":
            if cur:
                bc[cur]["votes"] = int(r[5]) if pd.notna(r[5]) else 0
                bc[cur]["rv"] = int(r[1]) if pd.notna(r[1]) else 0
        elif c0 in SKIP_PRECINCT or c0.startswith("Cumulative") or "Total" in c0:
            cur = None
        elif c0:
            cur = c0
            bc.setdefault(cur, {})

    rows = []
    header = ["county", "precinct", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]
    problems = []
    party_tag_conflicts = []

    # ---- Contest sheets: one contest (party) per sheet --------------------
    for s in xl.sheet_names[1:]:
        df = xl.parse(s, header=None)
        title = re.sub(r"\s+", " ", str(df.iloc[1, 0]).strip())
        office, district, cparty = contest_from_title(title)

        # candidate columns from header row 3
        cands = []  # (col, candidate, party)
        for i, v in enumerate(df.iloc[3]):
            if pd.isna(v):
                continue
            t = re.sub(r"\s+", " ", str(v).strip())
            if not t or t.startswith("Precinct") or "Registered" in t \
                    or "Undervotes" in t:
                continue
            if t.startswith("Total Votes"):
                continue
            if "Unresolved" in t and "Write" in t:
                continue  # excluded from Total Votes (see docstring)
            if "Qualified Write In" in t:
                cands.append((i, "Write-ins", ""))
                continue
            name = t
            tag = ""
            if "\n" in str(v):
                parts = [p.strip() for p in str(v).split("\n") if p.strip()]
                name = parts[0]
                if len(parts) > 1 and parts[-1].startswith("("):
                    tag = parts[-1].strip("()").upper()
            if tag and tag != cparty:
                party_tag_conflicts.append((title, name, tag, cparty))
            name = re.sub(r"\s+", " ", name)
            cname = name.title() if name.isupper() else name
            cands.append((i, NAME_FIX.get(cname, cname), ""))

        # walk data rows
        cur = None
        accum = {}  # col -> {group: value} for current precinct

        def flush():
            if cur is None:
                return
            merged = {}  # (candidate, party) -> [votes, ed, ml, pv]
            for col, cand, party in cands:
                d = accum.get(col, {})
                votes = int(d.get("votes", 0) or 0)
                ed = int(d.get("election_day", 0) or 0)
                ml = int(d.get("mail", 0) or 0)
                pv = int(d.get("provisional", 0) or 0)
                agg = merged.setdefault((cand, party), [0, 0, 0, 0])
                agg[0] += votes; agg[1] += ed; agg[2] += ml; agg[3] += pv
            for (cand, party), (votes, ed, ml, pv) in sorted(merged.items()):
                if ed + ml + pv != votes:
                    problems.append((office, district, cparty, cand, cur,
                                     ed, ml, pv, votes))
                # Write-ins rows carry the contest party (repo convention in
                # the 2023 primary files), as do named candidates.
                rows.append([COUNTY, cur, office, district, cparty, cand,
                             votes, ed, ml, pv])

        for _, r in df.iterrows():
            c0 = str(r[0]).strip() if pd.notna(r[0]) else ""
            if c0.startswith("Page:") or "(Vote for" in c0:
                continue
            if c0 in GROUPS:
                if cur is not None:
                    g = GROUPS[c0]
                    for col, _, _ in cands:
                        accum.setdefault(col, {})[g] = r[col] if pd.notna(r[col]) else 0
            elif c0 == "Total":
                if cur is not None:
                    for col, _, _ in cands:
                        accum.setdefault(col, {})["votes"] = r[col] if pd.notna(r[col]) else 0
            elif c0 in SKIP_PRECINCT or c0.startswith("Cumulative") or "Total" in c0:
                if c0 == "Precinct" or not c0:
                    continue
                flush()
                cur = None
                accum = {}
            elif c0:
                flush()
                cur = c0
                accum = {}
        flush()

    # Registered Voters / Ballots Cast rows (from Sheet1)
    for prec, d in bc.items():
        rows.append([COUNTY, prec, "Registered Voters", "", "", "",
                     d.get("rv", 0), "", "", ""])
        bt = d.get("votes", 0)
        ed, ml, pv = d.get("election_day", 0), d.get("mail", 0), d.get("provisional", 0)
        if ed + ml + pv != bt:
            problems.append(("Ballots Cast", "", "", prec, prec, ed, ml, pv, bt))
        rows.append([COUNTY, prec, "Ballots Cast", "", "", "", bt, ed, ml, pv])

    with open(output_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {output_path}")
    print(f"precincts: {len(bc)}")
    for p in problems[:30]:
        print("BREAKDOWN MISMATCH", p)
    print(f"breakdown problems: {len(problems)}")
    if party_tag_conflicts:
        print(f"candidate party-tag conflicts with contest header: "
              f"{len(party_tag_conflicts)}")
        for t in party_tag_conflicts[:10]:
            print("  ", t)
    return rows


def main():
    if len(sys.argv) < 3:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt|xlsx> <output.csv>")
    src, dst = sys.argv[1], sys.argv[2]
    if src.lower().endswith(".xlsx"):
        parse_precinct(src, dst)
        return
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    with open(src, encoding="utf-8") as fh:
        text = fh.read()

    fixed = fix_wrapped_rows(text)
    engine_text, unresolved = drop_unresolved(fixed)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
        tmp.write(engine_text)
        R = parse_esr2(tmp.name, COUNTY, map_contest, titlecase_names=True)

    # Stamp the contest party on Undervotes and Write-ins rows.  The engine
    # emits them with an empty party, which collides DEM vs REP in the
    # duplicate_entries test.  parse_esr2 emits exactly one Undervotes row
    # per contest, first in each contest block, in source order — so
    # R.contests indexes line up 1:1 with Undervotes rows, and each
    # Write-ins row belongs to the contest whose Undervotes row most
    # recently appeared.
    und = -1
    for row in R.rows:
        if row[4] == "Undervotes":
            und += 1
            row[3] = R.contests[und][2]
        elif row[4] == "Write-ins" and row[3] == "":
            row[3] = R.contests[und][2]
    nu = sum(1 for row in R.rows if row[4] == "Undervotes")
    if nu != len(R.contests):
        print(f"WARNING: {nu} Undervotes rows vs {len(R.contests)} contests; "
              f"party stamping may be off")

    # Registered Voters / Ballots Cast breakdown from the Elector Group block.
    elector = parse_elector_block(text)
    tot = elector.get("Total", {})
    for idx, r in enumerate(R.rows):
        if r[1] == "Ballots Cast" and r[5] == str(tot.get("ballots", "")):
            R.rows[idx] = [r[0], r[1], "", "", "",
                           str(tot.get("ballots", "")),
                           str(tot.get("ed", "")),
                           str(tot.get("mail", "")),
                           str(tot.get("prov", ""))]
    if tot.get("rv"):
        R.rows.append([COUNTY, "Registered Voters", "", "", "",
                       str(tot["rv"]), "", "", ""])

    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")

    problems, sums = reconcile(engine_text)
    if problems:
        print(f"contest totals check: {len(problems)} MISMATCHES")
        for (office, dist, party), s, t, c, w in problems:
            print(f"  {office} | {dist} | {party}: cand {c} + writeins {w} "
                  f"= {s} != Total Votes {t}")
    else:
        print("contest totals check: all contests reconcile")

    # Party-ballot arithmetic: each party's per-contest vote sums must be
    # <= that party's ballots cast times the contest's "Vote for N".
    PARTY_GROUPS = {"DEM": "Democratic", "REP": "Republican"}
    for party in ("DEM", "REP"):
        ballots = elector.get(PARTY_GROUPS[party], {}).get("ballots")
        if ballots is None:
            continue
        psums = [(k, n, s) for k, n, s in sums if k[2] == party]
        over = [(k, s) for k, n, s in psums if s > ballots * n]
        top = max(psums, key=lambda kv: kv[2]) if psums else None
        status = "ok" if not over else f"OVER: {over}"
        print(f"party-ballot arithmetic {party}: contests={len(psums)} "
              f"ballots={ballots} "
              f"max_sum={top[2] if top else '-'} (vote for "
              f"{top[1] if top else '-'}) {status}")

    for grp in ("Democratic", "Republican", "Total"):
        d = elector.get(grp)
        if d:
            print(f"elector group {grp}: ballots={d.get('ballots')} "
                  f"ed={d.get('ed')} mail={d.get('mail')} "
                  f"prov={d.get('prov')} rv={d.get('rv')}")

    nu = [u for u in unresolved if u]
    print(f"unresolved write-in rows excluded from aggregates: "
          f"{len(nu)} contests, {sum(nu)} votes")


if __name__ == "__main__":
    main()