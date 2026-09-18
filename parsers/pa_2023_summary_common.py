#!/usr/bin/env python3
"""Shared helpers for the 2023 PA general county-summary-only counties.

These counties have no usable precinct-level source; their official summary
report IS the results source.  Each county wrapper script in this series
(`parsers/pa_<county>_general_2023_results_parser.py`) extracts rows from the
pre-extracted pdftotext -layout text (or extracts it from the PDF) and writes
the county-level CSV directly:

    county,office,district,party,candidate,votes,election_day,mail,provisional

Two engine families are provided:

- parse_esr()      Electionware "Summary Results Report" county summary
                   (Butler, Cameron, Venango, Franklin, Northampton, Somerset)
- parse_esr2()     Dominion "Election Summary Report" county summary
                   (Luzerne, Clarion, Carbon, Pike)

plus small helpers used by the Perry (SOVC) and Cambria (certified winners
table) custom parsers.
"""

import csv
import re
import subprocess
import sys
import tempfile

FIELDNAMES = ["county", "office", "district", "party", "candidate",
              "votes", "election_day", "mail", "provisional"]

PARTIES = {"DEM", "REP", "D/R", "DEM/REP", "DAR", "NOP", "CON", "LIB", "LBR",
           "GRN", "IND", "CST", "FWD", "PRO", "SUS", "LIB/"}

# Canonical spellings for the statewide 2023 general candidates.
CANON_NAMES = {
    "DANIEL MCCAFFERY": "Daniel McCaffery",
    "CAROLYN CARLUCCIO": "Carolyn Carluccio",
    "JILL BECK": "Jill Beck",
    "TIMIKA LANE": "Timika Lane",
    "MARIA BATTISTA": "Maria Battista",
    "HARRY F SMAIL JR": "Harry F. Smail Jr.",
    "HARRY F. SMAIL JR.": "Harry F. Smail Jr.",
    "HARRY F SMAIL, JR": "Harry F. Smail Jr.",
    "HARRY F. SMAIL, JR.": "Harry F. Smail Jr.",
    "MATT WOLF": "Matt Wolf",
    "MEGAN MARTIN": "Megan Martin",
}

SMALL_WORDS = {"of", "and", "the", "for", "in", "on", "at", "to"}


def smart_title(s):
    """Title-case an ALL-CAPS name while keeping suffixes/romans readable."""
    out = []
    for w in s.split():
        lw = w.lower()
        core = w.strip(".,")
        if core.upper() in ("II", "III", "IV", "VI", "VII", "VIII"):
            out.append(core.upper() + w[len(core):])
        elif core.upper() in ("JR", "SR"):
            out.append(core.capitalize() + w[len(core):])
        elif lw.startswith("mc") and len(w) > 2:
            out.append("Mc" + lw[2:].capitalize())
        elif lw.startswith("o'") and len(w) > 2:
            out.append("O'" + lw[2:].capitalize())
        elif lw in SMALL_WORDS:
            out.append(lw)
        else:
            out.append(lw.capitalize())
    return " ".join(out)


def clean_name(name, titlecase=False):
    name = re.sub(r"\s+", " ", name).strip()
    if name.upper() in CANON_NAMES:
        return CANON_NAMES[name.upper()]
    if titlecase and re.search(r"[A-Za-z]{2,}", name):
        return smart_title(name)
    return name


def norm_party(p):
    p = p.upper().strip(".,")
    if p in ("DAR", "DEM/REP"):
        return "D/R"
    return p


def nums_from(tokens):
    """Return numeric tokens (commas stripped); drops percentage tokens."""
    out = []
    for t in tokens:
        t = t.replace(",", "").strip()
        if re.fullmatch(r"\d+\.\d+%", t):
            continue
        if re.fullmatch(r"\d+", t):
            out.append(t)
    return out


def _is_party_tok(t):
    u = t.upper().rstrip(".,")
    return u in PARTIES or u in ("DAR", "DEM/REP", "D/R")


def split_candidate(tokens):
    """Split a candidate row's tokens into (head, party, nums).

    Handles both layouts: "Name DEM 123" (trailing party) and
    "DEM Name 123" (leading party, Electionware ESR).
    """
    head = []
    for t in tokens:
        tt = t.replace(",", "")
        if re.fullmatch(r"\d+", tt) or re.fullmatch(r"\d+(?:\.\d+)?%", t):
            break
        head.append(t)
    party = ""
    if len(head) >= 2 and _is_party_tok(head[-1]):
        party = norm_party(head[-1])
        head = head[:-1]
    elif len(head) >= 2 and _is_party_tok(head[0]):
        party = norm_party(head[0])
        head = head[1:]
    rest = tokens[len(head) + (1 if party else 0):]
    return head, party, nums_from(rest)


