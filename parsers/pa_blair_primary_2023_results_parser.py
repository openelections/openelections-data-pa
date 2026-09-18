#!/usr/bin/env python3
"""Parse Blair County PA 2023 Primary (Municipal Primary, May 16, 2023).

Sources (Electionware, pdftotext -layout text extracts; .pdf also accepted):

  * "Summary Results Report" (county summary, one STATISTICS block, no
    per-precinct data) -> county-level CSV (9 columns).
  * "Precinct Summary Results Report" (one STATISTICS block per precinct,
    93 precincts, multi-page blocks without repeated Statistics markers)
    -> precinct CSV (10 columns).

The report is auto-detected from the input text ("PRECINCT SUMMARY RESULTS
REPORT" appears in the precinct report, only "SUMMARY RESULTS REPORT" in
the county report).

Primary structure: the contest header carries the party as a prefix
("DEM JUSTICE OF THE SUPREME COURT", "REP COUNTY COMMITTEE Altoona Ward 1");
``map_contest_primary`` strips it into the party slot and maps the office
portion to the conventions of the county's 2023 general file
(``2023/counties/20231107__pa__general__blair__precinct.csv``):

  - candidates keep their printed ALL-CAPS casing;
  - "COMMISSIONER" -> County Commissioner, "REGISTER OF WILLS & RECORDER
    OF DEEDS" -> Register and Recorder;
  - "SUPERVISOR/AUDITOR [2YR|4YR|6YR] <TWP>" -> Township Supervisor/Auditor
    [(N Year)] with "Twp" expanded to "Township";
  - "COUNCIL <BORO>" -> Borough Council with "Boro" expanded;
  - primary school-director headers put the district name AFTER the office
    ("SCHOOL DIRECTOR 2YR TYRONE"), the reverse of the general 2023 file;
    the "HOLLIDAYSURG" misspelling is normalized to Hollidaysburg Area;
  - "REP COUNTY COMMITTEE <area>" committee races are kept (per the task
    brief), as office "Member of Republican County Committee <area>" with
    empty district (Lebanon-2024 convention), so each committee district
    stays distinct;
  - "LIQUOR LICENSE QUESTION DUNCANSVILLE BORO" -> office "Liquor License
    Question", district "Duncansville Borough", Yes/No rows.

Write-in detail ("Write-In: NAME n n n n", in the with-write-ins reports)
is folded into the aggregate; the aggregate "Write-ins" row carries the
CONTEST party so DEM and REP contests of the same office stay distinct.

Usage:
    python parsers/pa_blair_primary_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>
"""

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    TERM_TOKEN_RE,
    VOTE_FOR_RE,
    expand_muni_flexible,
)
from electionware_txt import (  # noqa: E402
    TxtConfig,
    is_junk,
    precinct_segments,
    strip_percent,
)
from pa_2023_summary_common import (  # noqa: E402
    Rows,
    is_junk as esr_is_junk,
    nums_from,
    text_from_pdf,
    vals_from,
    write_csv,
)

COUNTY = "Blair"

COUNTY_FIELDNAMES = ["county", "office", "district", "party", "candidate",
                     "votes", "election_day", "mail", "provisional"]
PRECINCT_FIELDNAMES = ["county", "precinct"] + COUNTY_FIELDNAMES[1:]

PARTY_PREFIX_RE = re.compile(r"^(DEM|REP)\s+(.+)$", re.IGNORECASE)

# County offices (party prefix already stripped).
EXACT_OFFICES = {
    "COMMISSIONER": ("County Commissioner", ""),
    "CONTROLLER": ("Controller", ""),
    "TREASURER": ("Treasurer", ""),
    "CORONER": ("Coroner", ""),
    "REGISTER OF WILLS & RECORDER OF DEEDS": ("Register and Recorder", ""),
    "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
    "JUDGE OF THE SUPERIOR COURT": ("Judge of the Superior Court", ""),
    "JUDGE OF THE COMMONWEALTH COURT": ("Judge of the Commonwealth Court", ""),
    "JUDGE OF THE COURT OF COMMON PLEAS": ("Judge of the Court of Common Pleas", ""),
}

