#!/usr/bin/env python3
"""
Parse Huntingdon County PA 2023 General (Municipal) Election precinct results.

Source: Huntingdon County Official Precinct Results 2023 General.pdf
(Electionware "Precinct Summary Results Report", vertical layout: candidate
rows carry 4 numbers inline TOTAL / Election Day / Mail Votes / Provisional;
Ballots Cast values sit on the line ABOVE their label). Parsed from the
pdftotext -layout extract.

Usage:
    python parsers/pa_huntingdon_general_2023_results_parser.py \
        <input.txt|input.pdf> <output.csv>

Uses the text-based shared engine in ``electionware_txt`` with office
conventions from ``pa_huntingdon_general_2025_results_parser.py``.

Huntingdon-2023-specific quirks:
  - The two 2023 Superior Court judicial-retention questions are UNNAMED:
    header "SUPERIOR COURT RETENTION QUESTION" twice per precinct, and the
    Yes/No candidate rows repeat the same header text. Ballot order
    statewide is Panella then Stabile, so the questions are labeled
    "Superior Court Retention - Jack Panella" / "- Victor P. Stabile" and
    their two data rows become candidates "Yes"/"No" (a preprocessor
    rewrites the row heads to YES/NO before parsing; validate_helper.py
    mirrors this via RETENTION_QUESTION_HEAD).
  - Garbled school-district headers ("<DIST> <DIST> REGION N", with source
    typos "TUESSY MOUNTIAN", "SOUTHERN HUNTINGDON ... EASTERN REGION") are
    mapped explicitly to "School Director Region N" / "<District>".
  - "AT LARGE"/"ATL ARGE" (sic) sits at the END of local-office headers
    ("AUDITOR SHIRLEY TOWNSHIP ATL ARGE" -> Auditor | At Large Shirley).
  - Ordinal precinct/ward suffixes ("MOUNT UNION 1ST WARD" -> "Mount
    Union 1", "HUNTINGDON 5TH DISTRICT" -> "Huntingdon 5").
  - Magisterial District Judge headers embed the district number plus the
    precinct ("MAGISTERIAL DISTRICT JUDGE 20-3-04 WOOD"); the county runs
    one MDJ contest per precinct in 2023 (the county summary lists each as
    its own "1 of 1" section), so the qualifier is kept in the district
    ("20-3-04 Wood").
  - Source typos "UNON" -> Union, "ORBISIONIA" -> Orbisonia.
  - The bare "AUDITOR" header (58x, one per precinct) is the county
    auditor office -> "County Auditor" (municipal auditors always carry a
    municipality).
"""

import csv
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from electionware_precinct_np import (  # noqa: E402
    ElectionwareConfig,
    normalize_office,
    prettify_huntingdon_precinct,
    simple_capitalize,
)
from pa_huntingdon_general_2025_results_parser import (  # noqa: E402
    EXACT_OFFICES as _EXACT_2025,
    LOCAL_OFFICES as _LOCAL_2025,
)

RETENTION_QUESTION_HEAD = "SUPERIOR COURT RETENTION QUESTION"

_EXACT_2023 = dict(_EXACT_2025)
_EXACT_2023.update(
    {
        "JUSTICE OF THE SUPREME COURT": ("Justice of the Supreme Court", ""),
        "JUDGE OF THE COURT OF COMMON PLEAS, 20TH JUDICIAL DISTRICT": (
            "Judge of the Court of Common Pleas",
            "",
        ),
        "REGISTER OF WILLS AND RECORDER OF DEEDS": ("Register and Recorder", ""),
        "AUDITOR": ("County Auditor", ""),
        RETENTION_QUESTION_HEAD: ("Superior Court Retention Question", ""),
    }
)

_NP_CFG = ElectionwareConfig(
    county="Huntingdon",
    skip_prefixes=(),
    county_header_suffix="Huntingdon County",
    exact_offices=_EXACT_2023,
    local_offices=_LOCAL_2025,
    local_office_orientation="prefix",
    retention_style="retention-loose",
    title_case_retention_tail=False,
    municipality_normalizer=simple_capitalize,
    prettify_precinct=prettify_huntingdon_precinct,
    fallback_title_case=simple_capitalize,
)

