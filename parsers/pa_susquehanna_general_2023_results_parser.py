#!/usr/bin/env python3
"""
Parse Susquehanna County PA 2023 General (Municipal) Election results.

Source: "Susquehanna County Certified Official Results 2023 General.pdf"
(380 pages, SCANNED images with no text layer).  The PDF must be OCRed
before this parser runs; the expected input is a directory of tesseract TSV
files (one per page), produced with:

    pdftoppm -r 200 -png "<pdf>" page
    ls page-*.png | xargs -P 4 -I{} sh -c \\
        'b=$(basename {} .png); tesseract {} $b --psm 6 tsv'

Each page of the source is a Dominion "Precinct Summary Report" for one
election district, laid out as TWO side-by-side contest boxes per page.
Every box carries: contest header [("(Continued)")], "Vote For n",
"Total Votes N", candidate rows "<NAME> (<PARTY>) N P%", named write-in rows
"<NAME> (W) N P%", an aggregate "Write-in N P%" row, "Undervote N" and
"Overvote N" rows.  Contest boxes continue onto following pages with
"(Continued)" headers (long write-in lists).

Layout handling:
  * words are grouped into tesseract lines, then split into a LEFT and a
    RIGHT column stream at the horizontal midline (x < width/2); the two
    columns are parsed as independent contest streams;
  * the "Registered Voters N - Total Ballots M : P% - Blank Ballots B" line
    spans the page and is treated as full-width metadata;
  * a new precinct starts when the printed "Page n/m" footer number
    restarts.

Output rows follow the repo schema
(county,precinct,office,district,party,candidate,votes,election_day,mail,provisional);
the source reports totals only, so the breakdown columns stay empty.
Named write-in rows are NOT emitted individually -- they are summed into the
contest's aggregate "Write-in" row (candidate "Write-ins"), and the parser
verifies named-write-in sum == aggregate as an internal check.  "Undervote"
/ "Overvote" rows are emitted as candidate "Undervotes"/"Overvotes".

Validation (internal): for every contest instance, candidates + aggregate
Write-ins must equal the printed "Total Votes"; each percentage is
cross-checked against votes/Total Votes.  Failures are reported on stderr
as WARNING lines naming the page, so the flagged page images can be
inspected by eye.

Usage:
    python parsers/pa_susquehanna_general_2023_results_parser.py \\
        <tsv_dir> <output.csv> [--county Susquehanna]
"""

import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict

COUNTY = "Susquehanna"

FIELDNAMES = ["county", "precinct", "office", "district", "party",
              "candidate", "votes", "election_day", "mail", "provisional"]

PAGE_WIDTH = 1691  # 200dpi render width of the source pages
MID = PAGE_WIDTH / 2

# --------------------------------------------------------------------------
# contest header recognition
# --------------------------------------------------------------------------

COUNTY_OFFICES = {
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "DISTRICT ATTORNEY": ("District Attorney", ""),
    "COUNTY COMMISSIONER": ("County Commissioner", ""),
    "COUNTY TREASURER": ("County Treasurer", ""),
    "COUNTY AUDITOR": ("County Auditor", ""),
    "COUNTY CORONER": ("Coroner", ""),
    "REGISTER OF WILLS RECORDER OF DEEDS CLERK OF ORPHANS COURT":
        ("Register of Wills, Recorder of Deeds, & Clerk of Orphans Court", ""),
}

MDJ_RE = re.compile(r"MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d{2})")
# "SUPERIOR" is sometimes OCR-mangled ("PPERIOR"); COURT + the surname is
# distinctive enough (no other header pairs COURT with PANELLA/STABILE)
RETENTION_RE = re.compile(r"COURT(?:\s+RETENTION)?\s*-?\s*(PANELLA|STABILE)")
LOCAL_RE = re.compile(
    r"^(.*?)\s+(TOWNSHIP SUPERVISOR|TOWNSHIP AUDITOR|TOWNSHIP CONSTABLE|"
    r"BOROUGH COUNCILMAN|BOROUGH CONSTABLE|BOROUGH AUDITOR|TAX COLLECTOR)"
    r"(?:\s*(\d)\s*YEAR(?:\s*TERM)?)?$")
SCHOOL_RE = re.compile(r"^(.*?) SCHOOL (?:REGION )?(\d[A-B]?)$")