def vals_from(nums):
    """(total, ed, mi, pr) from trailing numeric tokens.

    ESR layouts: 4 numbers -> total, ed, mi, pr ; 1 number -> total only.
    """
    if len(nums) >= 4:
        return nums[0], nums[1], nums[2], nums[3]
    if len(nums) == 1:
        return nums[0], "", "", ""
    if not nums:
        return "", "", "", ""
    return nums[0], "", "", ""


class Rows:
    def __init__(self, county):
        self.county = county
        self.rows = []
        self.contests = []
        self.totals = {}
        self.warnings = []

    @staticmethod
    def _v(v):
        return "" if v is None or v == "" else str(v)

    def add(self, office, district, party, candidate, total, ed="", mi="", pr=""):
        self.rows.append([self.county, self._v(office), self._v(district),
                          self._v(party), self._v(candidate), self._v(total),
                          self._v(ed), self._v(mi), self._v(pr)])

    def add_meta(self, office, total, ed="", mi="", pr=""):
        self.add(office, "", "", "", total, ed, mi, pr)


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(rows.rows)
    return len(rows.rows)


# --------------------------------------------------------------------------
# page furniture that must never be parsed as data
# --------------------------------------------------------------------------

PAGE_JUNK = re.compile(
    r"^(\s*(page[:\s]|report generated|summary results report|results report|"
    r"official results|county summary results|election summary report|"
    r"election summary|officer|official municipal|official county wide|"
    r"municipal election|general election|statistics|election day precincts|"
    r"precincts complete|precincts partially|absentee/ early|voter turnout"
    r"|election day prec))",
    re.IGNORECASE)

DATE_LINE = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\s+\d{1,2},?\s+\d{4}\b", re.IGNORECASE)


NUMERIC_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")


def is_junk(stripped):
    if DATE_LINE.search(stripped):
        return True
    if NUMERIC_DATE.search(stripped):
        return True
    if re.search(r"municipal election", stripped, re.I):
        return True
    if re.match(r"^county of ", stripped, re.I):
        return True
    if stripped.startswith("-") and re.match(r"^-\s*\d\d/", stripped):
        return True
    return bool(PAGE_JUNK.match(stripped))


# --------------------------------------------------------------------------
# Electionware "Summary Results Report" (ESR) county summary
# --------------------------------------------------------------------------

def parse_esr(text_path, county, map_contest, titlecase_names=False,
              has_over_under=True):
    """Parse an Electionware ESR county summary text into a Rows object."""
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    current = None
    i = 0
    rv = None
    bc = None
    seen_stats = False

    # pre-compute which lines are contest headers (the line above "Vote For N")
    header_lines = set()
    for k, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                header_lines.add(j)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        upper = stripped.upper()

        # -- STATISTICS block -------------------------------------------------
        if "STATISTICS" in upper and not seen_stats:
            seen_stats = True
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if re.match(r"^registered voters - total", s, re.I):
                    n = nums_from(s.split())
                    rv = int(n[0]) if n else 0
                elif re.match(r"^ballots cast - total", s, re.I):
                    bc = nums_from(s.split())
                elif re.match(r"^ballots cast - blank", s, re.I):
                    n = nums_from(s.split())
                    if n:
                        R.add_meta("Ballots Cast - Blank", n[0],
                                   n[1] if len(n) > 1 else "",
                                   n[2] if len(n) > 2 else "",
                                   n[3] if len(n) > 3 else "")
                elif re.match(r"^(justice|judge|county|district|magisterial|"
                              r"mayor|council|supervisor|auditor|school|tax|"
                              r"constable|clerk|register|recorder|treasurer|"
                              r"coroner|sheriff|high|controller|prothonotary)",
                              s, re.I):
                    break
                i += 1
            continue

        # -- contest header: line just above "Vote For N" ----------------------
        if re.match(r"^vote for \d+$", stripped, re.I):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                current = map_contest(lines[j].strip())
                R.contests.append(current)
            i += 1
            continue

        if i in header_lines:
            i += 1
            continue

        if is_junk(stripped):
            i += 1
            continue

        if current is None or not any(c.isdigit() for c in stripped):
            i += 1
            continue

        tokens = stripped.split()
        nums = nums_from(tokens[1:]) if tokens else []
        lead = tokens[0].upper().rstrip(":") if tokens else ""

        if stripped.upper().startswith("WRITE-IN TOTALS"):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", "Write-ins", total, ed, mi, pr)
        elif stripped.upper().startswith("WRITE-IN:"):
            pass
        elif re.match(r"^not assigned", stripped, re.I):
            pass
        elif re.match(r"^total votes cast", stripped, re.I):
            R.totals[current] = int(nums[0]) if nums else None
        elif re.match(r"^contest totals", stripped, re.I):
            pass
        elif re.match(r"^over ?votes", stripped, re.I) and has_over_under:
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", "Overvotes", total, ed, mi, pr)
        elif re.match(r"^under ?votes", stripped, re.I) and has_over_under:
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", "Undervotes", total, ed, mi, pr)
        elif re.match(r"^no candidate filed", stripped, re.I):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", "No Candidate Filed",
                  total, ed, mi, pr)
        elif lead in ("YES", "NO"):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", lead.capitalize(), total, ed, mi, pr)
        else:
            head, party, allnums = split_candidate(tokens)
            name = clean_name(" ".join(head), titlecase_names)
            if name and allnums:
                total, ed, mi, pr = vals_from(allnums)
                R.add(current[0], current[1], party, name, total, ed, mi, pr)
        i += 1

    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc:
        R.add_meta("Ballots Cast", bc[0],
                   bc[1] if len(bc) > 1 else "",
                   bc[2] if len(bc) > 2 else "",
                   bc[3] if len(bc) > 3 else "")
    return R