# 2023 school-district headers are garbled ("<DIST> <DIST> REGION N").
_SCHOOL_2023 = {
    "HUNTINGDON AREA HUNTINGDON AREA REGION 1": ("School Director Region 1", "Huntingdon Area"),
    "HUNTINGDON AREA HUNTINGDON AREA REGION 2": ("School Director Region 2", "Huntingdon Area"),
    "HUNTINGDON AREA HUNTINGDON AREA REGION 3": ("School Director Region 3", "Huntingdon Area"),
    "HUNTINGDON AREA REGION 1 HUNTINGDON AREA REGION 1": ("School Director Region 1", "Huntingdon Area"),
    "JUNIATA VALLEY JUNIATA VALLEY": ("School Director", "Juniata Valley"),
    "TYRONE AREA TYRONE AREA": ("School Director", "Tyrone Area"),
    "TUSSEY MOUNTAIN TUSSEY MOUNTAIN REGION 1": ("School Director Region 1", "Tussey Mountain"),
    "TUESSY MOUNTIAN TUSSEY MOUNTAIN REGION 3": ("School Director Region 3", "Tussey Mountain"),
    "SOUTHERN HUNTINGDON SOUTHERN HUNTINGDON EASTERN REGION": ("School Director", "Southern Huntingdon - Eastern Region"),
    "SOUTHERN HUNTINGDON SOUTHERN HUNTINGDON WESTERN REGION": ("School Director", "Southern Huntingdon - Western Region"),
    "SOUTHERN HUNTINGDON SOUTHERN HUNTINGDON CENTRAL REGION": ("School Director", "Southern Huntingdon - Central Region"),
    "MOUNT UNION AREA MOUNT UNION REGION 1": ("School Director Region 1", "Mount Union Area"),
    "MOUNT UNION AREA MOUNT UNION REGION 2": ("School Director Region 2", "Mount Union Area"),
    "MOUNT UNION AREA MOUNT UNION REGION 3": ("School Director Region 3", "Mount Union Area"),
}

_MDJ_RE = re.compile(
    r"^MAGISTERIAL DISTRICT JUDGE\s+((?:DISTRICT\s+)?\d{2}-\d-\d{2})\s+(.+)$",
    re.IGNORECASE,
)
_AT_LARGE_RE = re.compile(
    r"^(.*?)\s+(?:TOWNSHIP\s+|BOROUGH\s+)?(?:AT LARGE|ATL ARGE)$", re.IGNORECASE
)
_ORDINAL_RE = re.compile(
    r"^(.+?)\s+(\d+)(?:ST|ND|RD|TH)(?:\s+(?:WARD|DISTRICT))?$", re.IGNORECASE
)
_MUNI_SUFFIX_RE = re.compile(r"\s+(TOWNSHIP|BOROUGH|BORO|TWP)$", re.IGNORECASE)
_TYPOS = {"UNON": "Union", "ORBISIONIA": "Orbisonia"}


def _district_fix(d: str) -> str:
    d = d.strip()
    if not d:
        return ""
    m = _AT_LARGE_RE.match(d)
    if m:
        muni = _MUNI_SUFFIX_RE.sub("", m.group(1).strip())
        return f"At Large {simple_capitalize(muni)}"
    m = _ORDINAL_RE.match(d)
    if m:
        d = f"{m.group(1).strip()} {m.group(2)}"
    return _TYPOS.get(d.upper(), simple_capitalize(d))


def normalize(line: str):
    line = re.sub(r"\s+", " ", line).strip()
    if line.upper() in _SCHOOL_2023:
        return _SCHOOL_2023[line.upper()]
    m = _MDJ_RE.match(line)
    if m:
        # Huntingdon runs one MDJ contest per precinct in 2023 (the county
        # summary lists each as its own "1 of 1" section), so the trailing
        # precinct qualifier is part of the contest identity and kept.
        code = m.group(1).replace("DISTRICT ", "", 1).replace("DISTRICT", "", 1).strip()
        muni = _ordinal_lower(simple_capitalize(m.group(2)))
        return ("Magisterial District Judge", f"{code} {muni}")
    office, district = normalize_office(line, _NP_CFG)
    if office in (
        "Auditor", "Borough Council", "Constable", "Judge of Election",
        "Inspector of Election", "Mayor", "School Director",
        "Tax Collector", "Township Supervisor",
    ):
        district = _district_fix(district)
    return (office, district)


def disambiguate(office: str, district: str, occurrence: int):
    """The two unnamed 2023 Superior Court retention questions per
    precinct, in ballot order (Panella, Stabile) statewide. Several
    townships also list two identical "TOWNSHIP SUPERVISOR <TWP>" /
    "TOWNSHIP SUPERVISOR <TWP> AT LARGE" contests (two seats, 2023 has no
    2yr/6yr term tokens) — those get a "(2)" occurrence suffix."""
    if office == "Superior Court Retention Question":
        name = "Jack Panella" if occurrence == 0 else "Victor P. Stabile"
        return (f"Superior Court Retention - {name}", "")
    if occurrence > 0:
        return (f"{office} ({occurrence + 1})", district)
    return (office, district)


def _ordinal_lower(s: str) -> str:
    """"Mount Union Borough 1St" -> "Mount Union Borough 1st"."""
    return re.sub(
        r"\b(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), s
    )


def prettify_precinct(name: str) -> str:
    return _ordinal_lower(prettify_huntingdon_precinct(name))


_NUM_RE = re.compile(r"^\d[\d,]*$")

_VOTE_FOR_RE_H = re.compile(r"^Vote For\s+\d+$", re.IGNORECASE)
_STAT_MARK_RE = re.compile(r"^STATISTICS\b", re.IGNORECASE)


