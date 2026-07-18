#!/usr/bin/env python3
"""
Parse Pike County PA 2024 Primary precinct results.

Source: Pike PA April 23, 2024 Official Results per Precinct.pdf
(Crosstab format: each contest has a "D - OFFICE (Vote for N)" header, a
"Precinct <cand1> - DEM <cand2> - DEM ..." column header, then precinct rows
with the precinct name left-aligned and vote counts column-aligned. Parsed
via pdftotext -layout which preserves the column alignment.)

Usage:
    python parsers/pa_pike_primary_2024_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path


OFFICE_MAP = {
    "PRESIDENT OF THE UNITED STATES": "President",
    "UNITED STATES SENATOR": "U.S. Senate",
    "ATTORNEY GENERAL": "Attorney General",
    "AUDITOR GENERAL": "Auditor General",
    "STATE TREASURER": "State Treasurer",
    "REPRESENTATIVE IN CONGRESS": "U.S. House",
    "CONGRESS REPRESENTATIVE": "U.S. House",
    "SENATOR IN THE GENERAL ASSEMBLY": "State Senate",
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": "State House",
    "GENERAL ASSEMBLY": "State House",
}

DISTRICT_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\s+DISTRICT\b", re.IGNORECASE)
DISTRICT_AFTER_RE = re.compile(r"\bDISTRICT\s+(\d+)\b", re.IGNORECASE)
PAREN_DIST_RE = re.compile(r"\((\d+)(?:st|nd|rd|th)?\s+District\)", re.IGNORECASE)

CONTEST_RE = re.compile(
    r"^([DR])\s*-\s*(.+?)\s*\(Vote for\s+(\d+)\)\s*$"
)

FIELDNAMES = [
    "county", "precinct", "office", "district", "party",
    "candidate", "votes", "election_day", "provisional", "absentee",
]


def normalize_office(raw: str) -> tuple[str, str]:
    upper = raw.upper().strip()
    district = ""
    dm = DISTRICT_RE.search(upper)
    if dm:
        district = str(int(dm.group(1)))
        upper = DISTRICT_RE.sub("", upper).strip()
    da = DISTRICT_AFTER_RE.search(upper)
    if da and not district:
        district = str(int(da.group(1)))
        upper = DISTRICT_AFTER_RE.sub("", upper).strip()
    pdm = PAREN_DIST_RE.search(upper)
    if pdm and not district:
        district = str(int(pdm.group(1)))
        upper = PAREN_DIST_RE.sub("", upper).strip()
    upper = re.sub(r"\s+", " ", upper).strip()
    if upper in OFFICE_MAP:
        extract = upper in ("REPRESENTATIVE IN CONGRESS", "CONGRESS REPRESENTATIVE", "SENATOR IN THE GENERAL ASSEMBLY", "REPRESENTATIVE IN THE GENERAL ASSEMBLY", "GENERAL ASSEMBLY")
        return (OFFICE_MAP[upper], district if extract else "")
    for key, norm in OFFICE_MAP.items():
        if upper == key or upper.startswith(key + " "):
            extract = key in ("REPRESENTATIVE IN CONGRESS", "CONGRESS REPRESENTATIVE", "SENATOR IN THE GENERAL ASSEMBLY", "REPRESENTATIVE IN THE GENERAL ASSEMBLY", "GENERAL ASSEMBLY")
            return (norm, district if extract else "")
    words = []
    for w in raw.split():
        if re.match(r"^[IVX]+$", w.upper()):
            words.append(w.upper())
        else:
            words.append(w.capitalize())
    return (" ".join(words), district)


def finalize_candidate(name: str) -> str:
    name = name.replace(",", "").strip()
    parts = name.split()
    out = []
    for w in parts:
        if "." in w and w.upper() == w:
            out.append(w)
        elif "-" in w and w.upper() != w:
            sub = []
            for piece in w.split("-"):
                sub.append(piece.capitalize())
            out.append("-".join(sub))
        elif w.upper() in ("JR", "SR", "II", "III", "IV"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif w[:2].upper() == "MC":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def extract_text(pdf_path: Path) -> str:
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), tmp_path],
            check=True, capture_output=True,
        )
        with open(tmp_path, encoding="utf-8") as f:
            return f.read()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _merge_wrapped_lines(lines: list[str]) -> list[str]:
    """Merge precinct-name lines that wrap around their number row.

    Pike's PDF wraps long precinct names like:
        BLOOMING GROVE
                                       125   53   60   73   43   5
        TWP
    into three lines. We merge the text-only lines around the numbers-only
    line into a single logical row.
    """
    out: list[str] = []
    pending_name = ""
    pending_nums: list[str] = []
    has_pending = False

    def is_nums_only(s: str) -> bool:
        toks = s.split()
        return bool(toks) and all(re.match(r"^[\d,]+$", t) for t in toks)

    def is_text_only(s: str) -> bool:
        toks = s.split()
        return bool(toks) and not any(re.match(r"^[\d,]+$", t) for t in toks)

    def is_protected(s: str) -> bool:
        st = s.strip()
        if CONTEST_RE.match(st):
            return True
        if st.startswith("Precinct "):
            return True
        if st in ("Results per Precinct", "Pike County PA 04232024", "Official", "2024-05-03 09:35:39"):
            return True
        if st.startswith("Total ") or st == "Total":
            return True
        return False
    """Merge precinct-name lines that wrap around their number row.

    Pike's PDF wraps long precinct names like:
        BLOOMING GROVE
                                       125   53   60   73   43   5
        TWP
    into three lines. We merge the text-only lines around the numbers-only
    line into a single logical row.
    """
    out: list[str] = []
    pending_name = ""
    pending_nums: list[str] = []
    has_pending = False

    def is_nums_only(s: str) -> bool:
        toks = s.split()
        return bool(toks) and all(re.match(r"^[\d,]+$", t) for t in toks)

    def is_text_only(s: str) -> bool:
        toks = s.split()
        return bool(toks) and not any(re.match(r"^[\d,]+$", t) for t in toks)

    for raw in lines:
        s = raw.rstrip()
        if not s.strip():
            if has_pending:
                out.append(pending_name + " " + " ".join(pending_nums))
                pending_name = ""
                pending_nums = []
                has_pending = False
            out.append("")
            continue
        stripped = s.strip()
        if is_protected(stripped):
            if has_pending:
                out.append(pending_name + " " + " ".join(pending_nums))
                pending_name = ""
                pending_nums = []
                has_pending = False
            out.append(stripped)
            continue
        if is_nums_only(stripped):
            if pending_name and not has_pending:
                pending_nums = stripped.split()
                has_pending = True
            elif has_pending:
                out.append(pending_name + " " + " ".join(pending_nums))
                pending_name = ""
                pending_nums = []
                has_pending = False
                out.append(stripped)
            else:
                out.append(stripped)
            continue
        if is_text_only(stripped):
            if has_pending:
                pending_name = pending_name + " " + stripped
                out.append(pending_name + " " + " ".join(pending_nums))
                pending_name = ""
                pending_nums = []
                has_pending = False
            else:
                pending_name = stripped
            continue
        if has_pending:
            out.append(pending_name + " " + " ".join(pending_nums))
            pending_name = ""
            pending_nums = []
            has_pending = False
        out.append(stripped)
    if has_pending:
        out.append(pending_name + " " + " ".join(pending_nums))
    return out


def parse_pike(text: str) -> list[dict]:
    rows: list[dict] = []
    current_office = ""
    current_district = ""
    current_party = ""
    candidates: list[str] = []
    n_candidates = 0
    for line in _merge_wrapped_lines(text.split("\n")):
        line = line.rstrip()
        if not line.strip():
            continue
        if line.strip() in ("Results per Precinct", "Pike County PA 04232024", "Official", "2024-05-03 09:35:39"):
            continue
        if line.strip().startswith("Total ") or line.strip() == "Total":
            continue
        cm = CONTEST_RE.match(line.strip())
        if cm:
            party_code = cm.group(1)
            current_party = "DEM" if party_code == "D" else "REP"
            current_office, current_district = normalize_office(cm.group(2))
            candidates = []
            n_candidates = 0
            continue
        if line.strip().startswith("Precinct "):
            rest = line.strip()[len("Precinct "):]
            cands = re.split(r"\s*-\s*(?:DEM|REP)\s*", rest)
            candidates = [c.strip() for c in cands if c.strip()]
            n_candidates = len(candidates)
            continue
        if not current_office or n_candidates == 0:
            continue
        if line.strip().startswith("SPECIAL ") or "SPECIAL" in line.strip().upper()[:10]:
            continue
        toks = line.split()
        if len(toks) < n_candidates + 1:
            continue
        nums = toks[-n_candidates:]
        if not all(re.match(r"^[\d,]+$", n) for n in nums):
            continue
        precinct = " ".join(toks[:-n_candidates]).strip()
        if precinct.upper() == "TOTAL":
            continue
        # Drop rows where the precinct name contains bare-digit tokens
        # (these are wrapped-header contests where the column header split
        # across multiple lines and the numbers merged into the precinct name).
        if any(re.match(r"^\d+$", t) for t in precinct.split()):
            continue
        for cand, nv in zip(candidates, nums):
            votes = int(nv.replace(",", ""))
            cand_name = "Write-In Totals" if cand.lower().startswith("write") else finalize_candidate(cand)
            rows.append({
                "county": "Pike", "precinct": precinct,
                "office": current_office, "district": current_district,
                "party": current_party, "candidate": cand_name,
                "votes": votes, "election_day": "", "provisional": "", "absentee": "",
            })
    return rows


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        sys.exit(f"Usage: {Path(argv[0]).name} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    text = extract_text(pdf_path)
    rows = parse_pike(text)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main(sys.argv)