# --------------------------------------------------------------------------
# Dominion "Election Summary Report" (ESR2) county summary
# --------------------------------------------------------------------------

VOTEFOR = re.compile(r"\(vote for (\d+)\)", re.IGNORECASE)


def vals_esr2(nums):
    """(total, ed, mi, pr) for Dominion ESR2 rows: columns are
    Election Day, Mail-In, Provisional, Total."""
    if len(nums) >= 4:
        return nums[3], nums[0], nums[1], nums[2]
    if len(nums) == 1:
        return nums[0], "", "", ""
    if not nums:
        return "", "", "", ""
    return nums[-1], "", "", ""


def parse_esr2(text_path, county, map_contest, titlecase_names=False):
    """Parse a Dominion ESR2 county summary into a Rows object.

    Contest layout:
        <Header> (Vote for N)
        Precincts Reported: ...
        Times Cast ...
        Candidate ... Total
        <candidate rows> [Write-in aggregate row]
        Total Votes ...
        (optional write-in detail block: "Name WRITE-IN n..." / "Scattered WRITE-IN n...")
    """
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    text = "\n".join(lines)

    rv = bc = ed = mi = pr = None
    m = re.search(r"Registered Voters:\s*[\d,]+\s*of\s*([\d,]+)", text)
    if m:
        rv = int(m.group(1).replace(",", ""))
    m = re.search(r"^Ballots Cast:\s*([\d,]+)\s*$", text, re.M)
    if m:
        bc = int(m.group(1).replace(",", ""))
    if rv is not None:
        m = re.search(r"\bElection Day\s+([\d,]+)\s+[\d,]+", text)
        if m:
            ed = int(m.group(1).replace(",", ""))
        m = re.search(r"\bMail-In\s+([\d,]+)\s+[\d,]+", text)
        if m:
            mi = int(m.group(1).replace(",", ""))
        m = re.search(r"\bProvisional\s+([\d,]+)\s+[\d,]+", text)
        if m:
            pr = int(m.group(1).replace(",", ""))

    def flush_details():
        if current is not None and details[0]:
            s = details[1]
            if s != [0, 0, 0, 0]:
                R.add(current[0], current[1], "", "Write-ins",
                      s[3], s[0], s[1], s[2])
        details[0] = False
        details[1] = [0, 0, 0, 0]

    details = [False, [0, 0, 0, 0]]
    had_agg = [False]
    current = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        if VOTEFOR.search(stripped) \
                or re.search(r"\(vote(\s+for)?\s*$", stripped, re.I):
            flush_details()
            header = stripped
            # wrapped headers: "(Vote for" may be split across lines
            j = i
            while not VOTEFOR.search(header) and j + 1 < len(lines) \
                    and lines[j + 1].strip():
                j += 1
                header += " " + lines[j].strip()
                if re.search(r"\(vote for \d+\)", header, re.I):
                    break
            m = VOTEFOR.search(header)
            prefix = header[:m.start()].strip() if m else ""
            # a stub prefix (roman numeral / bare "(Vote for N)") continues
            # the previous non-blank line
            if prefix == "" or re.fullmatch(r"[IVXivx]+", prefix):
                k = i - 1
                while k >= 0 and not lines[k].strip():
                    k -= 1
                if k >= 0 and not re.search(r"\(vote\b", lines[k], re.I) \
                        and lines[k].strip() != "Precincts Reported:":
                    header = lines[k].strip() + " " + header
                    header = re.sub(r"\s+", " ", header)
            current = map_contest(header)
            R.contests.append(current)
            had_agg[0] = False
            i = j + 1
            continue

        if current is None or is_junk(stripped):
            i += 1
            continue

        low = stripped.lower()
        if low.startswith("precincts reported") or low.startswith("times cast") \
                or low.startswith("candidate") or low.startswith("elector group") \
                or low.startswith("counting group") \
                or low.startswith("registered voters:") \
                or low.startswith("ballots cast:"):
            i += 1
            continue

        if re.match(r"^total votes", stripped, re.I):
            flush_details()
            details[0] = True
            nums = nums_from(stripped.replace("WRITE-IN", " ").split())
            if nums:
                R.totals[current] = int(nums[3] if len(nums) >= 4 else nums[0])
            i += 1
            continue

        if re.search(r"write-in", stripped, re.I):
            # numbers come after the WRITE-IN token ("SCATTER 1 WRITE-IN 66 9 0 75")
            toks = stripped.split()
            try:
                widx = next(k for k, t in enumerate(toks)
                            if t.upper().rstrip(",") == "WRITE-IN")
            except StopIteration:
                widx = -1
            after = toks[widx + 1:] if widx >= 0 else toks
            nums = nums_from(after)
            is_scatter = bool(toks) and toks[0].upper().startswith("SCATTER")
            if details[0]:
                # detail block rows: accumulate only when the contest had no
                # aggregate "Write-in" row in its candidate block
                if not had_agg[0]:
                    if len(nums) >= 4:
                        details[1] = [a + int(b) for a, b in
                                      zip(details[1], nums[:4])]
                    elif len(nums) == 1:
                        details[1][3] += int(nums[0])
            else:
                had_agg[0] = True
                total, edv, miv, prv = vals_esr2(nums)
                R.add(current[0], current[1], "", "Write-ins",
                      total, edv, miv, prv)
            i += 1
            continue

        nums = nums_from(stripped.split())
        if not nums:
            i += 1
            continue

        lead = stripped.split()[0].upper()
        if lead == "WRITE-IN":
            total, edv, miv, prv = vals_esr2(nums)
            R.add(current[0], current[1], "", "Write-ins", total, edv, miv, prv)
        elif lead in ("YES", "NO"):
            total, edv, miv, prv = vals_esr2(nums)
            R.add(current[0], current[1], "", lead.capitalize(), total, edv, miv, prv)
        else:
            head, party, allnums = split_candidate(stripped.split())
            name = clean_name(" ".join(head), titlecase_names)
            if name and allnums:
                total, edv, miv, prv = vals_esr2(allnums)
                R.add(current[0], current[1], party, name, total, edv, miv, prv)
        i += 1

    flush_details()

    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc is not None:
        R.add_meta("Ballots Cast", bc,
                   ed if ed is not None else "",
                   mi if mi is not None else "",
                   pr if pr is not None else "")
    return R