def _trailing_ints(s: str):
    toks = s.split()
    out = []
    for t in reversed(toks):
        if _NUM_RE.match(t):
            out.append(t)
        else:
            break
    out.reverse()
    return out


def merge_continuation_blocks(lines: list[str]) -> list[str]:
    """Huntingdon splits long contests across pages, repeating the office
    header on every continuation page (Alexandria Borough's Vote-For-3
    council contest spans three pages; Jackson Township's second supervisor
    contest spans two). Only the LAST page of a contest carries its
    "Contest Totals" row, so within a run of identical office headers a
    block immediately following a block WITHOUT "Contest Totals" is a
    continuation, not a separate contest: drop its header/Vote For lines
    so its rows merge into the preceding contest (the shared engine's
    _merge_split_aggregates then sums the aggregate partials)."""
    n = len(lines)
    stripped = [ln.strip() for ln in lines]
    # office headers: next non-empty line is "Vote For N"
    headers = []
    for i, s in enumerate(stripped):
        if not s or _STAT_MARK_RE.match(s):
            continue
        for j in range(i + 1, n):
            nxt = stripped[j]
            if not nxt:
                continue
            if _VOTE_FOR_RE_H.match(nxt):
                headers.append(i)
            break
    header_set = set(headers)
    drop: set[int] = set()
    k = 0
    while k < len(headers):
        h = headers[k]
        # gather the run of identical raw headers with no STAT marker or
        # different office header in between
        run = [h]
        while k + 1 < len(headers):
            nxt_h = headers[k + 1]
            if any(_STAT_MARK_RE.match(stripped[x]) for x in range(h, nxt_h)):
                break
            if stripped[nxt_h] != stripped[h]:
                break
            # also stop if a different office header sits in between
            if any(
                x in header_set and stripped[x] != stripped[h]
                for x in range(h, nxt_h)
            ):
                break
            k += 1
            run.append(headers[k])
        for idx, cur in enumerate(run):
            if idx == 0:
                continue
            prev = run[idx - 1]
            prev_has_ct = False
            for j in range(prev + 1, cur):
                if stripped[j].upper().startswith("CONTEST TOTALS"):
                    prev_has_ct = True
                    break
            if not prev_has_ct:
                # previous block was not the last page of its contest:
                # this header repeats it -> continuation
                drop.add(cur)
                for j in range(cur + 1, n):
                    if stripped[j]:
                        if _VOTE_FOR_RE_H.match(stripped[j]):
                            drop.add(j)
                        break
        k += 1
    return [ln for i, ln in enumerate(lines) if i not in drop]


def preprocess_retention_rows(lines: list[str]) -> list[str]:
    """Rewrite the two data rows under each unnamed retention question
    (which repeat the question header as the row head) to YES / NO so the
    shared engine records them as candidates."""
    out = []
    in_q = False
    row_no = 0
    for ln in lines:
        s = ln.strip()
        toks = s.split()
        if " ".join(toks[:4]) == RETENTION_QUESTION_HEAD:
            if len(toks) == 4:
                # office header
                in_q = True
                row_no = 0
            elif (
                in_q
                and len(toks) == 8
                and all(_NUM_RE.match(t.replace(",", "")) for t in toks[4:])
            ):
                row_no += 1
                repl = "YES" if row_no % 2 == 1 else "NO"
                ln = re.sub(
                    r"^(\s*)" + re.escape(RETENTION_QUESTION_HEAD), r"\1" + repl, ln
                )
        out.append(ln)
    return out


from electionware_txt import (  # noqa: E402
    FIELDNAMES,
    TxtConfig,
    parse_input,
)

CONFIG = TxtConfig(
    county="Huntingdon",
    normalize_office=normalize,
    prettify_precinct=prettify_precinct,
    disambiguate=disambiguate,
    # Cross-filed MDJ / supervisor candidates ("Lisa Marie Covert", "Linda
    # Greenland") print without a party code in 2023.
    party_optional=True,
)


def main(argv):
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.txt|input.pdf> <output.csv>")
    inp = Path(argv[1])
    if inp.suffix.lower() == ".pdf":
        rows, n, warnings, unnamed = parse_input(str(inp), CONFIG)
    else:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
            tmp = Path(tf.name)
            txt_lines = inp.read_text(errors="replace").split("\n")
            txt_lines = merge_continuation_blocks(txt_lines)
            txt_lines = preprocess_retention_rows(txt_lines)
            tmp.write_text("\n".join(txt_lines), encoding="utf-8")
        try:
            rows, n, warnings, unnamed = parse_input(str(tmp), CONFIG)
        finally:
            tmp.unlink(missing_ok=True)
    out_path = Path(argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows across {n} precincts to {out_path}")
    if unnamed:
        print(f"Skipped {unnamed} unnamed (county-summary) Statistics segment(s)")
    if warnings:
        print(f"WARNING: {len(warnings)} unmatched lines:")
        for wmsg in warnings[:40]:
            print("  " + wmsg)
        if len(warnings) > 40:
            print("  ...")


if __name__ == "__main__":
    main(sys.argv)