# Township / borough names exactly as the county's 2023 general file prints
# them.
TWPS = {
    "ALLEGHENY TWP": "Allegheny Township",
    "ANTIS TWP": "Antis Township",
    "BLAIR TWP": "Blair Township",
    "CATHARINE TWP": "Catharine Township",
    "FRANKSTOWN TWP": "Frankstown Township",
    "FREEDOM TWP": "Freedom Township",
    "GREENFIELD TWP": "Greenfield Township",
    "HUSTON TWP": "Huston Township",
    "JUNIATA TWP": "Juniata Township",
    "LOGAN TWP": "Logan Township",
    "NORTH WOODBURY TWP": "North Woodbury Township",
    "SNYDER TWP": "Snyder Township",
    "TAYLOR TWP": "Taylor Township",
    "TYRONE TWP": "Tyrone Township",
    "WOODBURY TWP": "Woodbury Township",
}

BOROS = {
    "BELLWOOD BORO": "Bellwood Borough",
    "DUNCANSVILLE BORO": "Duncansville Borough",
    "MARTINSBURG BORO": "Martinsburg Borough",
    "NEWRY BORO": "Newry Borough",
    "ROARING SPRING BORO": "Roaring Spring Borough",
    "TUNNELHILL BORO": "Tunnelhill Borough",
    "TYRONE BORO": "Tyrone Borough",
    "WILLIAMSBURG BORO": "Williamsburg Borough",
}

# School-district bases ("SCHOOL DIRECTOR <NAME> [ - <SUB>]"), matching the
# district values of the county's 2023 general file.
SCHOOL_BASES = {
    "ALTOONA": "Altoona Area",
    "HOLLIDAYSURG": "Hollidaysburg Area",  # source misspells Hollidaysburg
    "SPRING COVE": "Spring Cove",
    "TYRONE": "Tyrone Area",
    "BELLWOOD ANTIS": "Bellwood Antis",
    "CLAYSBURG KIMMEL": "Claysburg Kimmel",
    "PENN CAMBRIA": "Penn Cambria",
    "WILLIAMSBURG": "Williamsburg Community",
}

PROBLEMS = []


def _muni(rest: str) -> str:
    """Expand a TWP/BORO tail exactly as the county's general file does."""
    key = rest.strip().upper()
    if key in TWPS:
        return TWPS[key]
    if key in BOROS:
        return BOROS[key]
    return expand_muni_flexible(rest.strip())


def _termed(base: str, rest: str) -> tuple[str, str]:
    """'SUPERVISOR/AUDITOR' tails: optional '2YR' term token + municipality."""
    rest = rest.strip()
    m = re.match(r"^(\d)\s*YR\s+(.+)$", rest)
    years = None
    if m:
        years, rest = m.group(1), m.group(2).strip()
    office = base
    if years:
        office += f" ({years} Year)"
    return (office, _muni(rest))


def school_director_primary(rest: str) -> tuple[str, str]:
    """'SCHOOL DIRECTOR [2YR] <DISTRICT> [- <SUB>]' (district-name-last
    orientation; the general 2023 file put the name first)."""
    tokens = rest[len("SCHOOL DIRECTOR"):].strip().split()
    years = None
    if tokens:
        tm = TERM_TOKEN_RE.match(tokens[0])
        if tm:
            years = tm.group(1)
            tokens = tokens[1:]
    remainder = re.sub(r"\s+", " ", " ".join(tokens)).strip(" -")
    upper = remainder.upper()
    if upper in SCHOOL_BASES:
        district = SCHOOL_BASES[upper]
    elif " - " in remainder:
        base, sub = [p.strip() for p in remainder.split(" - ", 1)]
        basename = SCHOOL_BASES.get(base.upper())
        if not basename:
            raise ValueError(f"unrecognized school district {base!r}")
        district = f"{basename} - {_muni(sub)}"
    else:
        raise ValueError(f"unrecognized school director header {remainder!r}")
    office = "School Director"
    if years:
        office += f" ({years} Year)"
    return (office, district)