# --------------------------------------------------------------------------
# misc helpers
# --------------------------------------------------------------------------

def text_from_pdf(pdf_path):
    """pdftotext -layout extraction to a temp file; returns the path."""
    out = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    out.close()
    subprocess.run(["pdftotext", "-layout", pdf_path, out.name],
                   check=True, capture_output=True, text=True)
    return out.name


def check_totals(R):
    """Internal consistency: candidate+write-in+over+under sums vs source totals."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in R.rows:
        key = (r[1], r[2])
        if r[4] in ("Overvotes", "Undervotes"):
            continue  # "Total Votes Cast" in the source excludes over/under
        try:
            agg[key] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is not None and agg.get(key, 0) != tot:
            bad.append((key, agg[key], tot))
    return bad


def run_cli(county, map_contest, engine, titlecase=False):
    """CLI contract: parser <input> <output> [--county NAME]."""
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt> <output.csv> [--county NAME]")
    src, dst = args[0], args[1]
    cty = county
    if "--county" in args:
        cty = args[args.index("--county") + 1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    if engine is parse_esr:
        R = parse_esr(src, cty, map_contest, titlecase_names=titlecase)
    else:
        R = parse_esr2(src, cty, map_contest, titlecase_names=titlecase)
    n = write_csv(dst, R)
    print(f"wrote {n} rows -> {dst}")
    print(f"contests parsed: {len(R.contests)}")
    bad = check_totals(R)
    if bad:
        for (office, dist), a, t in bad:
            print(f"WARNING {office} | {dist}: rows sum {a} != Total Votes Cast {t}")
    else:
        print("contest totals check: all contests reconcile")