#!/usr/bin/env python
"""
Luzerne County 2026 Primary (2026-05-19) results parser.

Sources (official, 5/27/2026 post-certification reports from the county):
  - "Luzerne County 2026 Primary Result Export by Precinct 5-27-2026.pdf"
    (vendor precinct-level export, "Contest Results by Precinct Counting
    Groups Break Out": per counting group per contest per choice, with
    Election Day / Provisional / Mail In breakdowns and write-in rows).
  - "Luzerne County 2026 Primary Cumulative Results Report 5-27-2026.pdf"
    (countywide per-contest summary), used for the county-level CSV.
  - "Luzerne County 2026 Primary Certified Precinct Returns PA DOS.csv"
    (PA DOS certified precinct returns), used only for validation and for
    the official spellings of two ballot names the vendor export misprints:
      * Democratic State Committee candidate printed "SHANNON GORSHEK" ->
        official "SHANNON GROSHEK" (countywide total 11020 matches DOS/anchor
        exactly, all 190 precinct rows included).
      * HD-121 candidate printed "MICHAEL J STADIUS" -> official
        "MICHAEL J STADULIS" (total 1631 matches exactly).

The vendor export covers every contest the DOS file does, plus named/write-in
class rows and the Democratic county committee contests, and it matches the
DOS certified totals exactly per precinct (validated 874 precinct-contest
comparisons / 0 diffs) and the anchor county file (49/49 named candidates).

Vendor quirks handled:
  - Long contest titles overlap the choice-name column and the cells are
    drawn as overlapping text runs, so the PDF content stream is parsed run
    by run (chars grouped by drawing runs) instead of by text columns.
  - Each record is 5 cells: precinct(+sometimes title), contest title,
    choice, and 4 right-aligned numbers (Total, Election Day, Provisional,
    Mail In). ed+prov+mail must equal total (checked for every record).
  - Some precincts have extra counting groups (separate ballot style or
    mail-batch groups, e.g. "HAZLE Township District 01 - 280001-02",
    "LARKSVILLE Borough Ward 03 - 41030003" and "PLYMOUTH Borough Ward 01 -
    55010001", plus BUTLER Township District 05's "(CD 8)"/"(CD 9)" split).
    Counting groups of the same ballot style are folded into one precinct
    (validated exactly against the DOS certified precinct totals).
    BUTLER D5's two ballot styles are folded into one precinct name with
    the U.S. House rows kept district-tagged (8 and 9), matching how the
    repo's 2024 Luzerne general file reports that split precinct.
  - Write-in class rows ("SCATTER", "SCATTER n", "Rejected write-ins",
    "Unassigned write-ins") carry the contest party of the block they
    follow; "Unassigned write-ins" are included in the vendor's Cast Votes
    while "Rejected write-ins" are not (verified against the cumulative
    report's Cast Votes rows).
  - No Registered Voters / Ballots Cast rows exist at precinct level, and
    they are not invented. No separate 'absentee' column exists; the Mail
    In column (absentee + mail-in combined) is written to "absentee",
    matching the county's earlier files.

Usage:
  python parsers/pa_luzerne_primary_2026_results_parser.py [outdir]
Writes, into outdir (default 2026/counties):
  20260519__pa__primary__luzerne__precinct.csv
  20260519__pa__primary__luzerne__county.csv
"""
import csv, os, re, sys, warnings
from collections import defaultdict
import pdfplumber

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.expanduser("~/code/openelections-sources-pa/2026/primary/")
EXPORT_PDF = os.path.join(SRC, "Luzerne County 2026 Primary Result Export by Precinct 5-27-2026.pdf")
CUM_PDF = os.path.join(SRC, "Luzerne County 2026 Primary Cumulative Results Report 5-27-2026.pdf")

