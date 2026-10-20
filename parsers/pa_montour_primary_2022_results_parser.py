#!/usr/bin/env python3
"""
Parse Montour County PA 2022 Primary (May 17, 2022) precinct results.

Sources (Electionware "Summary Results Report" / OFFICIAL RESULTS):
  * "Montour PA Official All Precincts Report 5-17-22.pdf" -- per-municipality
    sections (15 municipalities; the finest granularity Montour reports),
    each with a STATISTICS block (Registered Voters / Ballots Cast by party)
    and party contests with TOTAL / Election Day / Absentee / Provisional
    columns plus indented Write-In rows.
  * "Montour PA May 2022 Recount Summary.pdf" -- POST-recount per-municipality
    results for the GOP UNITED STATES SENATOR contest (May 27, 2022 machine
    recount, Oz/McCormick).  The All Precincts Report is dated 05/25/2022,
    i.e. pre-recount; the recount PDF is the certified per-precinct
    post-recount source, so it REPLACES the GOP U.S. Senate values from the
    main report.

Notes:
  * "Not Assigned" rows (Electionware undervote bookkeeping) are dropped.
  * Named "Write-In: X" detail rows are collapsed into a single ``Write Ins``
    row (carrying the contest party) per contest/municipality.
  * Contest headers repeat at page breaks (continuation blocks); candidate
    rows for the same (municipality, contest) are merged.
  * Electionware "Absentee" combines absentee + mail-in votes -> ``mail``.
  * Registered Voters / Ballots Cast emitted with party where the source
    splits by party; Ballots Cast - Blank as its own office row (the all-zero
    NONPARTISAN rows are omitted).
  * Contest headers do not carry district numbers; U.S. House district 9 and
    State House district 108 follow Montour's 2022 general file / ENR.
  * Local municipal party committee contests ("DEM ANTHONY TWP COMMITTEEMAN")
    are emitted as "Democratic Committeeman" etc., with the municipality as
    district (Clinton County 2022-primary precedent).

Usage:
    python parsers/pa_montour_primary_2022_results_parser.py \\
        <all-precincts.pdf-or-txt> <recount.pdf-or-txt> <output.csv>
"""

import csv
import os
import re
import subprocess
import sys

COUNTY = "Montour"

MUNICIPALITIES = [
    "ANTHONY TWP",
    "COOPER TWP",
    "DANVILLE BORO FIRST WARD",
    "DANVILLE BORO SECOND WARD",
    "DANVILLE BORO THIRD WARD",
    "DANVILLE BORO FOURTH WARD",
    "DERRY TWP",
    "LIBERTY TWP",
    "LIMESTONE TWP",
    "MAHONING I",
    "MAHONING II",
    "MAYBERRY TWP",
    "VALLEY TWP",
    "WASHINGTONVILLE BORO",
    "WEST HEMLOCK TWP",
]
MUNI_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(m) for m in MUNICIPALITIES) + r")\s*$")

OFFICE_MAP = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", "9"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", "108"),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE":
        ("Member of Democratic State Committee", ""),
    "MEMBER OF REPUBLICAN STATE COMMITTEE":
        ("Member of Republican State Committee", ""),
}

NUM = r"(\d{1,3}(?:,\d{3})*)"
PCT = r"(\d{1,3}(?:\.\d+)?%)"
CONTEST_RE = re.compile(r"^(DEM|REP)\s+(.+?)\s*$")
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)")
CAND_RE = re.compile(
    r"^(.*?)\s+" + NUM + r"\s+" + PCT + r"\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s*$")
TOTAL_CAST_RE = re.compile(
    r"^Total Votes Cast\s+" + NUM + r"\s+" + PCT + r"\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s*$")
WRITE_IN_TOTALS_RE = re.compile(
    r"^Write-In Totals\s+" + NUM + r"\s+" + PCT + r"\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s*$")
CONTEST_TOTALS_RE = re.compile(
    r"^Contest Totals\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s+" + NUM + r"\s*$")
NOT_ASSIGNED_RE = re.compile(r"^Not Assigned")

RV_RE = re.compile(
    r"^Registered Voters - (Total|DEMOCRATIC|REPUBLICAN|NONPARTISAN)\s+" + NUM + r"\s*$")
BC_LABEL_RE = re.compile(r"^Ballots Cast - (\w+)")
NUM_ONLY_RE = re.compile(r"^(?:\s*\d{1,3}(?:,\d{3})*)+\s*$")