def norm_header(s):
    """Normalize an OCR'd contest header for matching."""
    s = s.upper()
    s = re.sub(r"\(CONTINUED\)", " ", s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bTOWNSH?I?P?\b", "TOWNSHIP", s)
    s = re.sub(r"\bAUDI[A-Z]{1,2}R\b", "AUDITOR", s)
    return s


def map_header(raw):
    """Map a raw (possibly two-line) header string to (office, district)."""
    h = norm_header(raw)
    if h in COUNTY_OFFICES:
        return COUNTY_OFFICES[h]
    # OCR-mangled wrapped forms
    if re.search(r"(REGISTER|GISTER) OF WILLS", h):
        return COUNTY_OFFICES[
            "REGISTER OF WILLS RECORDER OF DEEDS CLERK OF ORPHANS COURT"]
    if re.search(r"(JU)?DGE OF THE SUPERIOR COURT", h):
        return ("Judge of the Superior Court", "")
    if re.search(r"(JU)?DGE OF THE COMMONWEALTH COURT", h):
        return ("Judge of the Commonwealth Court", "")
    # MDJ on the raw text: norm_header strips the dashes from "34-3-01"
    m = MDJ_RE.search(raw.upper())
    if m:
        return ("Magisterial District Judge", m.group(1))
    m = RETENTION_RE.search(h)
    if m:
        return ("Superior Court Retention - "
                + ("Jack Panella" if m.group(1) == "PANELLA"
                   else "Victor P. Stabile"), "")
    m = SCHOOL_RE.match(h)
    if m:
        dist = m.group(1).strip()
        return (f"School Director Region {m.group(2)}", dist.title())
    m = re.match(r"^(.*?) SCHOOL (\d)[A-C]?$", h)
    if m:
        return (f"School Director Region {m.group(2)}", m.group(1).title())
    # ward-suffixed borough council: "MONTROSE BOROUGH-1W COUNCILMAN" /
    # "MONTROSE BORO-2W COUNCILMAN" (Montrose Borough elects its council by
    # ward; every other borough prints "<BORO> BOROUGH COUNCILMAN", which
    # LOCAL_RE below handles).  norm_header has already stripped the dash,
    # so the ward digit sits between BOROUGH and COUNCILMAN.
    m = re.match(r"^(.*?)\s*BORO(?:UGH)?\s*(\d)\s*W\s+COUNCILMAN$", h)
    if m:
        boro = m.group(1).strip()
        if boro.endswith("BOROUGH"):
            boro = boro[:-len("BOROUGH")].strip()
        return ("Borough Council", f"{boro.title()} Ward {m.group(2)}")
    m = LOCAL_RE.match(h)
    if m:
        dist = m.group(1).strip()
        kind = m.group(2)
        term = m.group(3)
        suffix = f" ({term} Year)" if term else ""
        office = {"TOWNSHIP SUPERVISOR": "Township Supervisor",
                  "TOWNSHIP AUDITOR": "Township Auditor",
                  "TOWNSHIP CONSTABLE": "Township Constable",
                  "BOROUGH COUNCILMAN": "Borough Council",
                  "BOROUGH CONSTABLE": "Borough Constable",
                  "BOROUGH AUDITOR": "Borough Auditor",
                  "TAX COLLECTOR": "Tax Collector"}[kind]
        return (office + suffix, dist.title())
    return None


def is_header_ish(s):
    """Cheap test: mostly-uppercase line with no vote numbers.

    Contest headers never carry percentages, "(W)" or "(PARTY)" tokens, or
    more than one bare number (write-in/candidate rows do) -- except the
    MDJ headers ("MAGISTERIAL DISTRICT JUDGE 34-3-01").
    """
    t = s.strip("| ").strip()
    if len(t) < 8:
        return False
    if "%" in t or re.search(r"\(\s*[A-Z]{1,3}\s*/?\s*[A-Z]{0,3}\s*\)", t):
        return False
    if len(re.findall(r"\d+", t)) > 1 and not MDJ_RE.search(t):
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return False
    return True


# fuzzy keyword classes for short summary rows (Undervote / Overvote /
# Write-in), robust to OCR mangling like "dervote", "overvets", "\rite-in"
FUZZY_TARGETS = {"undervote": "under", "overvote": "over", "writein": "wi",
                 "writeln": "wi", "writein": "wi"}


def label_row_class(name):
    """Classify a mangled label as 'total' / 'yes' / 'no' row, else None.

    Catches OCR variants like "{otal Votes", "Lotal Votes" (box totals that
    clustered as candidate rows) and "No Or" / "No vAs" (the NO row of a
    retention question).  Short names only, so real candidates cannot hit.
    """
    import difflib
    letters = re.sub(r"[^A-Za-z]", "", name).lower()
    if not letters:
        return None
    if len(letters) <= 10 and difflib.SequenceMatcher(
            None, letters, "totalvotes").ratio() >= 0.8:
        return "total"
    if len(letters) <= 5:
        if difflib.SequenceMatcher(None, letters, "yes").ratio() >= 0.6 \
                or letters.startswith("ye"):
            return "yes"
        if difflib.SequenceMatcher(None, letters, "no").ratio() >= 0.6 \
                or letters.startswith("no"):
            return "no"
    return None


def fuzzy_row_class(s):
    toks = [t for t in s.split() if not re.search(r"[\d%]", t)]
    if not toks or len(s) > 25:
        return None
    words = re.sub(r"[^A-Za-z]", "", " ".join(toks)).lower()
    if not words:
        return None
    import difflib
    best, ratio = None, 0.0
    for target, cls in FUZZY_TARGETS.items():
        r = difflib.SequenceMatcher(None, words, target).ratio()
        if r > ratio:
            best, ratio = cls, r
    if ratio < 0.6 and "%" not in s and len(re.findall(r"\d+", s)) == 1:
        # heavy label mangling ("Wadearats 107" for "Undervote 107"): the
        # Undervote/Overvote rows never carry a percentage, unlike candidate
        # rows, so a vowel-stripped comparison is safe here
        skel = re.sub(r"[aeiou]", "", words)
        for target, cls in (("undervote", "under"), ("overvote", "over")):
            r = difflib.SequenceMatcher(
                None, skel, re.sub(r"[aeiou]", "", target)).ratio()
            if r > ratio:
                best, ratio = cls, r
    # substring catches truncated forms like "dervote"; "rvote" alone would
    # also match "overvote", so require the undervote 'd' (an Overvote row
    # must not be reclassified as Undervote -- it would zero the real one)
    if "dervote" in words or ("rvote" in words and "overvote" not in words):
        return "under"
    return best if ratio >= 0.6 else None


# --------------------------------------------------------------------------
# tsv -> lines -> columns
# --------------------------------------------------------------------------

def read_tsv(path):
    """Read one tesseract TSV file into visual lines.

    tesseract's own line ids are unreliable here (it sometimes folds a whole
    column of rows into a single line id), so lines are rebuilt by clustering
    words on their vertical center (row pitch is ~25px at 200dpi).
    """
    words = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # split manually, NOT csv.reader: OCR'd quote characters (e.g. a
            # stray '"' inch-mark token) put the csv module into quoted mode
            # and silently swallow every following line until the next quote
            r = line.rstrip("\n").split("\t")
            if len(r) < 12 or r[0] != "5":
                continue
            text = r[11].strip()
            if not text or not re.search(r"[A-Za-z0-9]", text):
                continue  # box borders, dashes, punctuation-only tokens
            if len(r) > 12 or "\t" in text:
                continue  # malformed TSV row leaking raw data
            x, y, w, h = int(r[6]), int(r[7]), int(r[8]), int(r[9])
            words.append((x, y, w, h, text))
    words.sort(key=lambda t: (t[1] + t[3] / 2, t[0]))
    rows = []
    for wd in words:
        yc = wd[1] + wd[3] / 2
        if rows and abs(yc - rows[-1]["yc"]) <= 12:
            rows[-1]["words"].append(wd)
            n = len(rows[-1]["words"])
            rows[-1]["yc"] += (yc - rows[-1]["yc"]) / n
        else:
            rows.append({"yc": yc, "words": [wd]})
    out = []
    for r in rows:
        ws = sorted(r["words"], key=lambda t: t[0])
        out.append({"y": int(r["yc"]),
                    "x0": min(w[0] for w in ws),
                    "x1": max(w[0] + w[2] for w in ws),
                    "text": " ".join(w[4] for w in ws),
                    "words": ws})
    return out


PAGE_MARK = re.compile(r"Page\s*(\d+)\s*/\s*(\d+)", re.IGNORECASE)
META_RE = re.compile(r"Registered\s*Voters\s*([\d,]+)", re.IGNORECASE)
BALLOTS_RE = re.compile(r"Total\s*Ballots\s*([\d,]+)", re.IGNORECASE)
BLANK_RE = re.compile(r"Blank\s*Ballots\s*([\d,]+)", re.IGNORECASE)


def page_split(lines):
    """Split a page's lines into (meta, left, right) line lists."""
    meta, left, right = [], [], []
    for ln in lines:
        if re.search(r"Total\s*Ballots", ln["text"], re.IGNORECASE):
            meta.append(ln)
            continue
        lw = [w for w in ln["words"] if (w[0] + w[2] / 2) < MID]
        rw = [w for w in ln["words"] if (w[0] + w[2] / 2) >= MID]
        if lw:
            left.append({**ln, "words": lw,
                         "text": " ".join(w[4] for w in lw)})
        if rw:
            right.append({**ln, "words": rw,
                          "text": " ".join(w[4] for w in rw)})
    return meta, left, right


def page_meta(lines):
    """(registered_voters, ballots_cast, blank) from a page's meta line."""
    text = " ".join(ln["text"] for ln in lines
                    if re.search(r"Total\s*Ballots", ln["text"],
                                 re.IGNORECASE))
    rv = BAL = BL = None
    m = re.search(r"Voters\s*:?\s*([\d,]+)", text, re.IGNORECASE)
    if m:
        rv = int(m.group(1).replace(",", ""))
    m = BALLOTS_RE.search(text)
    if m:
        BAL = int(m.group(1).replace(",", ""))
    m = BLANK_RE.search(text)
    if m:
        BL = int(m.group(1).replace(",", ""))
    return rv, BAL, BL


# --------------------------------------------------------------------------
# stream parsing
# --------------------------------------------------------------------------

CONTINUED_RE = re.compile(r"^\(?\s*C[o0][nñ]ti?[nml]ue?d?\s*\)?$", re.IGNORECASE)
VOTEFOR_RE = re.compile(r"[VA][o0]?t?e?\s*For\s*:?\s*(\d+)", re.IGNORECASE)
TOTALVOTES_RE = re.compile(r"To[a-z]+l\s*[VUW]o[a-z]{1,4}\s*:?\s*(\S{0,10})",
                           re.IGNORECASE)
WRITEIN_AGG_RE = re.compile(
    r"^[WVV][rR]?i?t?e?[- ]?[ií1l]?[nñ]\s*:?\s+(\d[\d,)]*)", re.IGNORECASE)
UNDER_RE = re.compile(r"Und[eé][a-z]{1,6}\s*:?\s*(\d[\d,)]*)", re.IGNORECASE)
OVER_RE = re.compile(r"Ov[eé][a-z]{1,6}\s*:?\s*(\d[\d,)]*)", re.IGNORECASE)
PCT = re.compile(r"\d+\.\d+\s*%")
# box furniture that must never become a candidate row
JUNK_NAME_RE = re.compile(
    r"^(Total\s*Vo|V?o[a-z]{0,2}e?\s*Fo|Undervotes?|Overvotes?|Write-?\s*in|"
    r"Continued|Ft\s*Wiw|Wi\s*Cw|WI\s*\(W\)|^[A-Za-z]$)",
    re.IGNORECASE)
PARTY_TOK_RE = re.compile(
    r"\(\s*(DEM|REP|W|W/I|WI|WRITE-?IN|D/R|LIB|IND|GRN|CON|WF|SSP)\s*\)",
    re.IGNORECASE)


def clean_num(tok):
    """Parse an OCR'd integer, stripping trailing junk like '0)' or 'it)'."""
    t = tok.replace(",", "")
    m = re.search(r"\d+", t)
    return int(m.group()) if m else None


class Contest:
    def __init__(self, office, district, page):
        self.office, self.district, self.page = office, district, page
        self.vote_for = None
        self.total = None
        self.cands = []        # (name, party, votes, pct, page)
        self.writeins = None   # aggregate (votes, page)
        self.under = None
        self.over = None
        self.named_w = 0       # sum of named write-in rows
        self.named_rows = []   # (votes, pct) per named write-in row
        self.pending_total = False  # "Total Votes" number wrapped to next line


def votes_from(toks, total, warnings, precinct, office, page, name):
    """Derive a row's vote count from its OCR'd tokens.

    The source prints "<votes> <pct>%"; the percentage (2 decimals) lets us
    repair OCR-mangled counts: for these totals round(pct*total/100) is
    exact.  A bare "0%" also pins junk tokens like "1)" or "9" to 0.
    """
    raw = None
    pct = None
    for t in toks:
        if PCT.fullmatch(t) or re.fullmatch(r"\d+\s*%", t):
            pct = t
        elif re.search(r"\d", t):
            if raw is None:
                raw = clean_num(t)
    if pct is not None:
        try:
            p = float(pct.strip().replace("%", ""))
        except ValueError:
            p = None
        if p is not None:
            if p == 0:
                return 0, pct, raw is not None and raw != 0
            if total:
                derived = int(round(p / 100 * total))
                if raw is not None and derived != raw:
                    return derived, pct, True
                return (raw if raw is not None else derived), pct, False
    return raw, pct, False


def parse_stream(stream, precinct, warnings):
    """Parse one column stream (list of line dicts, in order)."""
    contests = []
    cur = None
    pending_header = []

    def warn(msg):
        warnings.append(msg)

    def flush():
        nonlocal cur
        if cur is not None:
            contests.append(cur)
        cur = None

    def has_data(c):
        return c is not None and (c.total is not None or c.cands
                                  or c.writeins is not None
                                  or c.under is not None
                                  or c.over is not None)

    for ln in stream:
        s = ln["text"].strip().strip("|").strip()
        if not s:
            continue
        page = ln.get("page", "?")

        # page furniture
        if re.match(r"^(Precinct Summary Report|OFFI?[CCL]?AL? ELECTION RESULTS"
                    r"|RESULTS|MUNICIPAL ELECTION|NOVEMBER)", s, re.IGNORECASE):
            continue
        if "SUSQUEHANNA COUNTY" in s.upper() or "Date:" in s or "Time:" in s:
            continue
        if CONTINUED_RE.match(s):
            continue
        if PAGE_MARK.search(s) and len(s) < 30:
            continue

        m = VOTEFOR_RE.search(s)
        if m and len(s) < 25:
            vf = int(m.group(1))
            if pending_header:
                if has_data(cur):
                    flush()
                hdr = " ".join(pending_header)
                pending_header = []
                mapped = map_header(hdr)
                if mapped:
                    cur = Contest(*mapped, page)
                    cur.vote_for = vf
                else:
                    warn(f"p{page} {precinct}: unmapped header {hdr!r}")
            elif cur is None:
                warn(f"p{page} {precinct}: Vote For {vf} with no header")
            elif cur.vote_for is None:
                cur.vote_for = vf
            continue

        m = TOTALVOTES_RE.search(s)
        if m and cur is not None and len(s) < 30:
            if cur.total is None:
                val = m.group(1)
                if val and not re.search(r"[A-Za-z]", val):
                    # the count itself can be OCR-mangled ("Sn7" for 317);
                    # a garbage token must not become the total -- leave it
                    # pending so a repeat on a continuation box can fill it
                    cur.total = clean_num(val)
                if cur.total is None:
                    cur.pending_total = True
            pending_header = []
            continue

        if is_header_ish(s):
            mapped = map_header(s)
            if mapped:
                # continuation boxes repeat the same contest header; a new
                # box for the SAME office+district never occurs within one
                # precinct, so treat the repeat as a continuation
                if (cur is not None and mapped == (cur.office, cur.district)):
                    pending_header = []
                    continue
                if has_data(cur) or pending_header:
                    flush()
                cur = Contest(*mapped, page)
                pending_header = []
                continue
            if s.upper().startswith("OF ") and cur is not None:
                # wrapped tail of the previous header, e.g. "... CLERK" /
                # "OF ORPHANS COURT": the contest was already mapped
                pending_header = []
                continue
            # possible first line of a wrapped header
            if pending_header and map_header(" ".join(pending_header)):
                if has_data(cur):
                    flush()
                pending_header = []
            pending_header.append(s)
            combined = map_header(" ".join(pending_header))
            if combined:
                if cur is not None \
                        and combined == (cur.office, cur.district):
                    pending_header = []
                    continue
                if has_data(cur):
                    flush()
                cur = Contest(*combined, page)
                pending_header = []
            continue

        if cur is None:
            continue  # data before any readable header

        # OCR noise from box borders: stray 1-2 character tokens ("a", "oO")
        if re.fullmatch(r"[A-Za-z]{1,2}[).,]?", s):
            continue

        # wrapped-row continuation: a numbers-only line belongs to the
        # previously seen row ("Undervote" / "48", "Write-in" / "0 0%", ...)
        if not re.search(r"[A-Za-z]", s):
            toks = s.split()
            has_pct = any(PCT.fullmatch(t) or re.fullmatch(r"\d+\s*%", t)
                          for t in toks)
            nums = [t for t in toks if re.search(r"\d", t)
                    and not PCT.fullmatch(t)
                    and not re.fullmatch(r"\d+\s*%", t)]
            if nums:
                v = clean_num(nums[0])
                # a percentage marks a wrapped candidate row ("72 24.91%" --
                # the digits cluster away from the name): it must never fill
                # Total Votes / Undervote / Overvote / Write-in, all of which
                # are printed without a percentage
                if has_pct and cur.cands and cur.cands[-1][2] is None:
                    nm, pty, _, pct0, pg0 = cur.cands[-1]
                    if pct0 is None:
                        pct0 = next((t for t in toks if PCT.fullmatch(t)
                                     or re.fullmatch(r"\d+\s*%", t)), None)
                    cur.cands[-1] = (nm, pty, v, pct0, page)
                elif has_pct:
                    pass
                elif getattr(cur, "pending_total", False):
                    cur.total = v
                    cur.pending_total = False
                elif cur.under is not None and cur.under[0] is None:
                    cur.under = (v, page)
                elif cur.over is not None and cur.over[0] is None:
                    cur.over = (v, page)
                elif cur.writeins is not None and cur.writeins[0] is None:
                    cur.writeins = (v, page)
                elif cur.cands and cur.cands[-1][2] is None:
                    nm, pty, _, pct, pg0 = cur.cands[-1]
                    cur.cands[-1] = (nm, pty, v, pct, page)
            continue

        # candidate / write-in / under / over rows
        fc = fuzzy_row_class(s)
        if fc == "under":
            m = UNDER_RE.search(s)
            cur.under = (clean_num(m.group(1)) if m else
                         next((clean_num(t) for t in s.split()
                               if re.search(r"\d", t)), None), page)
            continue
        if fc == "over":
            m = OVER_RE.search(s)
            cur.over = (clean_num(m.group(1)) if m else
                        next((clean_num(t) for t in s.split()
                              if re.search(r"\d", t)), None), page)
            continue
        if fc == "wi" and not PARTY_TOK_RE.search(s):
            v = next((clean_num(t) for t in s.split()
                      if re.search(r"\d", t) and not PCT.fullmatch(t)), None)
            if cur.writeins is None:
                cur.writeins = (v, page)
            else:  # continuation box repeating the aggregate: keep first
                if v and (cur.writeins[0] or 0) == 0:
                    cur.writeins = (v, page)
            continue
        # a contest header line that the cheap tests rejected
        if map_header(s) is not None:
            if has_data(cur):
                flush()
            cur = Contest(*map_header(s), page)
            pending_header = []
            continue
        # wrapped named-write-in row: the "(W)" marker sits alone on a line
        if re.fullmatch(r"\(W\)\s*", s) and cur.cands \
                and cur.cands[-1][1] == "":
            nm, _, v, pct, pg0 = cur.cands.pop()
            cur.named_w += v or 0
            cur.named_rows.append((v, pct))
            continue

        # wrapped party token: "(REP" ended the previous line, "REP)" alone
        # on this one -- attach to the previous partyless candidate row
        mpt = re.fullmatch(r"\(?\s*(DEM|REP|LIB|IND|GRN|CON)\s*\)?\s*[,.;|]*",
                           s)
        if mpt and cur.cands and cur.cands[-1][1] == "":
            nm, _, v, pct, pg0 = cur.cands[-1]
            cur.cands[-1] = (nm, mpt.group(1), v, pct, pg0)
            continue

        toks = s.split()
        pm = PARTY_TOK_RE.search(s)
        if pm:
            party = pm.group(1).upper()
            is_w = party in ("W", "WI", "W/I", "WRITE")
            name = s[:pm.start()].strip(" |(),.:;")
            if JUNK_NAME_RE.match(name):
                continue
            v, pct, repaired = votes_from(
                toks, cur.total, warnings, precinct, cur.office, page, name)
            if repaired:
                warnings.append(
                    f"p{page} {precinct} {cur.office}: {name} votes repaired "
                    f"to {v} from pct {pct}")
            if name:
                if is_w:
                    if v:
                        warnings.append(
                            f"NAMEDWI p{page} {precinct} {cur.office}: "
                            f"{name} = {v}")
                    cur.named_w += v or 0
                    cur.named_rows.append((v, pct))
                else:
                    cur.cands.append((name, party, v, pct, page))
            continue
        # nonpartisan candidate row (no party suffix): "Yes 6,630 18.74%"
        if len(toks) >= 2 and re.search(r"\d", toks[-1]):
            name_toks = [t for t in toks if not PCT.fullmatch(t)
                         and not re.search(r"\d", t)]
            name = " ".join(name_toks).strip(" |(),.:;").strip()
            if name and not JUNK_NAME_RE.match(name):
                v, pct, repaired = votes_from(toks, cur.total, warnings,
                                              precinct, cur.office, page,
                                              name)
                # OCR-mangled label rows that slipped through the cheap
                # tests: a garbled "Total Votes" line ("{otal Votes 276")
                # is the box total, never a candidate; "NO or 35.92%" /
                # "No vAs" in a retention box are the NO row
                lab = label_row_class(name)
                if lab == "total":
                    if cur.total is None and v:
                        cur.total = v
                    continue
                if (lab is None and pct is None and v is not None
                        and cur.total is None
                        and all(len(t) <= 3 for t in name_toks)):
                    # an OCR-shredded "Total Votes N" line ("res sti 218"):
                    # real candidate rows always print a percentage, and no
                    # real name is a pair of <=3-letter tokens over a bare
                    # count, so the number is the box total
                    cur.total = v
                    continue
                if v is not None:
                    if lab in ("yes", "no") and "Retention" in cur.office:
                        name = lab.capitalize()
                    cur.cands.append((name, "", v, pct, page))
                    continue
        warn(f"p{page} {precinct}: unparsed line in {cur.office!r}: {s!r}")

    if pending_header:
        warn(f"unconsumed header fragment: {' '.join(pending_header)!r}")
    flush()
    return contests


# --------------------------------------------------------------------------
# precinct grouping and naming
# --------------------------------------------------------------------------

JUNK_IN_NAME_RE = re.compile(
    r"(Date:|Time:|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}:\d{2}:\d{2}|[AP]M|"
    r"PRECINCT SUMMARY REPORT|MUNICIPAL ELECTION|GENERAL ELECTION|"
    r"NOVEMBER|CERTIFIED|OFFI?[CCL]?AL|RESULTS|SUSQUEHANNA COUNTY|"
    r"Election Summary Report|Page\s*\d+|\d+\s*/\s*\d+)", re.IGNORECASE)


def page_name(lines):
    """Guess the precinct name from a page's header box."""
    idx = None
    for i, ln in enumerate(lines):
        if re.search(r"ELECTION RESULTS", ln["text"], re.IGNORECASE):
            idx = i
            break
    if idx is None:
        return ""
    toks = []
    for ln in lines[:idx]:
        t = JUNK_IN_NAME_RE.sub(" ", ln["text"])
        t = re.sub(r"[^A-Za-z .'\-]", " ", t)
        t = re.sub(r"\s+", " ", t).strip(" .|-")
        if len(t) >= 4 and not re.fullmatch(r"[A-Z]{1,2}", t):
            toks.append(t)
    return " ".join(toks).strip()


def group_pages(pages):
    """Group page numbers into precincts via the printed Page n/m footer."""
    groups = []
    prev_n = None
    for n in sorted(pages):
        k = None
        for ln in pages[n]:
            m = PAGE_MARK.search(ln["text"])
            if m:
                k = int(m.group(1))
                break
        if k == 1 or (k is not None and prev_n is not None and k <= prev_n):
            groups.append([])
        if not groups:
            groups.append([])
        groups[-1].append(n)
        prev_n = k
    return groups


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _pct_ok(c, total, tol=1):
    """True when most pct-bearing rows agree with this total.

    Named write-in rows carry printed percentages too, so they count as
    evidence alongside candidate rows (write-in-only boxes -- auditors,
    2-year council seats -- have no candidate rows at all).
    """
    rows = []
    for _, _, v, pct, _ in c.cands:
        if not pct or not v:
            continue
        try:
            p = float(pct.strip().replace("%", ""))
        except ValueError:
            continue
        if p > 0:
            rows.append((v, p))
    for v, pct in getattr(c, "named_rows", ()):
        if not pct or not v:
            continue
        try:
            p = float(pct.strip().replace("%", ""))
        except ValueError:
            continue
        if p > 0:
            rows.append((v, p))
    if not rows:
        return False
    ok = sum(1 for v, p in rows if abs(round(p / 100 * total) - v) <= tol)
    return ok / len(rows) >= 0.5


def _pct_impossible(c, total):
    """True when most printed percentages cannot arise from this total.

    The source prints pct = round(100*v/total, 2); for a genuine total T
    the round-trip is exact at precinct-level sizes (round(pct*T/100) is
    the printed count, so 100*round(pct*T/100)/T reproduces the pct to
    within 0.005).  A total from which most printed pcts have no integer
    count within rounding ("30.81%" cannot come from a 3-vote box) is an
    OCR-mangled total.
    """
    pcts = [pct for _, _, _, pct, _ in c.cands if pct]
    pcts += [pct for _, pct in c.named_rows if pct]
    if not pcts:
        return False
    imp = 0
    seen = 0
    for pct in pcts:
        try:
            p = float(pct.strip().replace("%", ""))
        except ValueError:
            continue
        if p <= 0:
            continue  # a 0% row is possible at any total; no information
        seen += 1
        v = int(round(p / 100 * total))
        if abs(100 * v / total - p) > 0.01:
            imp += 1
    return seen > 0 and imp > seen / 2


def repair(c, precinct, ballots, warnings):
    """Cross-check repairs using the report's own printed arithmetic.

    (a) a candidate count mangled by OCR ("$12" for 312) is re-derived from
        its printed percentage when it grossly disagrees with the Total
        Votes (the source prints 2 decimals, so round(pct*total/100) is
        exact for these totals);
    (b) the box identity  Total Votes + Undervote + Overvote =
        Ballots Cast * Vote For  (verified across the report) recovers a
        Total Votes line lost to OCR and repairs mangled
        Undervote/Overvote rows, e.g. "Wadearats 4107" for "Undervote 107".
    """
    csum = sum(v or 0 for _, _, v, _, _ in c.cands)
    # (a0) vote-for inference: retention questions and single-seat local
    # offices often lost their "Vote For 1" line to OCR.  Retention
    # questions are always vote-for-1; for any other contest, one whose
    # total/row-sum does not exceed ballots cast cannot be vote-for-2
    # (that would require >50% undervotes), so the identity pins it to 1.
    if c.vote_for is None and ballots and csum > 0:
        if "Retention" in c.office or max(csum, c.total or 0) <= ballots:
            c.vote_for = 1
    # (a1) the parsed Vote For itself can be OCR noise ("Vote For 1" read
    # as "Vote For 4").  When the printed Total + Undervote + Overvote
    # closes exactly at a SMALLER vote-for and the printed percentages
    # confirm the Total, prefer that vote-for: it preserves the printed
    # Undervote instead of overwriting it with ballots*vote_for - total.
    if c.vote_for and c.vote_for > 1 and c.total and ballots:
        u1 = c.under[0] if c.under else None
        o1 = c.over[0] if c.over else None
        if u1 is not None and o1 is not None:
            s = c.total + u1 + o1
            k, rem = divmod(s, ballots)
            if (rem == 0 and 1 <= k < c.vote_for
                    and _pct_ok(c, c.total, tol=0)):
                warnings.append(
                    f"p{c.page} {precinct} {c.office}: vote-for "
                    f"{c.vote_for} -> {k} (Total {c.total} + undervote {u1}"
                    f" + overvote {o1} = {s} = {k} x ballots {ballots})")
                c.vote_for = k
    # (b0) recover a lost or zeroed Total Votes -- and re-derive a printed
    # Total that is IMPOSSIBLE (larger than ballots*vote_for, i.e. an
    # OCR-inflated count such as "44Q" read for 142).  Write-in-only boxes
    # (borough auditor seats) have no candidate rows, so the named
    # write-in rows also qualify the box for repair.
    if c.vote_for and ballots and (csum > 0 or c.named_w > 0):
        impossible = c.total is not None and c.total > ballots * c.vote_for
        if not c.total or impossible:
            u = c.under[0] if c.under else None
            o = c.over[0] if c.over else None
            if u is not None or o is not None:
                t = ballots * c.vote_for - (u or 0) - (o or 0)
                if t >= csum and _pct_ok(c, t, tol=0):
                    warnings.append(
                        f"p{c.page} {precinct} {c.office}: Total Votes "
                        f"{c.total} -> {t} from ballots*vote_for - undervote "
                        f"- overvote")
                    c.total = t
                elif impossible and t > 0:
                    # the printed total AND the row counts are mutually
                    # inconsistent: parse-time pct repair inflated the rows
                    # using the bad total.  Re-derive every percentage row
                    # (candidates AND named write-ins) at the identity total
                    # -- the printed 2-decimal percentages pin
                    # round(pct*total/100) exactly.
                    derived = 0
                    for _, _, v, pct, _ in c.cands:
                        if not pct:
                            continue
                        try:
                            p = float(pct.strip().replace("%", ""))
                        except ValueError:
                            continue
                        if p > 0:
                            derived += int(round(p / 100 * t))
                    for v, pct in c.named_rows:
                        if not pct:
                            continue
                        try:
                            p = float(pct.strip().replace("%", ""))
                        except ValueError:
                            continue
                        if p > 0:
                            derived += int(round(p / 100 * t))
                    if 0 < derived <= t:
                        warnings.append(
                            f"p{c.page} {precinct} {c.office}: Total Votes "
                            f"{c.total} -> {t} from ballots*vote_for - "
                            f"undervote - overvote (rows re-derived)")
                        c.total = t
    # (b1) a parsed Total that is arithmetically IMPOSSIBLE for the printed
    # percentages even though it fits under ballots*vote_for ("Total Votes
    # 3" for a real 211: no integer count at a 3-vote box can print
    # "30.81%").  The printed Undervote/Overvote close the box identity at
    # the true total, and the percentages are possible there, so take it.
    if c.vote_for and ballots and c.total is not None:
        u = c.under[0] if c.under else None
        o = c.over[0] if c.over else None
        if u is not None or o is not None:
            t = ballots * c.vote_for - (u or 0) - (o or 0)
            if (t > 0 and t != c.total and t >= csum
                    and _pct_impossible(c, c.total)
                    and not _pct_impossible(c, t)):
                warnings.append(
                    f"p{c.page} {precinct} {c.office}: Total Votes "
                    f"{c.total} -> {t}: printed percentages impossible at "
                    f"{c.total}; box identity (ballots {ballots} x vf "
                    f"{c.vote_for} - undervote {u} - overvote {o})")
                c.total = t
    # (b2) still no usable Total (or one above ballots*vote_for that no
    # identity could replace): estimate it from the printed candidate
    # percentages -- total = votes / pct -- and take the median estimate.
    if c.vote_for and ballots and (not c.total or c.total > ballots * c.vote_for):
        # from the printed percentages: total = votes / pct
        ests = []
        for _, _, v, pct, _ in c.cands:
            if pct and v:
                try:
                    p = float(pct.strip().replace("%", ""))
                except ValueError:
                    continue
                if p >= 5:  # low pcts give wildly imprecise estimates
                    ests.append(round(v * 100 / p))
        if ests:
            ests.sort()
            t = ests[len(ests) // 2]
            if (t >= csum and t <= ballots * c.vote_for
                    and _pct_ok(c, t)):
                warnings.append(
                    f"p{c.page} {precinct} {c.office}: Total Votes "
                    f"{c.total} -> {t} from candidate percentages")
                c.total = t
    if c.total:
        for i, (name, party, v, pct, pg) in enumerate(c.cands):
            if not pct:
                continue
            try:
                p = float(pct.strip().replace("%", ""))
            except ValueError:
                continue
            derived = int(round(p / 100 * c.total))
            cur = v or 0
            if v is None or (derived != cur
                             and abs(p - cur / c.total * 100) > 0.5):
                warnings.append(
                    f"p{pg} {precinct} {c.office}: {name} votes {v} -> "
                    f"{derived} (pct {pct} of Total Votes {c.total})")
                c.cands[i] = (name, party, derived, pct, pg)
        for i, (v, pct) in enumerate(c.named_rows):
            if not pct:
                continue
            try:
                p = float(pct.strip().replace("%", ""))
            except ValueError:
                continue
            derived = int(round(p / 100 * c.total))
            cur = v or 0
            if v is None or (derived != cur
                             and abs(p - cur / c.total * 100) > 0.5):
                warnings.append(
                    f"p{c.page} {precinct} {c.office}: named write-in row "
                    f"votes {v} -> {derived} (pct {pct} of Total Votes "
                    f"{c.total})")
                c.named_rows[i] = (derived, pct)
        c.named_w = sum(v or 0 for v, _ in c.named_rows)
    if c.total is None or not c.vote_for or not ballots:
        return
    short = ballots * c.vote_for - c.total
    under = c.under[0] if c.under else None
    over = c.over[0] if c.over else None
    if short < 0:
        warnings.append(
            f"p{c.page} {precinct} {c.office}: Total Votes {c.total} > "
            f"ballots {ballots} x vote-for {c.vote_for}; cannot cross-check")
        return
    if under is None and over is None:
        if short:
            warnings.append(
                f"p{c.page} {precinct} {c.office}: Undervote/Overvote rows "
                f"lost; set Undervote={short} from identity")
            c.under = (short, c.page)
        return
    if under is None:
        c.under = (max(short - (over or 0), 0), c.page)
    elif over is None:
        if under == 0 and short > 0:
            # a printed literal 0 next to a large shortfall is usually
            # column bleed; overvotes are rare, so the shortfall belongs
            # to the undervote
            c.under = (short, c.page)
            c.over = (0, c.page)
        else:
            c.over = (max(short - (under or 0), 0), c.page)
    elif under + over != short:
        warnings.append(
            f"p{c.page} {precinct} {c.office}: undervote {under} + overvote "
            f"{over} != expected shortfall {short}; repaired")
        if over == short:
            c.under = (0, c.page)
        elif under == short:
            c.over = (0, c.page)
        else:
            c.under = (max(short - (over or 0), 0), c.page)


def validate(contest, precinct, warnings):
    """Check one contest instance.

    Source box arithmetic: Total Votes = candidates + the "WI (W)" row +
    named write-in rows + the bottom aggregate "Write-in" row.  The value
    output as "Write-ins" is named_w + writeins (falling back to
    Total - candidates when the aggregate row is unreadable).
    """
    c = contest
    if c.total is None:
        warnings.append(f"p{c.page} {precinct} {c.office}: no Total Votes")
        return
    vsum = sum(v for _, _, v, _, _ in c.cands if v is not None)
    agg = c.writeins[0] or 0 if c.writeins else 0
    if vsum + c.named_w + agg != c.total:
        warnings.append(
            f"p{c.page} {precinct} {c.office}: cands {vsum} + named {c.named_w}"
            f" + writein-row {agg} != Total Votes {c.total}")
    for name, party, v, pct, pg in c.cands:
        if v is None:
            warnings.append(f"p{pg} {precinct} {c.office}: {name} no votes")
            continue
        if pct:
            try:
                got = float(pct.strip().replace("%", ""))
            except ValueError:
                continue
            want = v / c.total * 100 if c.total else 0
            if abs(got - want) > 0.02:
                warnings.append(
                    f"p{pg} {precinct} {c.office}: {name} pct {got} vs "
                    f"{want:.2f} (votes {v}/{c.total})")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_pages(input_path):
    """Return {page_number: lines} from a directory of tesseract TSV files."""
    if not os.path.isdir(input_path):
        raise SystemExit(f"input must be a directory of page-*.tsv files, "
                         f"got {input_path}")
    pages = {}
    for f in sorted(glob.glob(os.path.join(input_path, "page-*.tsv"))):
        n = int(re.search(r"page-(\d+)", f).group(1))
        pages[n] = read_tsv(f)
    return pages


PRECINCT_NAME_FIXES = {
    # OCR-mangled precinct name -> corrected name (filled from visual check)
}

# Precinct names read visually from the header box of each report's first
# page (the printed header is the only reliable name source -- the OCR of
# the stylized name line is badly mangled).  Keyed by the first page number
# of the report section.  Both 2023 Montrose Borough wards are separate
# reports ("MONTROSE BORO-1W" / "MONTROSE BORO-2W").
PRECINCT_NAMES = {
    3: "Apolacon Township",
    12: "Ararat Township",
    21: "Auburn Township",
    31: "Bridgewater Township",
    41: "Brooklyn Township",
    49: "Choconut Township",
    59: "Clifford Township",
    69: "Dimock Township",
    79: "Forest City Borough",
    88: "Forest Lake Township",
    97: "Franklin Township",
    106: "Friendsville Borough",
    115: "Gibson Township",
    125: "Great Bend Borough",
    134: "Great Bend Township",
    144: "Hallstead Borough",
    153: "Harford Township",
    162: "Harmony Township",
    172: "Herrick Township",
    181: "Hop Bottom Borough",
    187: "Hop Bottom Borough",
    188: "Hop Bottom Borough",
    190: "Jackson Township",
    199: "Jessup Township",
    209: "Lanesboro Borough",
    218: "Lathrop Township",
    226: "Lenox Township",
    235: "Liberty Township",
    244: "Little Meadows Borough",
    253: "Middletown Township",
    262: "Montrose Borough Ward 1",
    271: "Montrose Borough Ward 2",
    280: "New Milford Borough",
    289: "New Milford Township",
    299: "Oakland Borough",
    308: "Oakland Township",
    318: "Rush Township",
    328: "Silver Lake Township",
    337: "Springville Township",
    346: "Susquehanna Borough",
    355: "Thompson Borough",
    364: "Thompson Township",
    373: "Union Dale Borough",
}

# The Hop Bottom Borough report's printed "Page n/m" footers are misread on
# pages 187 ("Page 7/9" but preceded by a misread page number) and 188
# ("Page 3/9" for 8/9), splitting one precinct into three page groups.
MERGE_GROUPS = {187, 188}

# The Bridgewater Township report's "Page n/m" footers (pages 31-40) OCR'd
# too badly to register a restart, so pages 31-40 were swallowed into the
# preceding Auburn Township group (Auburn's own report is pages 21-30,
# "Page 1/10"-"10/10"; Bridgewater is "Page 1/10"-"10/10" on pages 31-40,
# verified visually from the header boxes).  Force the group split.
SPLIT_BEFORE = {31}

CANDIDATE_NAME_FIXES = {
    # OCR-mangled candidate name -> corrected name (filled from review)
}


def title_name(name):
    out = []
    for w in name.split():
        if len(w) > 2 and w[:2].lower() == "mc":
            # all-caps OCR ("MCCAFFERY") must become "McCaffery", not
            # "Mccaffery" -- the repo convention for these names
            out.append("Mc" + w[2:].capitalize())
        elif w.isupper() or w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def main():
    args = [a for a in sys.argv[1:] if a != "--county"]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <tsv_dir> <output.csv> [--county NAME]")
    src, dst = args
    pages = load_pages(src)
    warnings = []

    groups = group_pages(pages)
    # merge continuation groups whose footer page numbers were misread
    merged = []
    for g in groups:
        if g[0] in MERGE_GROUPS and merged:
            merged[-1].extend(g)
        else:
            merged.append(g)
    groups = merged

    # split groups where a footer restart was missed by the OCR
    split_groups = []
    for g in groups:
        cuts = [i for i, n in enumerate(g) if n in SPLIT_BEFORE and i > 0]
        if not cuts:
            split_groups.append(g)
            continue
        prev = 0
        for i in cuts:
            split_groups.append(g[prev:i])
            prev = i
        split_groups.append(g[prev:])
    groups = split_groups

    raw_names = [page_name(pages[g[0]]) for g in groups]
    names = [PRECINCT_NAMES.get(g[0], PRECINCT_NAME_FIXES.get(
        raw_names[i], raw_names[i])) for i, g in enumerate(groups)]
    for g, nm in zip(groups, names):
        if g[0] not in PRECINCT_NAMES:
            warnings.append(f"p{g[0]}: no verified precinct name for "
                            f"OCR name {nm!r}")

    meta_by_group = [page_meta(pages[g[0]]) for g in groups]

    all_contests = []
    empty_groups = set()
    for gi, g in enumerate(groups):
        pname = names[gi] or f"PRECINCT@PAGE{g[0]}"
        # reading order of the two-column boxes: page by page, left column
        # then right column (a box continues L->R on one page and onto the
        # next page's left column)
        stream = []
        for pn in g:
            _, left, right = page_split(pages[pn])
            for ln in left + right:
                if ln["y"] < 340:
                    continue  # page-header box area, not contest data
                ln = dict(ln)
                ln["page"] = pn
                stream.append(ln)
        before = len(all_contests)
        all_contests.extend(
            c for c in parse_stream(stream, pname, warnings))
        for c in all_contests[before:]:
            c.precinct = pname
            c.group = gi
        if not any(c.group == gi for c in all_contests[before:]):
            # cover / certification pages with no contest boxes
            empty_groups.add(gi)

    for c in all_contests:
        repair(c, c.precinct, meta_by_group[c.group][1], warnings)
        # (c) a candidate count lost entirely by OCR (no digits, no printed
        # percentage) is recovered from the box identity:
        #   candidate = Total Votes - all other rows
        if c.total is not None:
            for i, (name, party, v, pct, pg) in enumerate(c.cands):
                if v is not None:
                    continue
                rest = sum(x or 0 for j, (_, _, x, _, _) in enumerate(c.cands)
                           if j != i) + c.named_w
                if c.writeins is not None:
                    rest += c.writeins[0] or 0
                t2 = c.total - rest
                if 0 <= t2 <= c.total:
                    warnings.append(
                        f"p{pg} {c.precinct} {c.office}: {name} votes "
                        f"{v} -> {t2} from Total Votes minus other rows")
                    c.cands[i] = (name, party, t2, pct, pg)
            # the same recovery for a named write-in row whose count OCR
            # lost entirely ("CAROL HALE (W) a ., sone")
            for i, (v, pct) in enumerate(c.named_rows):
                if v is not None:
                    continue
                rest = sum(x or 0 for _, _, x, _, _ in c.cands) \
                    + sum(x or 0 for j, (x, _) in enumerate(c.named_rows)
                          if j != i)
                if c.writeins is not None:
                    rest += c.writeins[0] or 0
                t2 = c.total - rest
                if 0 <= t2 <= c.total:
                    warnings.append(
                        f"p{c.page} {c.precinct} {c.office}: named write-in "
                        f"row votes {v} -> {t2} from Total Votes minus other "
                        f"rows")
                    c.named_rows[i] = (t2, pct)
            c.named_w = sum(v or 0 for v, _ in c.named_rows)
        validate(c, c.precinct, warnings)

    rows = []
    for gi, g in enumerate(groups):
        if gi in empty_groups:
            continue
        pname = names[gi]
        rv, bal, blank = meta_by_group[gi]
        rows.append([COUNTY, pname, "Registered Voters", "", "", "", rv,
                     "", "", ""])
        rows.append([COUNTY, pname, "Ballots Cast", "", "", "", bal,
                     "", "", ""])
        rows.append([COUNTY, pname, "Ballots Cast - Blank", "", "", "", blank,
                     "", "", ""])
        for c in all_contests:
            if c.group != gi:
                continue
            for name, party, v, pct, pg in c.cands:
                nm = title_name(name)
                nm = CANDIDATE_NAME_FIXES.get(nm, nm)
                rows.append([COUNTY, pname, c.office, c.district, party,
                             nm, v, "", "", ""])
            agg = c.writeins[0] or 0 if c.writeins else 0
            wi = agg + c.named_w
            csum = sum(v for _, _, v, _, _ in c.cands if v is not None)
            if c.total is not None and csum + wi != c.total:
                wi = c.total - csum
            if wi < 0:
                warnings.append(
                    f"p{g[0]} {pname} {c.office}: write-ins {wi} negative "
                    f"(cands {csum}, total {c.total}); clamped to 0")
                wi = 0
            if c.writeins is not None or c.named_w:
                rows.append([COUNTY, pname, c.office, c.district, "",
                             "Write-ins", wi, "", "", ""])
            if c.under is not None:
                rows.append([COUNTY, pname, c.office, c.district, "",
                             "Undervotes", c.under[0], "", "", ""])
            if c.over is not None:
                rows.append([COUNTY, pname, c.office, c.district, "",
                             "Overvotes", c.over[0], "", "", ""])

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDNAMES)
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {dst}")
    print(f"precincts: {len(groups)}; contests: {len(all_contests)}")
    print(f"warnings: {len(warnings)}")
    for wmsg in warnings:
        print("WARN:", wmsg, file=sys.stderr)
    print("\n-- precinct names (raw OCR -> output) --")
    for g, raw, fixed in zip(groups, raw_names, names):
        print(f"  p{g[0]:>3}: {raw!r} -> {fixed!r}")


if __name__ == "__main__":
    main()