def map_contest_primary(line: str) -> tuple[str, str, str]:
    """Contest header -> (office, district, party)."""
    line = re.sub(r"\s+", " ", line).strip()
    m = PARTY_PREFIX_RE.match(line)
    if m:
        party = m.group(1).upper()
        rest = m.group(2).strip()
    else:
        party = ""
        rest = line
    u = rest.upper()

    if u in EXACT_OFFICES:
        office, district = EXACT_OFFICES[u]
    elif u.startswith("MAGISTERIAL DISTRICT JUDGE"):
        dm = re.match(
            r"^MAGISTERIAL DISTRICT JUDGE\s+(?:DISTRICT\s+)?(\S+)$", u)
        if not dm:
            raise ValueError(f"unrecognized MDJ header {line!r}")
        office, district = "Magisterial District Judge", dm.group(1)
    elif u.startswith("SCHOOL DIRECTOR"):
        office, district = school_director_primary(u)
    elif u.startswith("MAYOR "):
        office, district = "Mayor", _muni(u[len("MAYOR "):])
    elif u.startswith("CITY COUNCIL"):
        office, district = "City Council", _muni(u[len("CITY COUNCIL "):])
    elif u.startswith("COUNCIL "):
        office, district = "Borough Council", _muni(u[len("COUNCIL "):])
    elif u.startswith("SUPERVISOR"):
        office, district = _termed("Township Supervisor", u[len("SUPERVISOR"):])
    elif u.startswith("AUDITOR"):
        office, district = _termed("Township Auditor", u[len("AUDITOR"):])
    elif u.startswith("TAX COLLECTOR"):
        office, district = "Tax Collector", _muni(u[len("TAX COLLECTOR "):])
    elif u.startswith("COUNTY COMMITTEE"):
        area = expand_muni_flexible(rest[len("COUNTY COMMITTEE"):].strip())
        wing = "Democratic" if party == "DEM" else "Republican"
        office, district = f"Member of {wing} County Committee {area}", ""
    elif u.startswith("LIQUOR LICENSE QUESTION"):
        office, district = ("Liquor License Question",
                            _muni(u[len("LIQUOR LICENSE QUESTION"):]))
    else:
        raise ValueError(f"unrecognized contest header {line!r}")
    return (office, district, party)


def _trailing_total_tokens(tokens):
    """Trailing integer tokens (commas stripped), mirroring the county
    summary's TOTAL/ED/Mail/Provisional column order."""
    out = []
    for t in reversed(tokens):
        t2 = t.replace(",", "")
        if not re.fullmatch(r"\d+", t2):
            break
        out.append(t2)
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# County summary ("Summary Results Report").
# ---------------------------------------------------------------------------


def parse_county_summary(text_path: str, county: str):
    with open(text_path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]

    R = Rows(county)
    current = None
    rv = None
    bc = None
    seen_stats = False

    header_idx = set()
    for k, l in enumerate(lines):
        if re.match(r"^\s*vote for \d+\s*$", l, re.I):
            j = k - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0:
                header_idx.add(j)

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        if "STATISTICS" in stripped.upper() and not seen_stats:
            seen_stats = True
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if re.match(r"^registered voters - total", s, re.I):
                    n = nums_from(s.split())
                    rv = int(n[0]) if n else None
                elif re.match(r"^ballots cast - total", s, re.I):
                    bc = nums_from(s.split())
                elif re.match(r"^ballots cast - blank", s, re.I):
                    n = nums_from(s.split())
                    if n:
                        R.add_meta("Ballots Cast - Blank", n[0],
                                   n[1] if len(n) > 1 else "",
                                   n[2] if len(n) > 2 else "",
                                   n[3] if len(n) > 3 else "")
                # per-party Registered Voters / Ballots Cast lines: skipped
                if i in header_idx:
                    break
                i += 1
            continue

        if i in header_idx:
            current = map_contest_primary(stripped)
            R.contests.append(current)
            i += 1
            continue

        if re.match(r"^\s*vote for \d+\s*$", stripped, re.I):
            i += 1
            continue

        if esr_is_junk(stripped):
            i += 1
            continue

        if current is None or not any(c.isdigit() for c in stripped):
            i += 1
            continue

        tokens = stripped.split()
        nums = nums_from(tokens[1:])
        lead = tokens[0].upper().rstrip(":") if tokens else ""

        if stripped.upper().startswith("WRITE-IN TOTALS"):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], current[2], "Write-ins",
                  total, ed, mi, pr)
        elif stripped.upper().startswith("WRITE-IN:"):
            pass  # named write-in detail: folded into the aggregate
        elif re.match(r"^not assigned", stripped, re.I):
            pass
        elif lead in ("YES", "NO"):
            total, ed, mi, pr = vals_from(nums)
            R.add(current[0], current[1], "", lead.capitalize(),
                  total, ed, mi, pr)
        else:
            tail = _trailing_total_tokens(tokens)
            # keep the printed ALL-CAPS casing (the county's general 2023
            # convention); only collapse whitespace
            name = re.sub(r"\s+", " ", " ".join(tokens[:len(tokens) - len(tail)])).strip()
            if name and tail:
                total, ed, mi, pr = vals_from(tail)
                R.add(current[0], current[1], current[2], name,
                      total, ed, mi, pr)
        i += 1

    if rv is not None:
        R.add_meta("Registered Voters", rv)
    if bc:
        R.add_meta("Ballots Cast", bc[0],
                   bc[1] if len(bc) > 1 else "",
                   bc[2] if len(bc) > 2 else "",
                   bc[3] if len(bc) > 3 else "")
    return R


