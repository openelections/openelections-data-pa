#!/usr/bin/env python3
"""
Parser for Clearfield County, PA 2023 Municipal Primary (May 16, 2023).

Sources (Electionware "Summary Results Report" reports, pdftotext -layout
extracts):
  * Clearfield County Summary Results 2023 Primary.pdf        -> county CSV
  * Clearfield County Precinct Summary Results 2023 Primary.pdf -> precinct CSV

One script, two modes; the mode is auto-detected from the number of
"Statistics" markers (1 = county summary, 70 = per-precinct report):

    python parsers/pa_clearfield_primary_2023_results_parser.py <input> <output>

The office/precinct-name conventions follow the county's 2023 general
parser, ``pa_clearfield_general_2023_results_parser.py`` (imported
read-only; its engines ``electionware_txt`` / ``electionware_precinct_np``
are reused, not edited). Only primary-specific differences are handled:

  * contest headers carry the party ("  DEM JUSTICE OF THE SUPREME COURT");
    that party goes on every row of the contest, including the Write-ins
    row (the convention of the sibling Electionware 2023 primary county
    summaries: Adams/Blair/Berks; a shared empty party would leave the
    DEM and REP Write-ins rows of the same office indistinguishable),
  * value rows carry SEVEN trailing numbers: total, election-day "all",
    election-day "polls", absentee/mail, provisional, plus two always-zero
    columns. total == col2+col3+col4+col5 on every row; election_day is
    emitted as col2+col3 so the breakdown sums to the total,
  * unnumbered "AUDITOR <MUNI>" / "SUPERVISOR <MUNI>" /
    "MEMBER OF COUNCIL <MUNI>" headers repeat once per term and carry no
    term; the term is recovered from the county's general-2023
    per-municipality term sets, taking the occurrence-th largest term
    (the report orders each office family by descending term, and
    single-occurrence municipalities sit in the highest-term block),
  * "COUNTY TREASURER"/"COUNTY CORONER" (no term in the primary header)
    are the 4-year Treasurer/Coroner; "COUNTY COMMISSIONER 1-4" and
    "DISTRICT ATTORNEY 1-4" carry the term explicitly,
  * "DEM TAX COLLECTOR 1-2" has no municipality in the source; the
    precinct report places it in Burnside Borough only and the Republican
    section of the same seat spells "TAX COLLECTOR 1-2 BURNSIDE BOROUGH",
  * "CITY CONTROLLER DUBOIS CITY CONTROLLER" -> City Controller (4 Year),
    Dubois City,
  * the primary spells the Philipsburg/Osceola school district without
    the slash and the third Clearfield ward "CLEARFIED"; both are
    restored to the general-file spelling,
  * primary MULTI auditor/supervisor contests are districted townshipwide
    ("Sandy Township") where the county's general file used per-precinct
    districts; the primary source's own districting is kept.
"""

import csv
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from electionware_txt import (  # shared engine, reused (not edited)
    FIELDNAMES,
    FOOTER_RE,
    JUNK_SUBSTR,
    TxtConfig,
    find_precinct_name,
    is_junk,
    numbers_only,
    strip_percent,
    trailing_numbers,
    txt_lines,
)
from electionware_precinct_np import (  # shared engine, do not edit
    VOTE_FOR_RE,
    _merge_split_aggregates,
    expand_muni_flexible,
)
import pa_clearfield_general_2023_results_parser as base
from pa_2023_summary_common import is_junk as esr_is_junk
from pa_2023_summary_common import nums_from

COUNTY = "Clearfield"
STAT_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)
PARTY_HDR = re.compile(r"^(DEM|REP)\s+(.+)$")

# county-level schema (precinct files insert "precinct" as the 2nd column;
# electionware_txt.FIELDNAMES is that 10-column version)
COUNTY_FIELDNAMES = ["county", "office", "district", "party", "candidate",
                     "votes", "election_day", "mail", "provisional"]

# ---------------------------------------------------------------------------
# Term-length sets per municipality, from the county's general-2023 precinct
# file (the same municipal seats are up in the May primary as in the
# November general of the same year).
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_GENERAL_CSV = _REPO / "2023/counties/20231107__pa__general__clearfield__precinct.csv"

NEEDS_TERM_OFFICES = {"Township Auditor", "Township Supervisor",
                      "Borough Council", "School Director At Large"}


