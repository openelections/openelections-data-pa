#!/usr/bin/env python3
"""Parse Cumberland County PA 2022 General Primary (May 17, 2022) precinct results.

Source: per-party Electionware "Precinct Results Report" PDFs
  * "Cumberland PA 2022 Primary Precinct - Democratic.pdf" (generated 05/23/2022)
  * "Cumberland PA 2022 Primary Precinct - Republican.pdf" (generated 06/06/2022,
    i.e. AFTER the June GOP U.S. Senate recount -- unlike most counties'
    May-dated files, the Republican report here already carries the
    post-recount certified counts, verified against the Cumberland PA 2022
    Primary Summary - Republican PDF (06/06/2022) and the DOS ENR anchor).

Format notes (Electionware family, but a standalone implementation):

  * Candidate rows carry NO party prefix in this report; the ballot party
    sits on the CONTEST title ("DEM UNITED STATES SENATOR").  The shared
    electionware_precinct_np.py engine expects party prefixes on candidate
    rows, so per the brief this is a standalone parser for this county/year.
  * Per-precinct blocks start with the precinct name followed by a
    " STATISTICS" marker; a precinct continues across page breaks (the page
    header repeats the precinct name).  Contest headers repeat only on the
    page where the contest begins; continuation rows inherit the open contest.
  * Statistics layout quirk: "Ballots Cast - Total" and
    "Ballots Cast - DEMOCRATIC/REPUBLICAN" print their four numbers
    (TOTAL / Election Day / Mail / Provisional) on the *preceding* line;
    "Registered Voters - Total" prints the value on the label line.
  * Registered Voters / Ballots Cast are emitted once per precinct as
    party-empty rows (2022 convention: cf.
    20221108__pa__general__cumberland__precinct.csv).  Both source files carry
    identical RV/BC totals; they are taken from the DEMOCRATIC file only and
    the REPUBLICAN file's copies are compared at parse time.  The per-party
    "Registered Voters - DEMOCRATIC/REPUBLICAN" and per-party Ballots Cast
    rows are not recorded (cf. Cambria 2022 primary parser).
  * Contest coverage: every precinct reports U.S. Senate / Governor /
    Lieutenant Governor / State Committee; the State Senate race is reported
    only for the 86 SD-34 precincts (SD-31 precincts have no section,
    matching the county's own 2022 general file and the DOS anchor, which
    report no SD-31); NORTH NEWTON has BOTH a CD-10 and a CD-13 section
    (split precinct); CD-13 appears with no named candidate on the DEM ballot
    (write-ins only).  Contests the source doesn't report are omitted.
  * Per-precinct committee races ("DEM <municipality> COMMITTEEPERSON",
    "REP <municipality> COMMITTEEMAN/COMMITTEEWOMAN") keep the municipality
    in the office name ("Committeeperson Camp Hill 4"), district empty.
  * Write-In Totals rows are emitted as candidate "Write Ins" WITH the
    contest's party (party-less write-in rows collide across parties in the
    duplicate_entries test).

Usage:
    python pa_cumberland_primary_2022_results_parser.py <dem.pdf> <rep.pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "mail",
    "provisional",
]

COUNTY = "Cumberland"

VOTE_TAIL_RE = re.compile(r"^(.*?)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$")
NUMBERS_ONLY_RE = re.compile(r"^([\d,]+)\s+([\d,]+)\s+([\d,]+)\s+([\d,]+)$")
VOTE_FOR_RE = re.compile(r"^Vote For\s+(\d+)$")
PRECINCT_NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .#&'/-]*[A-Z0-9.]$")
SMALL_WORDS = {"and", "for", "of", "the"}

# Page furniture that must never be read as data.
FURNITURE_RES = [
    re.compile(r"^Precinct Results Report\b"),
    re.compile(r"^Summary Results Report\b"),
    re.compile(r"^2022 GENERAL PRIMARY\b"),
    re.compile(r"^May 17, 2022\b"),
    re.compile(r"^Precinct Report - (Dem|Rep) -\s*\d"),
    re.compile(r"^Summary Report - (Dem|Rep) -\s*\d"),
    re.compile(r"^Report generated with Electionware\b"),
]

CONGRESS_RE = re.compile(r"^REPRESENTATIVE IN CONGRESS\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$", re.I)
STATE_SENATE_RE = re.compile(r"^SENATOR IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$", re.I)
STATE_HOUSE_RE = re.compile(r"^REPRESENTATIVE IN THE GENERAL ASSEMBLY\s+(\d+)(?:ST|ND|RD|TH)?\s+DISTRICT$", re.I)
COMMITTEE_RE = re.compile(r"(COMMITTEEPERSON|COMMITTEEMAN|COMMITTEEWOMAN)\b", re.I)
STATE_CONTESTS = {
    "UNITED STATES SENATOR": ("U.S. Senate", ""),
    "GOVERNOR": ("Governor", ""),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", ""),
}


def _stats_value_after(label: str, line: str, n: int, last_numeric):
    """Numeric values for a statistics label: printed after the label on the
    same line, or (Cumberland's layout quirk) on the line before it."""
    rest = line[len(label):]
    tokens = re.findall(r"[\d,]+", rest)
    vals = [int(t.replace(",", "")) for t in tokens]
    if len(vals) > n:
        # e.g. a trailing turnout percentage glued on; keep the first n
        vals = vals[:n]
    if len(vals) == n:
        return vals
    if not vals and last_numeric is not None and len(last_numeric) == n:
        return list(last_numeric)
    return None


def _title_case_local(s: str) -> str:
    return " ".join(_office_token(t) for t in s.split())


def _office_token(w: str) -> str:
    capped = _cap(w)
    if capped.upper() == "TWP" or capped.upper() == "MT":
        return capped.title()
    return capped


def _cap_name(head: str) -> str:
    """Title-case an ALL-CAPS candidate name, preserving initials."""
    return " ".join(_cap(t) for t in head.split())


COL_HEADER_TOKEN_RE = re.compile(r"^(TOTAL|ELECTION|DAY|MAIL|PROVISIONAL)$", re.I)


def _is_column_header_noise(line: str) -> bool:
    """'TOTAL Election Day Mail Provisional' column strips and their fragments
    (the Electionware stats table header wraps across 3 lines)."""
    return not re.search(r"\d", line) and all(
        COL_HEADER_TOKEN_RE.match(t) for t in line.split()
    )


def _cap(w: str) -> str:
    if len(w) >= 3 and w[:2].lower() == "mc":
        return "Mc" + w[2:].capitalize()
    if len(w) == 1 or (len(w) <= 3 and w.isupper() and not re.search(r"[AEIOUY]", w)):
        return w  # initials like "J", "JL"
    return "-".join(_part(p) for p in w.split("-"))


def _part(p: str) -> str:
    return p.capitalize()


def _cap_name(head: str) -> str:
    """Title-case an ALL-CAPS candidate name, preserving initials."""
    return " ".join(_cap(t) for t in head.split())


def parse_contest_title(raw: str):
    """Contest title -> (office, district, party, is_local) or None."""
    title = re.sub(r"\s+", " ", raw.strip())
    m = re.match(r"^(MEMBER OF THE)(?:\s+(DEMOCRATIC|REPUBLICAN)\s+STATE COMMITTEE)$", title, re.I)
    if m:
        party = "DEM" if m.group(2).upper().startswith("DEM") else "REP"
        return (f"Member of {m.group(2).capitalize()} State Committee", "", party, False)

    m = re.match(r"^(DEM|REP)\s+(.+)$", title)
    if not m:
        return None
    party = m.group(1)
    rest = m.group(2).strip()
    key = rest.upper()
    if key in STATE_CONTESTS:
        return (STATE_CONTESTS[key][0], "", party, False)
    for rx, office in (
        (CONGRESS_RE, "U.S. House"),
        (STATE_SENATE_RE, "State Senate"),
        (STATE_HOUSE_RE, "State House"),
    ):
        d = rx.match(rest)
        if d:
            return (office, d.group(1), party, False)
    cm = COMMITTEE_RE.search(rest)
    if cm and cm.end(1) == len(rest):
        muni = rest[: cm.start(1)].strip()
        return (f"{_title_case_local(cm.group(1).lower())}"
                + (f" {_title_case_local(muni)}" if muni else ""), "", party, True)
    return None


def parse_report(lines, county):
    """Parse one per-party Electionware Precinct Results Report text.

    Returns (rows, warnings, stats_totals):
      rows: contest-row list for FIELDNAMES
      warnings: unrecognized/stray lines
      stats_totals: {precinct: {'rv': int, 'bc': (votes, ed, mail, prov)}}
    """
    # Filter blanks and furniture once, remembering original order.
    kept = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if any(r.match(s) for r in FURNITURE_RES):
            continue
        kept.append(s)

    n = len(kept)
    next_idx = [None] * n
    for i in range(n - 1, -1, -1):
        next_idx[i] = i + 1 if i + 1 < n else None

    def nxt(i):
        return next_idx[i] and kept[next_idx[i]]

    rows = []
    warnings = []
    stats_totals = {}
    current = None
    office = district = party = None
    last_numeric = None

    def emit(candidate, vals):
        rows.append({
            "county": county,
            "precinct": current,
            "office": office,
            "district": district,
            "party": party,
            "candidate": candidate,
            "votes": vals[0],
            "election_day": vals[1],
            "mail": vals[2],
            "provisional": vals[3],
        })

    for i, line in enumerate(kept):
        # ---- precinct boundary -------------------------------------------
        if line == "STATISTICS":
            name = kept[i - 1] if i > 0 else "_COUNTY_"
            if name == "STATISTICS" or (name != "_COUNTY_" and not PRECINCT_NAME_RE.match(name)):
                raise RuntimeError(f"No precinct name above STATISTICS at kept[{i}]: {kept[i-3:i+1]}")
            current = re.sub(r"\s+", " ", name)
            office = district = party = None
            last_numeric = None
            continue

        # ---- open a waiting contest header --------------------------------
        if nxt(i) == "STATISTICS":
            # This line is the name of the precinct whose STATISTICS marker
            # follows on the next kept line; the STATISTICS branch handles it.
            continue
        if _is_column_header_noise(line):
            continue
        if office is not None and nxt(i) and VOTE_FOR_RE.match(nxt(i)):
            # Contest header while the previous contest has ended.
            parsed = parse_contest_title(line)
            if parsed is None:
                warnings.append(f"unrecognized contest title: {line!r} (in {current})")
                office = None
            else:
                office, district, party, _ = parsed
            last_numeric = None
            continue
        if VOTE_FOR_RE.match(line):
            if office is None:
                warnings.append(f"Vote For without a contest header in {current!r}")
            last_numeric = None
            continue
        if office is None and nxt(i) and VOTE_FOR_RE.match(nxt(i)):
            parsed = parse_contest_title(line)
            if parsed is None:
                warnings.append(f"unrecognized contest title: {line!r} (in {current})")
                office = None
            else:
                office, district, party, _ = parsed
            last_numeric = None
            continue

        # ---- statistics rows ----------------------------------------------
        m_numonly = NUMBERS_ONLY_RE.match(line)
        if m_numonly and office is None:
            last_numeric = [int(v.replace(",", "")) for v in m_numonly.groups()]
            continue

        for label in (
            "Registered Voters - Total",
            "Registered Voters - DEMOCRATIC",
            "Registered Voters - REPUBLICAN",
            "Ballots Cast - Total",
            "Ballots Cast - DEMOCRATIC",
            "Ballots Cast - REPUBLICAN",
        ):
            if not line.startswith(label):
                continue
            n_expected = 1 if label.startswith("Registered Voters") else 4
            vals = _stats_value_after(label, line, n_expected, last_numeric)
            if vals and current is not None:
                if label.startswith("Registered Voters - Total"):
                    stats_totals.setdefault(current, {})["rv"] = vals[0]
                elif label == "Ballots Cast - Total":
                    stats_totals.setdefault(current, {})["bc"] = vals
                # per-party RV / BC rows are intentionally not recorded
            last_numeric = None
            break
        else:
            if line.startswith("Voter Turnout") or line.endswith("%"):
                last_numeric = None
                continue
            # not a stats row -- fall through to contest/candidate handling
            pass
        if line.startswith(("Registered Voters -", "Ballots Cast -", "Voter Turnout")) or line.endswith("%"):
            continue

        # ---- candidate / aggregate rows ------------------------------------
        m = VOTE_TAIL_RE.match(line)
        if office is not None and m:
            head = m.group(1).strip()
            vals = [int(v.replace(",", "")) for v in m.groups()[1:]]
            emit("Write Ins" if head.upper() == "WRITE-IN TOTALS" else _cap_name(head), vals)
            continue

        # ---- precinct-name re-print on a continuation page -----------------
        if PRECINCT_NAME_RE.match(line) and line == current:
            continue

        warnings.append(f"unmatched line (precinct {current!r}, office {office!r}): {line!r}")

    return rows, warnings, stats_totals


def _extract_text(pdf_path: Path):
    """pdftotext -layout extraction via subprocess."""
    with tempfile.NamedTemporaryFile(suffix=".txt") as tf:
        subprocess.run(["pdftotext", "-layout", str(pdf_path), tf.name], check=True)
        return Path(tf.name).read_text().splitlines()


def main(argv):
    if len(argv) != 4:
        sys.exit(f"Usage: {Path(argv[0]).name} <dem.pdf> <rep.pdf> <output.csv>")
    out_path = Path(argv[3])
    all_rows = []
    dem_stats = {}
    for label, pdf in (("DEM", argv[1]), ("REP", argv[2])):
        lines = _extract_text(Path(pdf))
        rows, warnings, stats = parse_report(lines, COUNTY)
        if warnings:
            print(f"[{label}] warnings ({len(warnings)}):")
            for w in warnings[:20]:
                print("  ", w)
            if len(warnings) > 20:
                print(f"   ... and {len(warnings) - 20} more")
        print(f"[{label}] {len(rows)} contest rows, {len(stats)} precinct stats")
        for pct, st in sorted(stats.items()):
            if label == "DEM":
                dem_stats[pct] = st
            else:
                dem_bc = dem_stats.get(pct, {}).get("bc")
                if dem_bc != st.get("bc"):
                    print(f"  BC-Total mismatch {pct}: DEM={dem_bc} REP={st.get('bc')}")
        if label == "DEM":
            for pct, st in sorted(stats.items()):
                rv, (v, ed, mail, prov) = st["rv"], st["bc"]
                all_rows.append({
                    "county": COUNTY, "precinct": pct, "office": "Registered Voters",
                    "district": "", "party": "", "candidate": "", "votes": rv,
                    "election_day": "", "mail": "", "provisional": "",
                })
                all_rows.append({
                    "county": COUNTY, "precinct": pct, "office": "Ballots Cast",
                    "district": "", "party": "", "candidate": "", "votes": v,
                    "election_day": ed, "mail": mail, "provisional": prov,
                })
        all_rows.extend(rows)
        missing = set(dem_stats) - set(stats) if label == "REP" else set()
        if missing:
            print(f"  BC/RV missing in REP file for {len(missing)} precincts")

    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Wrote {len(all_rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)