# ---------------------------------------------------------------------------
# Precinct report (text-extract variant of electionware_txt with
# primary-style headers: the party lives on the office header line).
# ---------------------------------------------------------------------------

# The primary txt has "Primary Election" / "May 16, 2023" page-header lines
# the shared junk list doesn't know about.
PRIMARY_TXT_CFG = TxtConfig(
    county=COUNTY,
    normalize_office=lambda s: ("", ""),
    extra_junk=(r"(?i)\bprimary election\b",
                r"(?i)\bmay\s+\d{1,2},\s+\d{4}\b",
                r"(?i)\bgeneral primary election\b"),
)

AGG_CANDIDATES = ("Write-ins", "Overvotes", "Undervotes")


def parse_precinct_segment(precinct: str, seg: list[str],
                           precinct_names: set):
    rows = []
    warnings = []
    current_office = None
    current_district = ""
    current_party = ""
    lines = [ln.strip() for ln in seg]
    n = len(lines)

    # Office headers: next non-empty line is "Vote For N".
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

    def emit(office, district, party, candidate, nums):
        if len(nums) == 4:
            votes, ed, mail, prov = nums
        elif len(nums) == 1:
            votes, ed, mail, prov = nums[0], "", "", ""
        else:
            warnings.append(f"{precinct}: odd token count {nums} for "
                            f"{candidate!r} under {office!r}")
            return
        rows.append({
            "county": COUNTY,
            "precinct": precinct,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": votes,
            "election_day": ed,
            "mail": mail,
            "provisional": prov,
        })

    for idx, raw in enumerate(lines):
        line = raw
        if not line:
            continue

        if line.upper().startswith("STATISTICS"):
            current_office = None
            current_district = ""
            current_party = ""
            continue

        if idx in office_header_idx:
            office, district, party = map_contest_primary(line)
            current_office, current_district, current_party = \
                office, district, party
            continue

        if VOTE_FOR_RE.match(line):
            continue

        if is_junk(line, PRIMARY_TXT_CFG):
            continue

        # Continuation pages repeat nothing here, but a known precinct
        # name is never a data row.
        if line in precinct_names:
            continue

        # Statistics-block labels.
        if line.startswith("Registered Voters"):
            tail = strip_percent(_trailing_total_tokens(line.split()))
            if line.startswith("Registered Voters - Total"):
                if len(tail) == 1:
                    emit("Registered Voters", "", "", "", tail)
                else:
                    warnings.append(
                        f"{precinct}: Registered Voters unparsed: {line!r}")
            # per-party Registered Voters lines: skipped
            continue
        if line.startswith("Ballots Cast"):
            tail = strip_percent(_trailing_total_tokens(line.split()))
            if line.startswith("Ballots Cast - Total"):
                if len(tail) == 4:
                    emit("Ballots Cast", "", "", "", tail)
                elif len(tail) == 1:
                    emit("Ballots Cast", "", "", "", tail)
                else:
                    warnings.append(
                        f"{precinct}: Ballots Cast tokens={tail}")
            elif line.startswith("Ballots Cast - Blank"):
                if len(tail) in (4, 1):
                    emit("Ballots Cast - Blank", "", "", "", tail)
                else:
                    warnings.append(
                        f"{precinct}: Ballots Cast - Blank tokens={tail}")
            # per-party Ballots Cast lines: skipped
            continue
        if line.startswith("Voter Turnout"):
            continue

        tail = _trailing_total_tokens(line.split())
        nums = strip_percent(tail)
        if not nums:
            continue  # column-header junk / wrapped text
        head = " ".join(line.split()[:len(line.split()) - len(tail)])

        if current_office is None:
            warnings.append(f"{precinct}: candidate row with no office: {line!r}")
            continue

        upper = head.upper()
        if upper.startswith("WRITE-IN:"):
            continue  # named write-in detail: folded into the aggregate
        if upper == "WRITE-IN TOTALS":
            emit(current_office, current_district, current_party,
                 "Write-ins", nums)
            continue
        if upper == "NOT ASSIGNED" or upper == "TOTAL VOTES CAST" \
                or upper == "CONTEST TOTALS":
            continue
        if upper in ("YES", "NO"):
            emit(current_office, current_district, "", upper.capitalize(),
                 nums)
            continue
        if upper == "OVERVOTES" or upper == "UNDERVOTES":
            emit(current_office, current_district, current_party,
                 upper.capitalize(), nums)
            continue

        emit(current_office, current_district, current_party, head, nums)
        continue

    return rows, warnings