LOCAL_COMMITTEE_RE = re.compile(r"^(DEM|REP)\s+(.+?)\s+COMMITTEE(MAN|WOMAN|PERSON)\s*$")

# Recount table columns, in order.
REC_CANDIDATES = ["KATHY BARNETTE", "MEHMET OZ", "GEORGE BOCHETTO", "JEFF BARTOS",
                  "DAVE MCCORMICK", "SEAN GALE", "CARLA SANDS", "WRITE-IN",
                  "OVERVOTE", "NO VOTE"]
# Recount municipality names (from the wrapped PDF text) -> All Precincts names.
REC_TO_AP = {
    "ANTHONY TOWNSHIP": "ANTHONY TWP",
    "COOPER TOWNSHIP": "COOPER TWP",
    "DANVILLE BOROUGH FIRST WARD": "DANVILLE BORO FIRST WARD",
    "DANVILLE BOROUGH SECOND WARD": "DANVILLE BORO SECOND WARD",
    "DANVILLE BOROUGH THIRD WARD": "DANVILLE BORO THIRD WARD",
    "DANVILLE BOROUGH FOURTH WARD": "DANVILLE BORO FOURTH WARD",
    "DERRY TOWNSHIP": "DERRY TWP",
    "LIBERTY TOWNSHIP": "LIBERTY TWP",
    "LIMESTONE TOWNSHIP": "LIMESTONE TWP",
    "MAHONING TOWNSHIP I": "MAHONING I",
    "MAHONING TOWNSHIP II": "MAHONING II",
    "MAYBERRY TOWNSHIP": "MAYBERRY TWP",
    "VALLEY TOWNSHIP": "VALLEY TWP",
    "WASHINGTONVILLE BOROUGH": "WASHINGTONVILLE BORO",
    "WEST HEMLOCK TOWNSHIP": "WEST HEMLOCK TWP",
}
SKIP_WORDS = {"ELECTION", "DAY", "ABSENTEE", "MAIL-IN", "MAIL", "IN",
              "PROVISIONAL", "TOTAL", "VOTES", "VOTE", "COUNTY", "OF", "MONTOUR"}
REC_HEADER_WORDS = {"KATHY", "BARNETTE", "MEHMET", "OZ", "GEORGE", "BOCHETTO",
                    "JEFF", "BARTOS", "DAVE", "MCCORMICK", "SEAN", "GALE",
                    "CARLA", "SANDS", "WRITE-IN", "OVERVOTE", "NO"}


def _to_int(tok):
    return int(tok.replace(",", ""))


def num_tokens(text):
    return re.findall(r"\d[\d,]*", text)


def text_of_pdf(path):
    if str(path).lower().endswith(".pdf"):
        out = os.path.splitext(path)[0] + ".txt"
        subprocess.run(["pdftotext", "-layout", path, out], check=True)
        return out
    return path


SPECIAL_CAND = {"MCCORMICK": "McCormick", "MCSWAIN": "McSwain",
                "MCDONOUGH": "McDonough"}


