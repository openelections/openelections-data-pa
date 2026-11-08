#!/usr/bin/env python3
"""Parse Montgomery County PA 2026 Primary precinct results.

Source: "Montgomery County StatementOfVotesCastRPT 5.pdf" — a 3,374-page
Dominion "Statement of Votes Cast" PDF stamped OFFICIAL RESULTS (run
6/8/2026; the county's most recent per-precinct report available 11/7).

Document structure (1-based PDF pages):

* Pages 1-62: a turnout report — per precinct, one row per counting
  group (Election Day / Mail-in / Provisional / Total) with three
  numeric columns: Registered Voters, Votes Cast, % Turnout. Used for
  the "Registered Voters" / "Ballots Cast" rows.

* Per contest, precinct groups of ~5 precincts each spread over
  paired/continuation pages:

  - "Main" page: a left section (Times Cast / Registered Voters — not
    captured) and a right section with the vote columns that fit:
    candidate columns with rotated char-reversed headers (``ORIPAHS`` =
    ``SHAPIRO``) and an ``(DEM)``/``(REP)`` party token per column,
    then — when they fit — a Write-in column and a Total Votes column
    (Total Votes = named candidates only; never captured).
  - Continuation page(s): further candidate columns plus Write-in /
    Total Votes, in a single left-aligned section (precinct names at
    x~20, row labels at x~80-112, first data row as early as y~123).
  - "Write-in spill" pages (``Unresolved Write-In`` header, no party
    tokens): repeat the Write-in column for that group's precincts.

* Each contest's pages end with ``Montgomery County - Total`` and
  all-zero ``Cumulative`` blocks, which are skipped.

Two bugs fixed relative to the July batch (which undershot every
contest by ~17%): the old version hard-coded a y>=170 floor on data
rows, silently dropping the first precinct block of every page whose
header zone was short (the first data row starts at y~123 on
continuation pages), and it could not read the single-section
continuation pages that carry candidates 3+ of wide contests (no
right-hand ``Precinct`` column there), dropping all candidates beyond
the first two of a wide contest (e.g. only 2 of 4 DEM State Committee
candidates, 2 of 21 Republican State Committee candidates).

Rotated-name reading rule (verified against JOSH SHAPIRO, MARY GAY
SCANLON, MEGAN GRIFFIN-SHELLEY, NAPOLEON NELSON, CRAIG C BROWNE,
AYANNA LANEY-MARTIN): each name is drawn as vertical text reading
bottom-to-top, with wrapped continuation words shifted ~10px right.
Group the name words into columns by x (new column on a gap > 5),
order columns by ascending x, sort within a column by descending top,
concatenate, and join a trailing hyphen to the next column without a
space.

Per-precinct write-in votes are captured as ``Write-In Totals`` rows
with the contest's party. Registered Voters / Ballots Cast rows come
from the turnout pages (Ballots Cast carries the counting-group
breakdown). Per-precinct committeeperson and township-referendum
contests are skipped.

Usage:
    # precinct file (Statement of Votes Cast):
    uv run python parsers/pa_montgomery_primary_2026_results_parser.py <sovc.pdf> <out.csv>
    # countywide totals (Election Summary Report PDF):
    uv run python parsers/pa_montgomery_primary_2026_results_parser.py <summary.pdf> <out.csv> --summary
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import natural_pdf as npdf

from electionware_primary_np import (
    PRIMARY_FIELDNAMES,
    STATEWIDE_OFFICES,
    _finalize_candidate,
)

COUNTY_FIELDNAMES = [
    "county", "office", "district", "party", "candidate", "votes",
    "election_day", "mail", "provisional",
]

DISTRICT_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+"
    r"(?:CONGRESSIONAL\s+|LEGISLATIVE\s+|SENATORIAL\s+)?DISTRICT\b",
    re.IGNORECASE,
)

OFFICE_HEADER_RE = re.compile(
    r"^(.+?)\s*-\s*(DEM|REP|NON|GP|GRN|LBR|CON)\s*\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)
STATE_COMMITTEE_RE = re.compile(
    r"^Member of (?:the\s+)?(Democratic|Republican) State Committee"
    r"(?:\s*\((?:Senate District\s+)?(\d+)\))?\s*\(Vote for\s+\d+\)\s*$",
    re.IGNORECASE,
)
SKIP_OFFICE_KEYWORDS = ("Committeeperson", "Referendum", "Question")

PARTY_TOKEN_RE = re.compile(r"\((DEM|REP|NON|GP|GRN|LBR|CON)\)")
PARTY_NORMALIZE = {"DEMOCRATIC": "DEM", "REPUBLICAN": "REP"}

ROTATED_JUNK_WORDS = {
    "Votes", "Write-in", "Write-In", "Write-ins", "Totals", "Total",
    "Cast", "Times", "Registered", "Voters", "Unresolved", "Blank",
    "blank", "Precinct",
}

ROW_LABEL_WORDS = {"Election", "Day", "Mail-in", "Mail", "Provisional", "Total"}

NAME_JUNK_WORDS = {
    "Precinct", "County", "Cumulative", "Unresolved",
    "Write-In", "Write-in", "Total", "Times", "Cast", "Registered",
    "Voters", "Votes", "Statement",
}

Y_PAGE_HEADER = 34        # nothing but the page stamp sits above y~34
Y_TITLE_HEADER_MAX = 50   # horizontal office header sits at top<=36; the
                         # standalone "DEM"/"REP" ballot-party line is at
                         # 52.2 and must stay OUT of the header band
Y_ROTATED_MAX = 165       # rotated candidate/vote-header zone (max word ~156)

X_NAME_MAX = 122           # left-section precinct names span x20..~117
X_LABEL_ZONE_L = (75, 125)   # single-section row labels
X_LABEL_ZONE_R = (450, 540)  # main-page right-section row labels
X_TOKEN_MIN = 120            # rotated name words / tokens never left of 120
X_TOKEN_MAX = 760

VOTE_X_TOL = 25   # vote numbers sit within ~19px of their column header
NAME_X_TOL = 55   # name words sit within ~47px of their party token
NAME_COL_GAP = 5  # wrapped continuation words sit ~10px right of the base
ROW_BUCKET = 3

_WRITEIN = object()  # sentinel vote anchor


def _reverse_text(s: str) -> str:
    return s[::-1]


def _is_int(s: str) -> bool:
    try:
        int(s.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def _group_lines(words: list[dict]) -> list[tuple[float, list[dict]]]:
    lines: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        lines[round(w["top"] // ROW_BUCKET) * ROW_BUCKET].append(w)
    return [(t, sorted(v, key=lambda w: w["x0"])) for t, v in sorted(lines.items())]


def _extract_office_header(words: list[dict]) -> tuple[str, str, str] | None:
    """(office, district, party) from the horizontal y~36 header.

    ("", "", "") signals a skip (committeeperson/referendum page); None
    means no header (continuation page).
    """
    header_words = [
        w for w in words
        if 28 <= w["top"] <= Y_TITLE_HEADER_MAX and 10 < w["x0"] < 690
    ]
    if not header_words:
        return None
    header_words.sort(key=lambda w: w["x0"])
    line = " ".join(w["text"] for w in header_words)
    if any(k in line for k in SKIP_OFFICE_KEYWORDS):
        return ("", "", "")
    m = OFFICE_HEADER_RE.match(line)
    if m:
        return _normalize_office(m.group(1).strip(), m.group(2).upper())
    sm = STATE_COMMITTEE_RE.match(line)
    if sm:
        party = PARTY_NORMALIZE.get(sm.group(1).upper(), "")
        district = str(int(sm.group(2))) if sm.group(2) else ""
        return ("Member of " + sm.group(1).title() + " State Committee",
                district, party)
    return None


def _normalize_office(raw: str, party: str) -> tuple[str, str, str]:
    upper = raw.upper()
    dm = DISTRICT_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    key = DISTRICT_RE.sub("", upper).strip() if dm else upper
    if key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[key]
        return (norm, district if extract else "", party)
    for k, (norm, extract) in STATEWIDE_OFFICES.items():
        if key == k or key.startswith(k + " "):
            return (norm, district if extract else "", party)
    return (raw.title(), district, party)


def _assemble_name(parts: list[tuple[float, float, str]]) -> str:
    """Assemble a rotated candidate name from (x0, top, word) triples."""
    byx = sorted(parts, key=lambda p: p[0])
    cols: list[list[tuple[float, float, str]]] = []
    for w in byx:
        if cols and w[0] - cols[-1][-1][0] < NAME_COL_GAP:
            cols[-1].append(w)
        else:
            cols.append([w])
    ordered: list[str] = []
    for col in cols:
        col.sort(key=lambda p: p[1], reverse=True)  # reads bottom-to-top
        ordered.extend(p[2] for p in col)
    out = ""
    for part in ordered:
        if out.endswith("-"):
            out += part
        elif out:
            out += " " + part
        else:
            out = part
    return out


def _extract_candidates(words: list[dict]) -> tuple[list[dict], list[float]]:
    """(candidates, write-in column x positions) for this page."""
    has_office_header = any(
        28 <= w["top"] <= Y_TITLE_HEADER_MAX and "(" in w["text"]
        and w["x0"] < 690
        for w in words
    )
    y_min = Y_TITLE_HEADER_MAX + 2 if has_office_header else Y_PAGE_HEADER

    rot = [
        w for w in words
        if y_min < w["top"] < Y_ROTATED_MAX and 120 <= w["x0"] < X_TOKEN_MAX
    ]
    tokens = [w for w in rot if PARTY_TOKEN_RE.fullmatch(_reverse_text(w["text"]))]
    write_in_xs = [
        w["x0"] for w in rot
        if _reverse_text(w["text"]) in ("Write-in", "Write-In")
    ]

    claimable = [
        w for w in rot
        if not w["text"].strip().isdigit()
        and _reverse_text(w["text"]) not in ROTATED_JUNK_WORDS
        and all(c.isalpha() or c in "-.'" for c in _reverse_text(w["text"]))
    ]
    candidates: list[dict] = []
    for tok in tokens:
        tok_x = tok["x0"]
        owned = []
        for w in claimable:
            dx = abs(w["x0"] - tok_x)
            if dx > NAME_X_TOL:
                continue
            nearest_dx = min(abs(w["x0"] - t["x0"]) for t in tokens)
            if dx <= nearest_dx + 0.01:
                owned.append(w)
        if not owned:
            continue
        candidates.append({
            "name": _hyphen_capitalize(_finalize_candidate(_assemble_name(
                [(w["x0"], w["top"], _reverse_text(w["text"])) for w in owned]
            ))),
            "party": PARTY_TOKEN_RE.fullmatch(
                _reverse_text(tok["text"])
            ).group(1).upper(),
            "votes_x": tok_x,
        })
    return candidates, write_in_xs


def _page_kind(words: list[dict]) -> str:
    has_right = any(
        w["text"] == "Precinct" and Y_PAGE_HEADER < w["top"] < 165
        and 390 <= w["x0"] <= 460
        for w in words
    )
    return "main" if has_right else "single"


def _name_of_line(ws: list[dict]) -> tuple[str | None, bool]:
    """Classify a line in the left precinct-name column.

    Returns (precinct_name or None, county_marker).
    """
    left = [w for w in ws if 10 <= w["x0"] < X_NAME_MAX]
    if not left:
        return None, False
    texts = {w["text"] for w in left}
    # "Montgomery" (the township) is a real precinct name; the county and
    # county-total lines are recognisable by "County"/"Cumulative",
    # and the column-header line by "Precinct".
    if texts & {"County", "Cumulative", "Precinct"}:
        return None, True
    name_words = [
        w for w in left
        if w["text"] not in NAME_JUNK_WORDS
        and w["text"] not in ROW_LABEL_WORDS
    ]
    if not name_words:
        return None, False
    return " ".join(w["text"] for w in name_words).strip(), False


def _row_label(ws: list[dict], zone: tuple[float, float]) -> str | None:
    lw = next(
        (w for w in ws
         if zone[0] <= w["x0"] < zone[1]
         and w["text"] in ROW_LABEL_WORDS
         and w["text"] != "Day"),
        None,
    )
    if lw is None:
        return None
    if lw["text"] == "Election":
        return "Election Day"
    if lw["text"] in ("Mail-in", "Mail"):
        return "Mail-in"
    return lw["text"]


def _parse_turnout_page(words: list[dict], turnout: dict) -> None:
    """Turnout pages: rows of [Registered Voters, Votes Cast, % Turnout].

    The Registered Voters value repeats on every row; the Total row's
    Votes Cast is the precinct's ballots cast.
    """
    current_name: str | None = None
    rec: dict = {}
    suppressed = True

    def commit():
        if current_name and rec:
            turnout[current_name] = rec

    for top, ws in _group_lines(words):
        if top < 30:
            continue
        name, marker = _name_of_line(ws)
        if marker:
            commit()
            current_name = None
            suppressed = True
            continue
        if name:
            commit()
            if current_name and not rec:
                # wrapped precinct name ("Upper Providence Mont" + "Clare")
                turnout.pop(current_name, None)
                name = current_name + " " + name
            current_name = name
            rec = {}
            suppressed = False
            continue
        if suppressed or current_name is None:
            continue
        label = _row_label(ws, X_LABEL_ZONE_L)
        if label is None:
            continue
        vals = [
            int(w["text"].replace(",", "")) for w in ws
            if _is_int(w["text"])
        ]
        if len(vals) < 2:
            continue
        rec.setdefault("rv", vals[0])
        if label == "Election Day":
            rec.setdefault("ed", vals[1])
        elif label == "Mail-in":
            rec.setdefault("mail", vals[1])
        elif label == "Provisional":
            rec.setdefault("prov", vals[1])
        elif label == "Total":
            rec["total"] = vals[1]
        turnout[current_name] = rec
    commit()


def parse_sovc(pdf) -> tuple[dict, list, dict]:
    """Walk the SOVC pages; return (turnout, contest_order, merged).

    merged: (office, district, party) -> {precinct: {candidate: {label:
    votes}}} — "Write-In Totals" included as a candidate key.
    """
    turnout: dict[str, dict] = {}
    merged: dict = {}
    contest_order: list = []
    current: tuple | None = None
    state = "turnout"

    for page in pdf.pages:
        words = [
            {"top": w["top"], "x0": w["x0"], "text": w["text"]}
            for w in page._page.extract_words()
        ]
        hdr = _extract_office_header(words)
        if hdr == ("", "", ""):
            current = None
            state = "contests"
            continue
        if hdr is not None:
            current = hdr
            state = "contests"

        if state == "turnout":
            _parse_turnout_page(words, turnout)
            continue
        if current is None:
            continue

        okey = (current[0], current[1], current[2])
        if okey not in contest_order:
            contest_order.append(okey)

        cands, wr_xs = _extract_candidates(words)
        zone = X_LABEL_ZONE_R if _page_kind(words) == "main" else X_LABEL_ZONE_L
        anchors = [(c["votes_x"], c) for c in cands]
        anchors += [(x, _WRITEIN) for x in wr_xs]

        prec = None
        prec_rows: dict = {}
        prec_wr: dict = {}
        suppressed = True

        def flush():
            if prec is None:
                return
            slot = (
                merged.setdefault(okey, {}).setdefault(prec, {})
            )
            for cname, lbls in prec_rows.items():
                slot.setdefault(cname, {}).update(lbls)
            if prec_wr:
                slot.setdefault("Write-In Totals", {}).update(prec_wr)

        for top, ws in _group_lines(words):
            if top < 30:
                continue
            name, marker = _name_of_line(ws)
            if marker:
                flush()
                prec = None
                suppressed = True
                continue
            if name:
                suppressed = False
                if prec and not prec_rows and not prec_wr:
                    # wrapped precinct name
                    prec += " " + name
                    continue
                flush()
                prec = name
                prec_rows = {}
                prec_wr = {}
                continue
            if suppressed:
                continue
            label = _row_label(ws, zone)
            if label is None or prec is None:
                continue
            for w in ws:
                if not _is_int(w["text"]):
                    continue
                val = int(w["text"].replace(",", ""))
                best, best_dx = None, 999
                for ax, acand in anchors:
                    dx = abs(w["x0"] - ax)
                    if dx < best_dx:
                        best, best_dx = acand, dx
                if best is None or best_dx > VOTE_X_TOL:
                    continue
                if best is _WRITEIN:
                    prec_wr[label] = val
                else:
                    prec_rows.setdefault(best["name"], {})[label] = val
        flush()

    _dos_fill_missing(turnout, contest_order, merged)
    return turnout, contest_order, merged


def _dos_fill_missing(turnout, contest_order, merged) -> None:
    """PA DOS certified precinct-returns fallback.

    The Dominion SOVC itself omits one ballot geography: Upper
    Dublin 5-1's HD-153 piece (the SOVC prints the precinct on the
    HD-151 pass with zero Cerrato votes, but its HD-153 pages never
    list the precinct, even though the SOVC's own HD-153 county total
    and the Election Summary Report carry Sanchez 187 / Toner 31 /
    write-ins 2+1). Use the statewide certified DOS export slice
    (work2026p/dos/montgomery__dos_precinctreturns.txt, source
    "PA DOS 2026 General Primary Certified Precinct Returns
    Statewide.txt", county code 46) to fill per-precinct named-candidate
    cells that the SOVC lacks: a DOS key is applied only when this
    parser produced no row at all for that candidate in that precinct
    of an otherwise-covered contest. DOS gives totals only (no ED/mail/
    provisional breakdowns and no write-in rows), so those cells stay
    empty; the HD-153 Democratic/Republican countywide Write-in rows
    here therefore sit 2/1 below the county totals — documented.
    """
    dos_path = Path(__file__).resolve().parents[1] / (
        "work2026p/dos/montgomery__dos_precinctreturns.txt")
    if not dos_path.exists():
        return
    offices = {'GOV': 'Governor', 'LTG': 'Lieutenant Governor',
               'USC': 'U.S. House', 'STS': 'State Senate',
               'STH': 'State House'}
    dos = defaultdict(int)
    with dos_path.open() as fh:
        for fields in csv.reader(fh):
            of = offices.get(fields[8])
            if not of:
                continue
            dist = '' if fields[5] == '0' else fields[5]
            dos[(of, dist, fields[9],
                 _canon_last(f"{fields[12]} {fields[11]}"),
                 _dos2name(fields[22]))] += int(fields[15])

    contest_cands: dict = {}
    for okey, by_prec in merged.items():
        names = set()
        for slot in by_prec.values():
            names |= set(slot)
        contest_cands[okey] = names
    for (of, dist, p, canon_l, dos_prec), votes in dos.items():
        if not votes:
            continue
        okey = (of, dist, p)
        if okey not in contest_cands:
            continue  # contest not covered (should not happen)
        match = None
        for cname in contest_cands[okey]:
            if cname == 'Write-In Totals':
                continue
            if _canon_last(cname) == canon_l:
                match = cname
                break
        if match is None:
            continue
        prec = None
        for tprec in turnout:
            if tprec.upper() == dos_prec.upper():
                prec = tprec
        if prec is None:
            # DOS-only geography (e.g. "(STH 153)" split pieces live under
            # the plain precinct name); strip parenthetical qualifiers and
            # try the base name
            base = re.sub(r" \(.*?\)$", "", dos_prec)
            for tprec in turnout:
                if tprec.upper() == base.upper():
                    prec = tprec
        if prec is None:
            continue
        slot = merged[okey].setdefault(prec, {})
        for cname in slot:
            if cname == match:
                break  # we already have this candidate in this precinct
        else:
            slot[match] = {'Total': votes}


def _canon_last(name: str) -> str:
    c = re.sub(r"[.'\-]", '', name.upper())
    toks = [t for t in c.split() if t not in ('JR', 'SR', 'II', 'III', 'IV')]
    return toks[-1] if toks else ''


def _dos2name(name: str) -> str:
    """'ABINGTON W 3 D 1' -> Abington 3-1; 'UPPER DUBLIN X 5 X 1' ->
    Upper Dublin 5-1; 'ROCKLEDGE 1' -> Rockledge 1."""
    n = name.strip()
    n = re.sub(r" \(.*?$", "", n)
    toks = n.split()
    muni, rest = [], toks
    i = 0
    while i < len(toks) and not re.fullmatch(r"[WXDPB]", toks[i].upper()):
        muni.append(toks[i])
        i += 1
    rest = [t for t in toks[i:] if not re.fullmatch(r"[WXDPB]", t.upper())]
    nums = []
    words = []
    for t in rest:
        if re.fullmatch(r"\d+(ST|ND|RD|TH)", t, re.I):
            continue
        if t.upper() == 'CONGRESSIONAL':
            continue
        if t.isdigit():
            nums.append(str(int(t)))
        else:
            words.append(t)
    if words and nums:
        base = ' '.join(w.title() for w in words + nums)
    elif words:
        base = ' '.join(w.title() for w in words)
    elif len(nums) == 2:
        base = f"{nums[0]}-{nums[1]}"
    elif nums:
        base = nums[0]
    else:
        base = ''
    return ' '.join([t.title() for t in muni] + ([base] if base else []))


def build_precinct_rows(turnout, contest_order, merged) -> list[dict]:
    """Emit rows precinct-major: RV, BC, then contests in ballot order."""
    rows = []
    precincts = list(turnout.keys())
    extra = []
    for _, by_prec in merged.items():
        for p in by_prec:
            if p not in turnout and p not in extra:
                extra.append(p)

    def row(prec, office, district, party, candidate, votes, ed, mail, prov):
        return {
            "county": "Montgomery", "precinct": prec, "office": office,
            "district": district, "party": party, "candidate": candidate,
            "votes": votes, "election_day": ed, "provisional": prov,
            "absentee": mail,
        }

    for prec in precincts + extra:
        t = turnout.get(prec)
        if t:
            rows.append(row(prec, "Registered Voters", "", "", "",
                            t.get("rv", ""), "", "", ""))
            rows.append(row(prec, "Ballots Cast", "", "", "",
                            t.get("total", ""), t.get("ed", ""),
                            t.get("mail", ""), t.get("prov", "")))
        for okey in contest_order:
            slot = merged.get(okey, {}).get(prec)
            if not slot:
                continue
            o, d, p = okey
            named, wr = [], slot.get("Write-In Totals")
            for cname, rec in slot.items():
                if cname == "Write-In Totals":
                    continue
                named.append((cname, rec))
            for cname, rec in named:
                rows.append(row(prec, o, d, p, cname,
                                rec.get("Total", ""),
                                rec.get("Election Day", ""),
                                rec.get("Mail-in", ""),
                                rec.get("Provisional", "")))
            if wr:
                rows.append(row(prec, o, d, p, "Write-In Totals",
                                wr.get("Total", ""),
                                wr.get("Election Day", ""),
                                wr.get("Mail-in", ""),
                                wr.get("Provisional", "")))
    return rows


# --- countywide summary report -------------------------------------------

_SUMMARY_OFFICE_RE = re.compile(r"^([^-]+?)\s*-\s*(DEM|REP|NON)\s*\(Vote for\s+\d+\)\s*$")
_SUMMARY_TITLE_RE = re.compile(r"^(.+)\(Vote for\s+\d+\)\s*$")
_SUMMARY_CAND_RE = re.compile(
    r"^(.+?)\s+(DEM|REP)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)
_SUMMARY_WR_RE = re.compile(
    r"^Write-in\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s*$"
)


def _hyphen_capitalize(s: str) -> str:
    """Capitalize both halves of hyphenated name words (Laney-Martin)."""
    out = []
    for w in s.split(" "):
        if "-" in w.upper() and "JR" not in w.upper() and "SR" not in w.upper():
            out.append("-".join(p.capitalize() for p in w.split("-")))
        else:
            out.append(w)
    return " ".join(out)


def parse_summary_text(text: str) -> list[dict]:
    rows = []
    current = None
    for ln in text.splitlines():
        s = ln.strip()
        m = _SUMMARY_OFFICE_RE.match(s)
        if m:
            raw, party = m.group(1).strip(), m.group(2)
            if any(k in raw for k in SKIP_OFFICE_KEYWORDS):
                current = None
            else:
                current = _normalize_office(raw, party)
            continue
        sm = STATE_COMMITTEE_RE.match(s)
        if sm:
            party = PARTY_NORMALIZE.get(sm.group(1).upper(), "")
            district = str(int(sm.group(2))) if sm.group(2) else ""
            current = ("Member of " + sm.group(1).title() + " State Committee",
                       district, party)
            continue
        if _SUMMARY_TITLE_RE.match(s):
            # Unrecognized contest type (committeeperson / referendum).
            current = None
            continue
        if current is None or not s:
            continue
        if s.startswith("Write-in"):
            m = _SUMMARY_WR_RE.match(s)
            if m:
                rows.append({
                    "county": "Montgomery", "office": current[0],
                    "district": current[1], "party": current[2],
                    "candidate": "Write-In Totals",
                    "votes": int(m.group(4).replace(",", "")),
                    "election_day": int(m.group(1).replace(",", "")),
                    "mail": int(m.group(2).replace(",", "")),
                    "provisional": int(m.group(3).replace(",", "")),
                })
            continue
        m = _SUMMARY_CAND_RE.match(s)
        if m:
            name = _hyphen_capitalize(_finalize_candidate(m.group(1).strip()))
            rows.append({
                "county": "Montgomery", "office": current[0],
                "district": current[1], "party": current[2],
                "candidate": name,
                "votes": int(m.group(6).replace(",", "")),
                "election_day": int(m.group(3).replace(",", "")),
                "mail": int(m.group(4).replace(",", "")),
                "provisional": int(m.group(5).replace(",", "")),
            })
    return rows


def main_sovc(pdf_path: Path, out_path: Path) -> None:
    pdf = npdf.PDF(str(pdf_path))
    turnout, contest_order, merged = parse_sovc(pdf)
    rows = build_precinct_rows(turnout, contest_order, merged)
    write_csv(rows, out_path, PRIMARY_FIELDNAMES)
    precincts = len({r["precinct"] for r in rows})
    contests = len({(r["office"], r["district"], r["party"]) for r in rows})
    print(f"Wrote {len(rows)} rows across {contests} contests / "
          f"{precincts} precincts to {out_path}")


def write_csv(rows: list[dict], out_path: Path, fieldnames: list[str]) -> None:
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv: list[str]) -> None:
    args = argv[1:]
    summary = "--summary" in args
    args = [a for a in args if a != "--summary"]
    if len(args) != 2:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv> [--summary]")
    pdf_path, out_path = Path(args[0]), Path(args[1])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    if summary:
        pdf = npdf.PDF(str(pdf_path))
        buf = []
        for page in pdf.pages:
            buf.append(page._page.extract_text() or "")
        rows = parse_summary_text("\n".join(buf))
        write_csv(rows, out_path, COUNTY_FIELDNAMES)
        contests = {(r["office"], r["district"], r["party"]) for r in rows}
        print(f"Wrote {len(rows)} countywide rows across {len(contests)} "
              f"contests to {out_path}")
        return
    main_sovc(pdf_path, out_path)


if __name__ == "__main__":
    main(sys.argv)