def _load_general_terms():
    terms = defaultdict(set)
    try:
        with open(_GENERAL_CSV, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                m = re.match(r"^(Township Auditor|Township Supervisor|"
                             r"Borough Council|School Director At Large)"
                             r" \((\d+) Year\)$", r["office"])
                if m:
                    terms[(m.group(1), r["district"])].add(int(m.group(2)))
    except OSError:
        pass
    return terms


GENERAL_TERMS = _load_general_terms()

# Header text that carries no municipality and/or no term in the primary
# source (the general headers read "COUNTY COMMISSIONER 2-4" /
# "COUNTY TREASURER 1-4").
NO_MUNI_HEADERS = {
    "COUNTY COMMISSIONER 1-4": ("County Commissioner (4 Year)", ""),
    "DISTRICT ATTORNEY 1-4": ("District Attorney (4 Year)", ""),
    "COUNTY TREASURER": ("Treasurer (4 Year)", ""),
    "COUNTY CORONER": ("Coroner (4 Year)", ""),
    "TAX COLLECTOR 1-2": ("Tax Collector (2 Year)", "Burnside Borough"),
    "CITY CONTROLLER DUBOIS CITY CONTROLLER": ("City Controller (4 Year)",
                                               "Dubois City"),
}

UNNUMBERED_OFFICE = (("AUDITOR", "Township Auditor"),
                     ("SUPERVISOR", "Township Supervisor"),
                     ("MEMBER OF COUNCIL", "Borough Council"))


def normalize_office(line: str):
    """(office, district) for a party-stripped primary contest header.

    Offices whose term is not printed in the header come back WITHOUT a
    "(N Year)" suffix; the term is attached by _apply_term().
    """
    line = re.sub(r"\s+", " ", line).strip()

    if line in NO_MUNI_HEADERS:
        return NO_MUNI_HEADERS[line]

    if not re.search(r"\d+-\d+", line):
        for prefix, office in UNNUMBERED_OFFICE:
            if line.startswith(prefix + " "):
                rest = line[len(prefix):].strip()
                rest = re.sub(r"^MULTI\s+", "", rest)
                return (office, expand_muni_flexible(base._clean_muni(rest)))

    office, district = base.normalize_office(line)
    # The primary spells the Philipsburg/Osceola school district without
    # the slash and the third Clearfield ward "CLEARFIED".
    district = district.replace("Philipsburg Osceola", "Philipsburg/Osceola")
    district = district.replace("Clearfied", "Clearfield")
    return (office, district)


def _apply_term(office, district, idx, warnings, where=""):
    """Attach the idx-th (descending) term of the municipality's set.

    The report orders each office family's contests by descending term
    (6-year block, then 4-year, then 2-year -- corroborated by the numbered
    "MULTI" headers and the file's block structure), so the idx-th
    occurrence of an unnumbered header gets the idx-th largest term of the
    municipality's general-file term set. Municipalities whose set holds
    more terms than the primary printed sit in the highest-term block,
    which the descending rule reproduces.
    """
    if re.search(r"\(\d+ Year\)$", office) or office not in NEEDS_TERM_OFFICES:
        return (office, district)
    years = GENERAL_TERMS.get((office, district))
    if not years:
        warnings.append(f"{where}: no general-file term set for "
                        f"{office} | {district}")
        return (office, district)
    ordered = sorted(years, reverse=True)
    if idx >= len(ordered):
        warnings.append(f"{where}: {office} | {district} occurrence {idx} "
                        f"exceeds general terms {sorted(years)}")
        idx = len(ordered) - 1
    return (f"{office} ({ordered[idx]} Year)", district)


def prettify_precinct(name: str) -> str:
    s = base.prettify_precinct(name)
    # the primary source spells the 3rd ward "CLEARFIED"
    return s.replace("Clearfied", "Clearfield")


# ---------------------------------------------------------------------------
# Column handling: every value row carries SEVEN numbers (total, election-day
# "all", election-day "polls", absentee/mail, provisional, plus two
# always-zero columns); total == col2+col3+col4+col5 on every row. The
# election-day vote is emitted as col2+col3 so the breakdown sums to the
# total.
# ---------------------------------------------------------------------------

def vals7(nums):
    """(total, ed, mail, prov) from a numeric token list."""
    nums = [str(t).replace(",", "") for t in nums]
    if len(nums) >= 7:
        return (nums[0], str(int(nums[1]) + int(nums[2])),
                nums[3], nums[4])
    if len(nums) == 4:
        return tuple(nums)
    if len(nums) == 1:
        return (nums[0], "", "", "")
    return None


def new_wi():
    return {"detail": 0, "na": 0, "agg": None}


def wi_check(wi):
    """Write-in arithmetic: named details + Not Assigned == aggregate row."""
    if wi["agg"] is None:
        return None
    if wi["detail"] + wi["na"] == wi["agg"]:
        return None
    return (wi["detail"], wi["na"], wi["agg"])


# ---------------------------------------------------------------------------
# Contest mapping (party prefix -> party column), shared by both modes.
# Occurrences are counted per (party, raw office, raw district) BEFORE the
# term is attached.
# ---------------------------------------------------------------------------

def map_contest(header, occ, warnings, where=""):
    m = PARTY_HDR.match(header.strip())
    party, stripped = "", header.strip()
    if m:
        party, stripped = m.group(1), m.group(2)
    raw_office, district = normalize_office(stripped)
    occ[(party, raw_office, district)] += 1
    office, district = _apply_term(raw_office, district,
                                   occ[(party, raw_office, district)] - 1,
                                   warnings, f"{where} {party} {stripped!r}")
    return (office, district, party)


# ---------------------------------------------------------------------------
# County summary (modeled on pa_2023_summary_common.parse_esr, which must
# not be edited; the party-prefixed contest headers and the 7-column value
# rows of this report required a copy).
# ---------------------------------------------------------------------------

def split_candidate_primary(tokens):
    """(head, party, nums) for a candidate row. No party token is printed
    in this report (the contest header carries it), but a stray one is
    stripped the way pa_2023_summary_common.split_candidate does."""
    head = []
    for t in tokens:
        tt = t.replace(",", "")
        if re.fullmatch(r"\d+", tt) or re.fullmatch(r"\d+(?:\.\d+)?%", tt):
            break
        head.append(t)
    party = ""
    if len(head) >= 2 and head[-1].upper().rstrip(".,") in (
            "DEM", "REP", "D/R", "DEM/REP"):
        party = head[-1].upper().rstrip(".,")
        head = head[:-1]
    rest = tokens[len(head) + (1 if party else 0):]
    return head, party, nums_from(rest)


def parse_county(text_path, warnings):
    lines = [l.rstrip() for l in open(text_path, encoding="utf-8")]

    rows = []
    contests = []
    mismatches = []
    wi_mismatches = []
    party_rv = {}
    party_bc = {}
    seen_stats = False
    rv = None
    bc = None
    occ = defaultdict(int)

    office = None
    district = ""
    party = ""
    cand_sum = [0, 0, 0, 0]
    wi = new_wi()

    # contest headers: the non-empty line above each "Vote For N"
    header_lines = set()
    for k, l in enumerate(lines):
        if re.match(r"^vote for \d+$", l.strip(), re.I):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                header_lines.add(j)

    def emit(o, d, p, c, total, ed="", mi="", pr=""):
        rows.append([COUNTY, o, d, p, c, str(total), str(ed), str(mi),
                     str(pr)])

    def accumulate(v):
        cand_sum[0] += int(v[0])
        for k in (1, 2, 3):
            cand_sum[k] += int(v[k] or 0)

    i = 0
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
                if re.match(r"^registered voters - total\b", s, re.I):
                    n = nums_from(s.split())
                    rv = int(n[0]) if n else None
                elif re.match(r"^registered voters - \w", s, re.I):
                    toks = s.split()
                    n = nums_from(toks[3:]) if len(toks) > 3 else []
                    if len(n) == 1:
                        party_rv[toks[3].upper()] = int(n[0])
                elif re.match(r"^ballots cast - total\b", s, re.I):
                    bc = vals7(nums_from(s.split()))
                elif re.match(r"^ballots cast - (dem|rep|non)", s, re.I):
                    toks = s.split()
                    n = nums_from(toks[3:]) if len(toks) > 3 else []
                    if n:
                        party_bc[toks[3].upper()] = vals7(n)
                elif re.match(r"^ballots cast - blank\b", s, re.I):
                    v = vals7(nums_from(s.split()))
                    if v:
                        emit("Ballots Cast - Blank", "", "", "", *v)
                elif PARTY_HDR.match(s) or re.match(
                        r"^(justice|judge|county|district)\b", s, re.I):
                    break  # first contest header
                i += 1
            continue

        # -- contest header: the line just above "Vote For N" ------------------
        if re.match(r"^vote for \d+$", stripped, re.I):
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                office, district, party = map_contest(lines[j].strip(), occ,
                                                      warnings, "county")
                contests.append((office, district, party))
                cand_sum = [0, 0, 0, 0]
                wi = new_wi()
            i += 1
            continue

        if i in header_lines:
            i += 1
            continue

        if esr_is_junk(stripped):
            i += 1
            continue

        if not any(c.isdigit() for c in stripped):
            i += 1
            continue
        if office is None:
            i += 1
            continue

        tokens = stripped.split()
        nums = nums_from(tokens[1:])
        lead = tokens[0].upper().rstrip(":") if tokens else ""

        if upper.startswith("WRITE-IN TOTALS"):
            v = vals7(nums_from(tokens))
            if v:
                # the party column carries the contest party, as in the
                # other 2023 primary county summaries (Adams/Blair/Berks)
                emit(office, district, party, "Write-ins", *v)
                accumulate(v)
                wi["agg"] = int(v[0])
        elif upper.startswith("WRITE-IN:"):
            # named / scattered detail rows: folded into the aggregate row
            n = nums_from(tokens)
            if n:
                wi["detail"] += int(n[0])
        elif re.match(r"^not assigned", stripped, re.I):
            # "Not Assigned" (scattered) is part of the aggregate
            n = nums_from(tokens)
            if n:
                wi["na"] += int(n[0])
        elif re.match(r"^total votes cast", stripped, re.I):
            if nums:
                tvc = int(nums[0])
                if cand_sum[0] != tvc:
                    mismatches.append(((office, district, party),
                                       cand_sum[0], tvc))
                bad = wi_check(wi)
                if bad:
                    wi_mismatches.append(((office, district, party), *bad))
        elif re.match(r"^contest totals", stripped, re.I):
            pass  # includes undervotes; not used for reconciliation
        elif re.match(r"^over ?votes", stripped, re.I):
            v = vals7(nums)
            if v:
                emit(office, district, party, "Overvotes", *v)
        elif re.match(r"^under ?votes", stripped, re.I):
            v = vals7(nums)
            if v:
                emit(office, district, party, "Undervotes", *v)
        elif lead in ("YES", "NO"):
            v = vals7(nums)
            if v:
                emit(office, district, "", lead.capitalize(), *v)
                accumulate(v)
        else:
            head, cand_party, allnums = split_candidate_primary(tokens)
            name = re.sub(r"\s+", " ", " ".join(head)).strip()
            if name and allnums:
                if cand_party and cand_party != party:
                    warnings.append(
                        f"county: candidate row party {cand_party} != header "
                        f"party {party} for {name!r} under "
                        f"{office} | {district}")
                v = vals7(allnums)
                if v is None:
                    warnings.append(f"county: odd token count {allnums} "
                                    f"for {name!r}")
                else:
                    emit(office, district, party, name, *v)
                    accumulate(v)
        i += 1

    if rv is not None:
        rows.append([COUNTY, "Registered Voters", "", "", "", str(rv),
                     "", "", ""])
    if bc:
        rows.append([COUNTY, "Ballots Cast", "", "", "", *bc])
    return (rows, contests, mismatches, wi_mismatches, party_rv, party_bc)


# ---------------------------------------------------------------------------
# Precinct report (modeled on electionware_txt.parse_segment / its
# precinct_segments, which must not be edited; this copy adds the contest
# header party, the 7-column value rows and the per-contest totals check).
# ---------------------------------------------------------------------------

# statistics rows that are per-party counts: captured, never emitted
PARTY_STATS = re.compile(
    r"^(Registered Voters|Ballots Cast) - "
    r"(DEMOCRATIC|REPUBLICAN|NONPARTISAN)\b", re.I)


def precinct_segments(lines, cfg):
    markers = [i for i, ln in enumerate(lines) if STAT_RE.match(ln.strip())]
    if not markers:
        raise RuntimeError("No 'Statistics' markers found; wrong file format?")
    bounds = markers + [len(lines)]
    for k, start in enumerate(markers):
        name = find_precinct_name(lines, start, cfg)
        seg = lines[start: bounds[k + 1]]
        # Trim the next page's repeated header block (title/date/footer
        # lines and the next precinct's name) off the segment tail.
        cut = len(seg)
        for i in range(len(seg) - 1, -1, -1):
            s = seg[i].strip()
            if not s:
                continue
            if any(sub in s for sub in JUNK_SUBSTR) or FOOTER_RE.search(s):
                cut = i + 1
                break
        seg = seg[:cut]
        yield name, seg


def parse_segment(name, seg, warnings, precinct_names, occ):
    rows = []
    office = None
    district = ""
    party = ""
    last_numeric = None
    cand_sum = [0, 0, 0, 0]
    wi = new_wi()

    lines = [ln.strip() for ln in seg]
    n = len(lines)
    names = precinct_names or set()

    # Office headers: the next non-empty line is "Vote For N".
    office_header_idx = set()
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, n):
            nxt = lines[j]
            if not nxt:
                continue
            if VOTE_FOR_RE.match(nxt):
                office_header_idx.add(i)
            break

    def emit(o, d, p, c, votes, ed, mail, prov):
        rows.append({
            "county": COUNTY,
            "precinct": name,
            "office": o,
            "district": d,
            "party": p,
            "candidate": c,
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        })

    def accumulate(v):
        cand_sum[0] += int(v[0])
        for k in (1, 2, 3):
            cand_sum[k] += int(v[k] or 0)

    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue

        if STAT_RE.match(line):
            office = None
            district = ""
            party = ""
            last_numeric = None
            continue

        if idx in office_header_idx:
            office, district, party = map_contest(line, occ, warnings, name)
            cand_sum = [0, 0, 0, 0]
            wi = new_wi()
            last_numeric = None
            continue

        if VOTE_FOR_RE.match(line):
            continue
        if is_junk(line, CFG):
            continue
        if line in names:  # continuation pages repeat the precinct name
            continue

        # per-party RV / ballots-cast rows: captured by the parser, not rows
        if PARTY_STATS.match(line):
            continue

        if line.startswith("Registered Voters"):
            nums = [t.replace(",", "") for t in
                    strip_percent(trailing_numbers(line))]
            if len(nums) == 1:
                emit("Registered Voters", "", "", "", nums[0], "", "", "")
            else:
                warnings.append(f"{name}: Registered Voters unparsed: "
                                f"{line!r}")
            continue

        if line.startswith("Ballots Cast"):
            nums = [t.replace(",", "") for t in
                    strip_percent(trailing_numbers(line))]
            if not nums and last_numeric:
                nums = [t.replace(",", "") for t in
                        strip_percent(last_numeric)]
            if line.startswith(("Ballots Cast - Total",
                                "Ballots Cast-Total")):
                v = vals7(nums) if nums else None
                if v:
                    emit("Ballots Cast", "", "", "", *v)
                else:
                    warnings.append(f"{name}: Ballots Cast tokens={nums}")
            elif line.startswith(("Ballots Cast - Blank",
                                  "Ballots Cast-Blank")):
                v = vals7(nums) if nums else None
                if v:
                    emit("Ballots Cast - Blank", "", "", "", *v)
                else:
                    warnings.append(f"{name}: Ballots Cast Blank tokens="
                                    f"{nums}")
            # everything else (turnout, precincts reporting) is skipped
            continue

        if numbers_only(line):
            last_numeric = trailing_numbers(line)
            continue

        raw_tail = trailing_numbers(line)
        nums = strip_percent(raw_tail)
        head = " ".join(line.split()[: -len(raw_tail)]) if raw_tail else line

        if not nums:
            # wrapped text / column-header junk
            continue
        nums = [t.replace(",", "") for t in nums]

        if office is None:
            warnings.append(f"{name}: candidate row with no office: "
                            f"{line!r}")
            continue

        upper = head.upper()
        if upper.startswith("WRITE-IN:"):
            # named / scattered detail rows: folded into the aggregate row
            wi["detail"] += int(nums[0])
            continue
        if upper == "WRITE-IN TOTALS":
            v = vals7(nums)
            # the party column carries the contest party, as in the
            # sibling Electionware primary county summaries (Adams/Blair)
            emit(office, district, party, "Write-ins", *v)
            accumulate(v)
            wi["agg"] = int(v[0])
            continue
        if upper in ("OVERVOTES", "UNDERVOTES"):
            v = vals7(nums)
            if v:
                emit(office, district, party,
                     "Overvotes" if upper == "OVERVOTES" else "Undervotes",
                     *v)
            continue
        if upper == "TOTAL VOTES CAST":
            tvc = int(nums[0])
            if cand_sum[0] != tvc:
                warnings.append(
                    f"{name}: {office} | {district} | {party or '-'}: rows "
                    f"sum {cand_sum[0]} != Total Votes Cast {tvc}")
            bad = wi_check(wi)
            if bad:
                warnings.append(
                    f"{name}: {office} | {district} | {party or '-'}: "
                    f"write-in details {bad[0]} + not-assigned {bad[1]} != "
                    f"aggregate {bad[2]}")
            continue
        if upper == "NOT ASSIGNED":
            wi["na"] += int(nums[0])
            continue
        if upper == "CONTEST TOTALS":
            continue
        if upper in ("YES", "NO"):
            v = vals7(nums)
            emit(office, district, "", upper.capitalize(), *v)
            accumulate(v)
            continue
        if upper in CFG.skip_candidates:
            continue

        v = vals7(nums)
        if v is None:
            warnings.append(f"{name}: odd token count {nums} for {head!r}")
            continue
        emit(office, district, party, head, *v)
        accumulate(v)

    return _merge_split_aggregates(rows)