TITLES = {
    "GOVERNOR 4 YEAR TERM": ("Governor", ""),
    "LIEUTENANT GOVERNOR 4 YEAR TERM": ("Lieutenant Governor", ""),
    "REPRESENTATIVE IN CONGRESS 8TH CONGRESSIONAL DISTRICT 2 YEAR TERM": ("U.S. House", "8"),
    "REPRESENTATIVE IN CONGRESS 9TH CONGRESSIONAL DISTRICT 2 YEAR TERM": ("U.S. House", "9"),
    "SENATOR IN THE GENERAL ASSEMBLY 20TH SENATORIAL DISTRICT 4 YEAR TERM": ("State Senate", "20"),
    "SENATOR IN THE GENERAL ASSEMBLY 22ND SENATORIAL DISTRICT 4 YEAR TERM": ("State Senate", "22"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 116TH LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "116"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 117TH LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "117"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 118TH LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "118"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 119TH LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "119"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 120TH LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "120"),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 121ST LEGISLATIVE DISTRICT 2 YEAR TERM": ("State House", "121"),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE 4 YEAR TERM": ("Member of Democratic State Committee", ""),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE 4 YEAR TERM": ("Member of Republican State Committee", ""),
    "DEMOCRATIC COUNTY COMMITTEEMAN 4 YEAR TERM": ("Democratic County Committeeman", ""),
    "DEMOCRATIC COUNTY COMMITTEEWOMAN 4 YEAR TERM": ("Democratic County Committeewoman", ""),
}