def title_cand(name):
    out = []
    for w in name.split():
        if w.upper() in SPECIAL_CAND:
            out.append(SPECIAL_CAND[w.upper()])
        elif w.upper() in ("I", "II", "III"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


# ---------------------------------------------------------- all-precincts

def parse_all_precincts(txt_path, warnings):
    """Returns sections {(muni, office, district, party): {...}}, stats, order."""
    with open(txt_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    sections = {}
    stats = {}
    order = []
    muni = None
    stats_mode = False
    contest = None
    pending = []

    def sec(key):
        if key not in sections:
            sections[key] = {"cands": [], "writeins": None, "notassigned": None,
                             "totals_cast": None, "contest_totals": None}
            order.append(key)
        return sections[key]

    for raw in lines:
        line = raw.rstrip()
        st = line.strip()
        if not st:
            continue
        if ("Summary Results Report" in line or st == "Primary Election"
                or "May 17, 2022" in line or "Montour County" in line
                or "Official All Precincts Report" in line
                or "Report generated with" in line or st == "OFFICIAL RESULTS"):
            continue

        m = MUNI_RE.match(line)
        if m:
            muni = m.group(1)
            stats_mode, contest, pending = False, None, []
            stats.setdefault(muni, {})
            continue
        if st == "STATISTICS":
            stats_mode, pending = True, []
            continue
        if muni is None:
            continue

        # ---- statistics block
        if stats_mode:
            if CONTEST_RE.match(st) or VOTE_FOR_RE.match(st):
                stats_mode, pending = False, []
            else:
                rv = RV_RE.match(st)
                if rv:
                    stats[muni]["rv_" + rv.group(1).lower()] = _to_int(rv.group(2))
                    continue
                bcm = BC_LABEL_RE.match(st)
                nums = num_tokens(st)
                if bcm and not nums and pending:
                    nums = pending.pop()
                if bcm:
                    label = bcm.group(1).lower()
                    if label == "total":
                        stats[muni]["bc_total"] = [_to_int(x) for x in nums]
                    elif label in ("democratic", "republican"):
                        stats[muni]["bc_" + label] = [_to_int(x) for x in nums]
                    elif label == "blank":
                        stats[muni]["bc_blank"] = [_to_int(x) for x in nums]
                    # nonpartisan rows (all zeros in 2022) omitted
                    continue
                if NUM_ONLY_RE.match(st):
                    pending.append(num_tokens(st))
                    continue
                continue  # Voter Turnout lines

        # ---- contest headers
        cs = CONTEST_RE.match(st)
        if cs:
            party, name = cs.group(1), cs.group(2).strip()
            title, district = OFFICE_MAP.get(name, (None, None))
            if title is None:
                loc = LOCAL_COMMITTEE_RE.match(st)
                if loc:
                    label = []
                    for w in loc.group(2).split():
                        wl = w.lower()
                        if wl == "twp":
                            label.append("Twp")
                        elif wl == "boro":
                            label.append("Boro")
                        elif re.match(r"^\d+(ST|ND|RD|TH)$", w, re.I):
                            label.append(re.sub(r"(ST|ND|RD|TH)$", "", w, flags=re.I)
                                         + w[-2:].lower())
                        elif wl.upper() in ("I", "II"):
                            label.append(w.upper())
                        else:
                            label.append(w.title())
                    suffix = {"MAN": "Committeeman", "WOMAN": "Committeewoman",
                              "PERSON": "Committeeperson"}[loc.group(3)]
                    word = {"DEM": "Democratic", "REP": "Republican"}[loc.group(1)]
                    title = "%s %s" % (word, suffix)
                    district = " ".join(label)
                else:
                    title, district = name.title(), ""
            contest = (muni, title, district, party)
            sec(contest)
            continue
        if contest is None:
            continue

        # ---- contest rows
        s2 = sections[contest]
        tc = TOTAL_CAST_RE.match(st)
        if tc:
            vals = [_to_int(tc.group(i)) for i in (1, 3, 4, 5)]
            if s2["totals_cast"] not in (None, vals):
                warnings.append("differing Total Votes Cast %s: %s vs %s"
                                % (str(contest), s2["totals_cast"], vals))
            s2["totals_cast"] = vals
            continue
        ct = CONTEST_TOTALS_RE.match(st)
        if ct:
            s2["contest_totals"] = [_to_int(ct.group(i)) for i in (1, 2, 3, 4)]
            continue
        wi = WRITE_IN_TOTALS_RE.match(st)
        if wi:
            vals = [_to_int(wi.group(i)) for i in (1, 3, 4, 5)]
            if s2["writeins"] not in (None, vals):
                warnings.append("differing Write-In Totals %s: %s vs %s"
                                % (str(contest), s2["writeins"], vals))
            s2["writeins"] = vals
            continue
        if NOT_ASSIGNED_RE.match(st):
            nums = num_tokens(st)
            if len(nums) >= 4:
                vals = [_to_int(x) for x in nums[:4]]
                if s2["notassigned"] in (None, vals):
                    s2["notassigned"] = vals
                else:
                    s2["notassigned"] = [a + b for a, b in zip(s2["notassigned"], vals)]
            continue
        cd = CAND_RE.match(st)
        if cd:
            name = cd.group(1).strip()
            vals = [_to_int(cd.group(i)) for i in (2, 4, 5, 6)]
            existing = [i for i, (n, _, _) in enumerate(s2["cands"]) if n == name]
            if existing:
                # page-break continuation: same row repeated, or extend it
                i = existing[0]
                old = s2["cands"][i][1]
                if old != vals:
                    warnings.append("differing candidate %s %s: %s vs %s"
                                    % (str(contest), name, old, vals))
                    vals = [a + b for a, b in zip(old, vals)]
                s2["cands"][i] = (name, vals, None)
            else:
                s2["cands"].append((name, vals, vals))
            continue

    return sections, stats, order


def build_rows(sections, stats, order):
    rows = []

    for muni, st in stats.items():
        if "rv_total" in st:
            rows.append([muni, "Registered Voters", "", "", "", st["rv_total"],
                         "", "", ""])
        for p in ("democratic", "republican"):
            if "rv_" + p in st:
                rows.append([muni, "Registered Voters", "", p[:3].upper(), "",
                             st["rv_" + p], "", "", ""])
        if "bc_total" in st:
            b = st["bc_total"]
            rows.append([muni, "Ballots Cast", "", "", "", b[0], b[1], b[2], b[3]])
        for p in ("democratic", "republican"):
            if "bc_" + p in st:
                b = st["bc_" + p]
                rows.append([muni, "Ballots Cast", "", p[:3].upper(), "",
                             b[0], b[1], b[2], b[3]])
        if "bc_blank" in st:
            b = st["bc_blank"]
            rows.append([muni, "Ballots Cast - Blank", "", "", "", b[0], b[1], b[2], b[3]])

    for key in order:
        muni, title, district, party = key
        sec = sections[key]
        for name, vals in ((n, v) for n, v, _ in sec["cands"]):
            if name.lower().startswith("write-in:"):
                continue  # collapsed into Write Ins
            rows.append([muni, title, district, party, title_cand(name)] + vals)
        if sec["writeins"]:
            rows.append([muni, title, district, party, "Write Ins"] + sec["writeins"])
    return rows


# ------------------------------------------------------------------ recount

def parse_recount(txt_path):
    """Parse the May 27, 2022 recount PDF's per-municipality GOP U.S. Senate
    table.  Returns (blocks, leftover) where each block is
    {parts, ed, abs, prov, total: [10 ints]}; leftover is anything trailing.
    """
    with open(txt_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    blocks = []
    cur = {"parts": [], "ed": None, "abs": None, "prov": None, "total": None}

    def flush():
        if cur["total"] is not None:
            blocks.append(dict(cur))
        for k, v in (("parts", []), ("ed", None), ("abs", None),
                     ("prov", None), ("total", None)):
            cur[k] = v

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if ("May 17, 2022 General Primary" in line or "Reporting Group" in line
                or "Precinct Total Ballots" in line or "Total Ballots Counted" in s
                or "COUNTY OF MONTOUR" in s):
            continue
        if re.match(r"^Total (Election Day|Absentee|Provisional|Votes)", s):
            if re.match(r"^Total Votes\b", s):
                nums = num_tokens(s)
                if len(nums) == 10:
                    cur["total"] = [_to_int(x) for x in nums]
                    flush()
            continue

        if "Provisional" in line:
            col, key = cur["prov"], "prov"
        elif re.match(r"^Election Day\b", s):
            col, key = cur["ed"], "ed"
        else:
            col, key = None, None
        if key is not None:
            nums = num_tokens(line)
            if len(nums) >= 10:
                cur[key] = [_to_int(x) for x in nums[:10]]
            if "Provisional" in line:
                idx = line.index("Provisional")
                for w in line[:idx].split():
                    if w.upper() not in SKIP_WORDS and w != "/":
                        cur["parts"].append(w)
            continue

        if "Mail-In" in line:
            idx = line.index("Mail-In")
            for w in line[:idx].split():
                if w.upper() not in SKIP_WORDS and w != "/":
                    cur["parts"].append(w)
            continue

        if "Absentee" in line:
            idx = line.index("Absentee")
            for w in line[:idx].split():
                if w.upper() not in SKIP_WORDS and w != "/":
                    cur["parts"].append(w)
            nums = num_tokens(line)
            if len(nums) >= 10:
                cur["abs"] = [_to_int(x) for x in nums[:10]]
            continue

        nums = num_tokens(line)
        if len(nums) >= 10:
            # bare numbers row == the Absentee/Mail-In values, with the
            # municipality label possibly embedded before the first number
            cur["abs"] = [_to_int(x) for x in nums[:10]]
            head = re.split(r"\d[\d,]*", line)[0]
            for w in head.split():
                if w.upper() not in SKIP_WORDS and w != "/":
                    cur["parts"].append(w)
            continue

        # any other line: municipality-name fragment(s)
        if not re.search(r"[A-Za-z]", line) and nums:
            continue
        for w in line.split():
            if w.upper() not in SKIP_WORDS and w.upper() not in REC_HEADER_WORDS:
                cur["parts"].append(w)
    return blocks, cur


# --------------------------------------------------------------------- main

def main(argv):
    if len(argv) < 4:
        sys.exit("usage: parser <all_precincts> <recount> <output.csv>")
    ap_src = text_of_pdf(argv[1])
    rec_src = text_of_pdf(argv[2])
    out_csv = argv[3]

    warnings = []
    sections, stats, order = parse_all_precincts(ap_src, warnings)
    recount_blocks, trailing = parse_recount(rec_src)
    if trailing["total"] is not None or trailing["parts"]:
        warnings.append("unconsumed recount block: %r total=%s" % (trailing, "yes"))

    REC_SQUASH = {re.sub(r"[^A-Z]", "", k): v for k, v in REC_TO_AP.items()}
    rec_by_ap = {}
    for b in recount_blocks:
        name = " ".join(b["parts"])
        squashed = re.sub(r"[^A-Z]", "", name.upper())
        ap = REC_SQUASH.get(squashed)
        if not squashed:
            continue
        if ap is None:
            warnings.append("recount municipality not mapped: %r" % name)
            continue
        # sanity: components must sum to the Total Votes row
        for j, (ed, ab, pv, t) in enumerate(zip(b["ed"], b["abs"], b["prov"], b["total"])):
            if ed + ab + pv != t:
                warnings.append("%s recount %s: %s + %s + %s != %s"
                                % (ap, REC_CANDIDATES[j], ed, ab, pv, t))
        rec_by_ap[ap] = b

    missing = [m for m in MUNICIPALITIES if m in stats and m not in rec_by_ap]
    if missing:
        warnings.append("no recount data for: %s" % missing)

    # Replace GOP U.S. Senate sections with the post-recount values; log the
    # differences vs the pre-recount All Precincts values.
    office, party = "U.S. Senate", "REP"
    for muni in MUNICIPALITIES:
        key_ap = (muni, office, "", party)
        b = rec_by_ap.get(muni)
        if b is None:
            continue
        if key_ap in sections:
            ap_c = {title_cand(n): v for n, v, _ in sections[key_ap]["cands"]
                    if not n.lower().startswith("write-in:")}
            ap_wi = sections[key_ap]["writeins"][0] if sections[key_ap]["writeins"] else 0
            for i, cand in enumerate(REC_CANDIDATES[:7]):
                disp = title_cand(cand)
                if ap_c.get(disp, [0, 0, 0, 0])[0] != b["total"][i]:
                    print("RECOUNT DIFF %s REP U.S. Senate %s: precinct-report %s -> recount %s"
                          % (muni, cand, ap_c.get(disp, 0), b["total"][i]))
            if ap_wi != b["total"][7]:
                print("RECOUNT DIFF %s REP U.S. Senate Write-In: %s -> %s"
                      % (muni, ap_wi, b["total"][7]))
        candkey = ["Kathy Barnette", "Mehmet Oz", "George Bochetto", "Jeff Bartos",
                   "Dave McCormick", "Sean Gale", "Carla Sands", "Write Ins"]
        newsec = {"cands": [], "writeins": None, "notassigned": None,
                  "totals_cast": None, "contest_totals": None}
        for i, cand in enumerate(candkey):
            if cand == "Write Ins":
                newsec["writeins"] = [b["total"][7], b["ed"][7], b["abs"][7], b["prov"][7]]
            else:
                newsec["cands"].append((
                    title_cand(cand), [b["total"][i], b["ed"][i], b["abs"][i], b["prov"][i]], None))
        sections[key_ap] = newsec
        if key_ap not in order:
            order.append(key_ap)

    rows = build_rows(sections, stats, order)

    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes", "election_day", "mail", "provisional"])
        for r in rows:
            w.writerow([COUNTY] + r)

    sys.stderr.write("rows=%d  munis=%d  recount_munis=%d\n"
                     % (len(rows), len(stats), len(rec_by_ap)))
    if warnings:
        sys.stderr.write("WARNINGS:\n")
        for w in warnings:
            sys.stderr.write("  %s\n" % w)


if __name__ == "__main__":
    main(sys.argv)