def parse_precinct(path, warnings):
    lines = txt_lines(Path(path))
    rows = []
    precinct_count = 0
    unnamed = 0
    occ = defaultdict(int)
    segs = list(precinct_segments(lines, CFG))
    all_names = {re.sub(r"\s{2,}", " ", CFG.prettify_precinct(nm)).strip()
                 for nm, _ in segs if nm}
    all_names |= {nm.strip() for nm, _ in segs if nm}
    for name, seg in segs:
        if name is None:
            unnamed += 1
            continue
        # each precinct's contest sequence restarts the term blocks
        occ = defaultdict(int)
        pretty = re.sub(r"\s{2,}", " ", CFG.prettify_precinct(name)).strip()
        rows.extend(parse_segment(pretty, seg, warnings, all_names, occ))
        precinct_count += 1
    return rows, precinct_count, warnings, unnamed


CFG = TxtConfig(
    county=COUNTY,
    normalize_office=normalize_office,
    prettify_precinct=prettify_precinct,
    extra_junk=(r"\bfebruary\b", r"\bmay\s+\d{1,2},\s+\d{4}\b"),
    party_optional=True,
)


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        sys.exit(f"Usage: {Path(sys.argv[0]).name} <input.txt|pdf> "
                 f"<output.csv>")
    src, dst = args
    tmp = None
    if src.lower().endswith(".pdf"):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as tf:
            tmp = tf.name
        subprocess.run(["pdftotext", "-layout", src, tmp], check=True)
        src = tmp

    text = Path(src).read_text(encoding="utf-8", errors="replace")
    n_stats = sum(1 for ln in text.split("\n") if STAT_RE.match(ln.strip()))
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)

    if n_stats <= 1:  # county summary
        warnings = []
        rows, contests, mismatches, wi_mismatches, party_rv, party_bc = (
            parse_county(src, warnings))
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(COUNTY_FIELDNAMES)
            w.writerows(rows)
        print(f"wrote {len(rows)} rows -> {out}")
        print(f"contests parsed: {len(contests)}")
        if mismatches:
            print(f"contest totals check: {len(mismatches)} MISMATCH(ES):")
            for (office, dist, party), a, t in mismatches:
                print(f"  {party} {office} | {dist}: rows sum {a} != "
                      f"Total Votes Cast {t}")
        else:
            print("contest totals check: all contests reconcile")
        if wi_mismatches:
            print(f"write-in arithmetic: {len(wi_mismatches)} MISMATCH(ES):")
            for (office, dist, party), d, na, agg in wi_mismatches:
                print(f"  {party} {office} | {dist}: details {d} + "
                      f"not-assigned {na} != aggregate {agg}")
        else:
            print("write-in arithmetic: all contests reconcile")
        for w in warnings:
            print(f"WARNING: {w}")
        print(f"RV per party: {party_rv}")
        print(f"BC per party: { {k: v[0] for k, v in party_bc.items()} }")
    else:  # per-precinct report
        warnings = []
        rows, precinct_count, warnings, unnamed = parse_precinct(src,
                                                                 warnings)
        with out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {len(rows)} rows across {precinct_count} precincts "
              f"-> {out}")
        if unnamed:
            print(f"skipped {unnamed} unnamed Statistics segment(s)")
        recon = [w for w in warnings if "rows sum" in w
                 or "write-in details" in w]
        if recon:
            print(f"contest totals check: {len(recon)} MISMATCH(ES):")
            for w in recon[:40]:
                print("  " + w)
            if len(recon) > 40:
                print("  ...")
        else:
            print("contest totals check: all precinct contests reconcile")
        other = [w for w in warnings if w not in recon]
        if other:
            print(f"WARNING: {len(other)} other warning(s):")
            for w in other[:40]:
                print("  " + w)
            if len(other) > 40:
                print("  ...")


if __name__ == "__main__":
    main()