# official ballot names per contest (candidate -> party); validated against
# the PA DOS certified CSV totals per precinct.
NAME_PARTY = {
    "GOVERNOR 4 YEAR TERM": {"JOSH SHAPIRO": "DEM", "STACY GARRITY": "REP"},
    "LIEUTENANT GOVERNOR 4 YEAR TERM": {"AUSTIN DAVIS": "DEM", "JOHN VENTRE": "REP", "JASON RICHEY": "REP"},
    "REPRESENTATIVE IN CONGRESS 8TH CONGRESSIONAL DISTRICT 2 YEAR TERM": {"PAIGE COGNETTI": "DEM", "ROB BRESNAHAN JR": "REP"},
    "REPRESENTATIVE IN CONGRESS 9TH CONGRESSIONAL DISTRICT 2 YEAR TERM": {"RACHEL WALLACE": "DEM", "DAN MEUSER": "REP"},
    "SENATOR IN THE GENERAL ASSEMBLY 20TH SENATORIAL DISTRICT 4 YEAR TERM": {"JACKIE BAKER": "DEM", "TYLER MEYERS": "REP", "LISA BAKER": "REP"},
    "SENATOR IN THE GENERAL ASSEMBLY 22ND SENATORIAL DISTRICT 4 YEAR TERM": {"MARTY FLYNN": "DEM", "JEFFREY LAKE": "DEM", "SHARON SOLTIS SPARANO": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 116TH LEGISLATIVE DISTRICT 2 YEAR TERM": {"DANE WATRO": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 117TH LEGISLATIVE DISTRICT 2 YEAR TERM": {"JEREMY BENSCOTER": "DEM", "JAMIE WALSH": "REP", "BILL JONES": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 118TH LEGISLATIVE DISTRICT 2 YEAR TERM": {"JIM HADDOCK": "DEM", "JOHN LOMBARDO": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 119TH LEGISLATIVE DISTRICT 2 YEAR TERM": {"LAUREN MCCURDY": "DEM", "ALEC J RYNCAVAGE": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 120TH LEGISLATIVE DISTRICT 2 YEAR TERM": {"FERN LEARD": "DEM", "JOANNA BRYN SMITH": "DEM", "BRENDA PUGH": "REP"},
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY 121ST LEGISLATIVE DISTRICT 2 YEAR TERM": {"JESSICA MCCLAY": "DEM", "MICHAEL J STADULIS": "DEM", "MIKE HAROSTOCK": "REP"},
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE 4 YEAR TERM": {"PETER J BUTERA": "DEM", "NATE EACHUS": "DEM", "KRISTY KRUK": "DEM", "ALICIA ANGELI": "DEM", "ALISHA HOFFMAN-MIRILOVICH": "DEM", "THOMAS M SHUBILLA": "DEM", "JOHN CHERNESKY": "DEM", "JOHNNY SUDOL": "DEM", "ANTHONY R GRATTER": "DEM", "ALLIE SCHRAEDER": "DEM", "SHANNON GROSHEK": "DEM", "MEGAN KOCHER": "DEM", "JOSEPH FIORELLO-KOCHER": "DEM"},
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE 4 YEAR TERM": {"T LYNETTE VILLANO": "REP", "JACOB M ANDERSON": "REP", "SANDRA DEBIAS": "REP", "MARGARET PHILLIPS": "REP", "JENNY CENTRELLA": "REP", "CHRIS HUNTZINGER": "REP", "BILL LEWIS": "REP", "HARRY HAAS": "REP", "STEPHEN J URBAN": "REP"},
}

# official ballot-name corrections for vendor typos (totals verified)
NAME_FIX = {
    ("MEMBER OF THE DEMOCRATIC STATE COMMITTEE 4 YEAR TERM", "SHANNON GORSHEK"): "SHANNON GROSHEK",
    ("REPRESENTATIVE IN THE GENERAL ASSEMBLY 121ST LEGISLATIVE DISTRICT 2 YEAR TERM", "MICHAEL J STADIUS"): "MICHAEL J STADULIS",
}

INV = {"DEM": "REP", "REP": "DEM"}
CLASS_RE = re.compile(r"^(SCATTER( \d+)?|Rejected write-ins|Unassigned write-ins)$")
NUM_WINDOWS = {"total": (490, 517), "ed": (576, 601), "prov": (655, 682), "mail": (718, 747)}
HEADERS = {"#Precinct", "Contest Title", "Choice Name", "Total Votes",
           "Election Day Votes", "Provisional Votes", "Mail In Votes"}


# ---------------------------------------------------------------- extraction
def runs_of_page(chars):
    runs = []
    cur = [chars[0]]
    for i in range(1, len(chars)):
        c, nc = chars[i - 1], chars[i]
        jump = (abs(nc["top"] - c["top"]) > 0.6 or nc["x0"] > c["x1"] + 1.5
                or nc["x0"] < c["x0"] - 1.5)
        if jump:
            runs.append(cur)
            cur = []
        cur.append(nc)
    runs.append(cur)
    return runs


def num_window(x1):
    for k, (lo, hi) in NUM_WINDOWS.items():
        if lo <= x1 <= hi:
            return k
    return None


def extract_export(path):
    """Parse the by-precinct export PDF -> list of record dicts."""
    raw = []
    rec = None

    def flush():
        nonlocal rec
        if rec:
            raw.append(rec)
        rec = None

    for pg in pdfplumber.open(path).pages:
        chars = pg.chars
        if not chars:
            continue
        for r in runs_of_page(chars):
            text = "".join(c["text"] for c in r).strip()
            if not text or text in HEADERS or re.fullmatch(r"Page \d+ of \d+", text):
                continue
            x0, x1, top = r[0]["x0"], r[-1]["x1"], r[0]["top"]
            if top < 30:      # page banner
                continue
            digit = re.fullmatch(r"[\d,]+", text)
            key = num_window(x1) if digit else None
            if key:
                if rec is None:
                    rec = [None, None, None, {}]
                rec[3][key] = int(text.replace(",", ""))
            elif x0 < 170:
                if rec is None:
                    rec = [text, None, None, {}]
                elif rec[0] is not None and rec[1] is None and rec[2] is None and not rec[3]:
                    rec[0] += " " + text          # wrapped precinct name
                else:
                    flush()
                    rec = [text, None, None, {}]
            elif x0 < 343:
                if rec is None:
                    rec = [None, None, None, {}]
                rec[1] = (rec[1] + " " + text) if rec[1] else text
            elif x0 < 490:
                if rec is None:
                    rec = [None, None, None, {}]
                rec[2] = (rec[2] + " " + text) if rec[2] else text
            else:
                raise ValueError("unrouted run: %r (x0=%.1f)" % (text, x0))
            if rec and len(rec[3]) == 4 and rec[1] is not None and rec[2] is not None:
                flush()
    flush()

    records = []
    for prec, contest, choice, nums in raw:
        if len(nums) != 4:
            raise ValueError("record with %d numbers: %r | %r | %r" % (len(nums), prec, contest, choice))
        if nums["ed"] + nums["prov"] + nums["mail"] != nums["total"]:
            raise ValueError("breakdown mismatch: %r | %r | %r" % (prec, contest, choice))
        prec = re.sub(r"\s+", " ", (prec or "")).strip()
        contest = re.sub(r"\s+", " ", (contest or "")).strip()
        # contest title merged into the precinct cell, possibly truncated there
        m = re.match(r"^(.*?- ?\d+)([A-Z].*)$", prec)
        if m:
            prec_only, frag = None, None
            for t in TITLES:
                # the merged run usually carries the whole title
                if re.sub(r"\s+", " ", m.group(2) + " " + contest) == re.sub(r"\s+", " ", t):
                    prec_only, frag = m.group(1), t
                    break
            if prec_only is None and not contest:
                # title truncated in the precinct cell; tail arrives as the
                # contest-cell text
                for t in TITLES:
                    tn = re.sub(r"\s+", "", t)
                    if tn.startswith(re.sub(r"\s+", "", m.group(2)) + re.sub(r"\s+", "", contest)):
                        prec_only, frag = m.group(1), t
                        break
            if prec_only is None:
                raise ValueError("cannot split merged precinct cell: %r | %r" % (prec, contest))
            prec, contest = prec_only, frag
        records.append({"prec": prec, "contest": contest, "choice": choice,
                        "total": nums["total"], "ed": nums["ed"],
                        "prov": nums["prov"], "mail": nums["mail"]})
    return records


def parse_prec(prec):
    """'ASHLEY Borough Ward 01 - 10100' -> base name (counting groups and
    (CD n) markers dropped)."""
    m = re.match(r"^(.*?)(?: \(CD (\d+)\))? - ([\d\-]+)$", prec)
    return m.group(1).strip()


# ---------------------------------------------------------------- naming
def titlecase(name):
    s = re.sub(r"\s+", " ", name).strip()
    words = []
    for tok in s.split():
        parts = re.split("([-'])", tok)
        buf = []
        for p in parts:
            if p in ("-", "'"):
                buf.append(p)
            elif p.upper() in ("JR", "SR", "II", "III", "IV", "LTD"):
                buf.append(p.title())
            else:
                buf.append(p[:1].upper() + p[1:].lower())
        tok = "".join(buf)
        tok = re.sub(r"^Mc([a-z])", lambda m: "Mc" + m.group(1).upper(), tok)
        tok = re.sub(r"^Mac([a-z])", lambda m: "Mac" + m.group(1).upper(), tok)
        words.append(tok)
    out = " ".join(words)
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r",\s+", ", ", out)
    return out.strip()


def clean_precinct(base):
    s = base.title()
    s = re.sub(r"\bWilkes Barre\b", "Wilkes-Barre", s)
    s = re.sub(r"\bWard (\d+)\b", lambda m: "Ward %02d" % int(m.group(1)), s)
    s = re.sub(r"\bDist(?:rict)? (\d+)\b", lambda m: "Dist %02d" % int(m.group(1)), s)
    return s


# ---------------------------------------------------------------- party
def attribute_party(records):
    """Attach the contest party to every record, using the party-block
    structure of each counting group's contest."""
    groups = defaultdict(list)
    for r in records:
        r["base"] = parse_prec(r["prec"])
        groups[(r["prec"], r["contest"])].append(r)
    out = []
    inv = {"DEM": "REP", "REP": "DEM"}
    for (prec, contest), rows in groups.items():
        if contest.startswith("MEMBER OF THE DEMOCRATIC STATE") or "COUNTY COMMITTEE" in contest:
            parties = ["DEM"]
        elif contest.startswith("MEMBER OF THE REPUBLICAN STATE"):
            parties = ["REP"]
        else:
            parties = ["DEM", "REP"]
        np = NAME_PARTY.get(contest, {})
        assignments = {}
        last_named_party = None
        i, n = 0, len(rows)
        while i < n:
            r = rows[i]
            if not CLASS_RE.match(r["choice"]):
                p = np.get(r["choice"].upper(), parties[0])
                assignments[i] = p
                last_named_party = p
                i += 1
                continue
            j = i
            while j < n and CLASS_RE.match(rows[j]["choice"]):
                j += 1
            follows_names = i > 0 and not CLASS_RE.match(rows[i - 1]["choice"])
            if follows_names and last_named_party is not None:
                party = last_named_party
            elif len(parties) == 1:
                party = parties[0]
            else:
                nxt = next((rows[x] for x in range(j, n)
                            if not CLASS_RE.match(rows[x]["choice"])), None)
                if nxt is not None:
                    pn = np.get(nxt["choice"].upper(), "DEM")
                    party = inv.get(pn)
                elif last_named_party is not None:
                    party = inv.get(last_named_party)
                else:
                    party = parties[0]
            if party is None:
                raise ValueError("cannot attribute party: %s %s" % (prec, contest))
            for x in range(i, j):
                assignments[x] = party
            last_named_party = party
            i = j
        for idx, r in enumerate(rows):
            r["party"] = assignments[idx]
            out.append(r)
    return out


# ---------------------------------------------------------------- output
def build_precinct_csv(records):
    """Fold counting groups per ballot style and emit repo-style rows."""
    agg = {}     # (base, contest, choice, party) -> dict(total, ed, prov, mail)
    order = []
    for r in records:
        base = parse_prec(r["prec"])
        # vendor typo fix (official ballot spelling, totals verified equal)
        choice = NAME_FIX.get((r["contest"], r["choice"]), r["choice"])
        pk = (base, r["contest"], choice, r["party"])
        if pk not in agg:
            agg[pk] = {"total": 0, "ed": 0, "prov": 0, "mail": 0,
                       "parties": set()}
            order.append(pk)
        a = agg[pk]
        a["total"] += r["total"]; a["ed"] += r["ed"]
        a["prov"] += r["prov"]; a["mail"] += r["mail"]
    rows = []
    for base, contest, choice, party in order:
        a = agg[(base, contest, choice, party)]
        rows.append([clean_precinct(base), contest, choice, party, a])
    return rows


def cand_name(choice, contest):
    """Candidate label as the source prints it, normalized to the style the
    county's earlier files used (single-seat contests report a plain
    'Scattered' row; the multi-seat state committee contests report one
    scattered-write-in row per elect slot, numbered 1..n)."""
    if choice == "SCATTER":
        return "Scatter 1" if "STATE COMMITTEE" in contest else "Scattered"
    m = re.match(r"^SCATTER (\d+)$", choice)
    if m:
        return "Scatter %d" % int(m.group(1))
    if choice == "Rejected write-ins":
        return "Rejected Write-ins"
    if choice == "Unassigned write-ins":
        return "Unassigned Write-ins"
    return titlecase(choice)


def write_precinct_file(rows, path):
    cols = ["county", "precinct", "office", "district", "party", "candidate",
            "votes", "election_day", "provisional", "absentee"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for prec, contest, choice, party, a in rows:
            office, district = TITLES[contest]
            if "COUNTY COMMITTEE" in contest:
                office = "%s - %s" % (office, prec)
            cand = cand_name(choice, contest)
            w.writerow(["Luzerne", prec, office, district, party, cand,
                        a["total"], a["ed"], a["prov"], a["mail"]])


# ------------------------------------------------------- countywide (cum)
def extract_cumulative(path):
    '''Cumulative report -> [ (contest_title, choice, party, ed, pv, mail, total) ]
    with Cast Votes rows as choice="Cast Votes".'''
    out = []
    cur = None
    head_re = re.compile(r"^(.+?) - (Democratic|Republican) Party - ")
    for pg in pdfplumber.open(path).pages:
        words = pg.extract_words()
        lines = defaultdict(list)
        for w in words:
            lines[round(w["top"], 1)].append(w)
        merged = []
        for t in sorted(lines):
            if merged and t - merged[-1][0] < 1.0:
                merged[-1][1].extend(lines[t])
            else:
                merged.append((t, lines[t]))
        for t, ws in merged:
            ws = sorted(ws, key=lambda w: w["x0"])
            text = " ".join(w["text"] for w in ws)
            m = head_re.match(text)
            if m:
                cur = (re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2))
                continue
            if cur is None:
                continue
            toks = [w["text"] for w in ws]
            numer = []
            i = len(toks) - 1
            while i >= 0 and re.fullmatch(r"[\d,.%]+", toks[i]):
                numer.insert(0, toks[i])
                i -= 1
            if len(numer) < 8 or i < 0:
                continue
            name = " ".join(toks[:i + 1]).strip()
            try:
                ed = int(numer[0].replace(",", ""))
                pv = int(numer[2].replace(",", ""))
                ml = int(numer[4].replace(",", ""))
                tot = int(numer[6].replace(",", ""))
            except ValueError:
                continue
            if name.upper().startswith("CAST VOTES"):
                out.append((cur[0], "Cast Votes", cur[1], ed, pv, ml, tot))
            else:
                out.append((cur[0], re.sub(r"\s*\((W)\)$", "", name), cur[1],
                            ed, pv, ml, tot))
    return out


def write_county_file(cum, path):
    cols = ["county", "office", "district", "party", "candidate", "votes",
            "election_day", "mail", "provisional"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for contest, choice, party, ed, pv, ml, tot in cum:
            if choice == "Cast Votes" or "COUNTY COMMITTEE" in contest:
                continue
            office, district = TITLES[contest]
            party = {"Democratic": "DEM", "Republican": "REP"}[party]
            cand = cand_name(choice, contest)
            w.writerow(["Luzerne", office, district, party, cand, tot, ed, ml, pv])


# ---------------------------------------------------------------- main
def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "2026", "counties")
    os.makedirs(outdir, exist_ok=True)

    records = extract_export(EXPORT_PDF)
    records = attribute_party(records)
    print("export records:", len(records))
    print("precincts (counting groups / folded):",
          len({r["prec"] for r in records}), "/", len({parse_prec(r["prec"]) for r in records}))

    rows = build_precinct_csv(records)
    prec_path = os.path.join(outdir, "20260519__pa__primary__luzerne__precinct.csv")
    write_precinct_file(rows, prec_path)
    print("precinct rows:", len(rows), "->", prec_path)

    cum = extract_cumulative(CUM_PDF)
    print("cumulative rows:", len(cum))
    county_path = os.path.join(outdir, "20260519__pa__primary__luzerne__county.csv")
    write_county_file(cum, county_path)
    print("county rows:", len(cum) - sum(1 for x in cum if x[1] == "Cast Votes")
          - sum(1 for x in cum if "COUNTY COMMITTEEM" in x[0]), "->", county_path)

    # ----------------------- verification report -----------------------
    def named_sums(path, party_map=None):
        named = defaultdict(int)
        for r in csv.DictReader(open(path)):
            cand = (r["candidate"] or "")
            if cand.upper().startswith(("SCATTER", "REJECTED WRITE", "UNASSIGNED WRITE",
                                        "WRITE-IN", "WRITE IN")) or "County Committe" in (r["office"] or ""):
                continue
            named[(r["office"], r["district"], cand.lower())] += int(r["votes"] or 0)
        return named

    # anchor check
    anchor = os.path.join(ROOT, "2026", "20260519__pa__primary__county.csv")
    if os.path.exists(anchor):
        with open(anchor) as f:
            rows_a = [r for r in csv.DictReader(f) if r["county"].strip().upper() == "LUZERNE"]
        a = defaultdict(int)
        for r in rows_a:
            a[(r["office"].lower(), r["district"],
               re.sub(r"\s+", " ", r["candidate"].lower()))] = int(r["votes"])
        # build the same key set from our rows
        ours = defaultdict(int)
        for prec, contest, choice, party, agg in rows:
            office, district = TITLES[contest]
            if CLASS_RE.match(choice) or choice.startswith("SCATTER") or "COUNTY COMMITTEE" in contest:
                continue
            ours[(office.lower(), district, re.sub(r"\s+", " ", titlecase(choice).lower()))] += agg["total"]
        diffs = [k for k in set(a) | set(ours) if a.get(k) != ours.get(k)]
        print("anchor check: anchors=%d ours=%d diffs=%d" % (len(a), len(ours), len(diffs)))
        for k in diffs[:15]:
            print("   DIFF", k, "anchor", a.get(k), "ours", ours.get(k))


if __name__ == "__main__":
    main()