def merge_primary(rows):
    """Party-aware variant of the shared _merge_split_aggregates: partial
    Write-ins/Over/Undervotes aggregates within a contiguous run of the
    same (office, district, party) are summed into the first occurrence."""
    merged = []
    agg_index = {}
    current_key = None
    for row in rows:
        key = (row["office"], row["district"], row["party"])
        if key != current_key:
            current_key = key
            agg_index = {}
        akey = key + (row["candidate"],)
        if row["candidate"] in AGG_CANDIDATES and akey in agg_index:
            prev = merged[agg_index[akey]]
            for f in ("votes", "election_day", "mail", "provisional"):
                prev[f] = str(int(prev.get(f) or 0) + int(row.get(f) or 0))
        else:
            merged.append(row)
            if row["candidate"] in AGG_CANDIDATES:
                agg_index[akey] = len(merged) - 1
    return merged


def run_precinct(src, dst):
    lines = open(src, encoding="utf-8", errors="replace").read().split("\n")
    cfg = PRIMARY_TXT_CFG
    warnings = []
    precinct_count = 0
    all_rows = []
    merged_all = []
    segs = list(precinct_segments(lines, cfg))
    all_names = {re.sub(r"\s{2,}", " ", nm).strip() for nm, _ in segs if nm}
    all_names |= {nm.strip() for nm, _ in segs if nm}
    for name, seg in segs:
        if name is None:
            continue
        pretty = re.sub(r"\s{2,}", " ", name).strip()
        rows, warns = parse_precinct_segment(pretty, seg, all_names)
        warnings.extend(warns)
        all_rows.extend(merge_primary(rows))
        precinct_count += 1
    return _write_precinct(dst, all_rows, precinct_count, warnings)


def _write_precinct(dst, rows, precinct_count, warnings):
    out_path = Path(dst)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PRECINCT_FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"parsed {precinct_count} precincts")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for w_ in warnings[:40]:
            print("  " + w_)
        if len(warnings) > 40:
            print("  ...")
    return len(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    if len(args) != 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.txt|input.pdf> <output.csv>")
    src, dst = args
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    with open(src, encoding="utf-8", errors="replace") as fh:
        head = fh.read(4000)
    is_precinct = "PRECINCT SUMMARY RESULTS REPORT" in head.upper()

    if is_precinct:
        n = run_precinct(src, dst)
        print(f"wrote {n} precinct rows -> {dst}")
    else:
        R = parse_county_summary(src, COUNTY)
        n = write_csv(dst, R)
        print(f"wrote {n} county-level rows -> {dst}")
        print(f"contests parsed: {len(R.contests)}")
    for p in PROBLEMS:
        print("PROBLEM:", p)


if __name__ == "__main__":
    main()