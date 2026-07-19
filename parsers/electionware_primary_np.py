"""Shared natural_pdf Electionware parser for PA **primary** elections.

Sibling of ``electionware_precinct_np`` (which handles general/municipal
elections). Reuses that module's block extraction, vote-tail regexes, office
normalization and CSV plumbing, but overrides row parsing to handle the one
structural difference between a PA primary report and a general report: in a
primary, the party affiliation lives on the **office header** line (e.g.
``DEM PRESIDENT OF THE UNITED STATES``), not on each candidate row. Candidate
rows under that header inherit the header's party.

Per-county parser scripts (e.g. ``pa_juniata_primary_2024_results_parser.py``)
import ``PrimaryConfig`` and ``run_cli`` from this module and supply a
county-specific config, exactly as the 2025 general parsers do for
``ElectionwareConfig``.

Usage from a county script::

    from electionware_primary_np import PrimaryConfig, run_cli
    CONFIG = PrimaryConfig(county="Juniata", ...)
    if __name__ == "__main__":
        run_cli(CONFIG)
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Optional

from electionware_precinct_np import (
    PARTY_CODES,
    PARTY_RE,
    SINGLE_TAIL_RE,
    VOTE_FOR_RE,
    VOTE_TAIL_RE,
    ElectionwareConfig,
    _merge_split_aggregates,
    extract_precinct_blocks,
    normalize_office,
    parse_votes,
)
import natural_pdf as npdf


# 2024 primary file convention (matches the existing Adams/Chester 2024
# primary CSVs): mail-vote column is reported as ``absentee``, ``provisional``
# precedes it, and there is no ``vote_for`` column. State House is used (not
# "General Assembly") for the PA House of Representatives.
PRIMARY_FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "party",
    "candidate",
    "votes",
    "election_day",
    "provisional",
    "absentee",
]

# Candidate-name finalizer: strip commas, title-case preserving periods,
# Mc-prefixes and Roman numerals. "JOSEPH R. BIDEN, JR." -> "Joseph R. Biden Jr."
_ROMAN_RE = re.compile(r"^[IVX]+$")


def _finalize_candidate(raw: str) -> str:
    s = raw.replace(",", "")
    out = []
    for w in s.split():
        if _ROMAN_RE.match(w.upper()):
            out.append(w.upper())
        elif w.upper() in ("JR", "SR"):
            out.append(w.upper().replace("JR", "Jr.").replace("SR", "Sr."))
        elif len(w) >= 3 and w[:2].lower() == "mc":
            out.append("Mc" + w[2:].capitalize())
        else:
            out.append(w.capitalize())
    return " ".join(out)

# Leading party code on a primary office header: "DEM PRESIDENT...",
# "REP REPRESENTATIVE IN CONGRESS 5TH DISTRICT", "GP STATE SENATOR 33RD".
PRIMARY_OFFICE_PARTY_RE = re.compile(
    r"^(" + "|".join(re.escape(p) for p in PARTY_CODES) + r")\s+(.+)$",
    re.IGNORECASE,
)

# Ordinal district suffix on congressional/legislative offices:
# "5TH DISTRICT", "33RD DISTRICT", "108TH DISTRICT".
DISTRICT_ORDINAL_RE = re.compile(
    r"\b(\d+)(?:ST|ND|RD|TH)\s+DISTRICT\b", re.IGNORECASE
)

# Standard 2024 PA primary statewide / federal / legislative office names
# (the raw all-caps text after the party code is stripped). Values are
# (normalized_office, extract_district?) — when extract_district is True,
# a trailing ordinal district is pulled out of the header into ``district``.
STATEWIDE_OFFICES: dict[str, tuple[str, bool]] = {
    "PRESIDENT OF THE UNITED STATES": ("President", False),
    "UNITED STATES SENATOR": ("U.S. Senate", False),
    "GOVERNOR": ("Governor", False),
    "LIEUTENANT GOVERNOR": ("Lieutenant Governor", False),
    "LT. GOVERNOR": ("Lieutenant Governor", False),
    "LT GOVERNOR": ("Lieutenant Governor", False),
    "ATTORNEY GENERAL": ("Attorney General", False),
    "AUDITOR GENERAL": ("Auditor General", False),
    "STATE TREASURER": ("State Treasurer", False),
    "REPRESENTATIVE IN CONGRESS": ("U.S. House", True),
    "REP. IN CONGRESS": ("U.S. House", True),
    "REP IN CONGRESS": ("U.S. House", True),
    "SENATOR IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "SENATOR IN GENERAL ASSEMBLY": ("State Senate", True),
    "SEN. IN THE GEN. ASSEMBLY": ("State Senate", True),
    "SEN IN THE GENERAL ASSEMBLY": ("State Senate", True),
    "REPRESENTATIVE IN THE GENERAL ASSEMBLY": ("State House", True),
    "REPRESENTATIVE IN GENERAL ASSEMBLY": ("State House", True),
    "REP. IN GEN. ASSEMBLY": ("State House", True),
    "REP IN GEN ASSEMBLY": ("State House", True),
    "REP. IN THE GENERAL ASSEMBLY": ("State House", True),
    "REP IN GEN. ASSEMBLY": ("State House", True),
    "MEMBER OF DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "MEMBER OF THE DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "MEMBER OF THE REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    "DEMOCRATIC STATE COMMITTEE": ("Member of Democratic State Committee", False),
    "REPUBLICAN STATE COMMITTEE": ("Member of Republican State Committee", False),
    # Bare "STATE COMMITTEE" (Snyder) — resolved via current_party below.
    "STATE COMMITTEE": ("__STATE_COMMITTEE__", False),
}


def _split_primary_header(line: str) -> tuple[str, str]:
    """Return (party_upper_or_empty, office_text) for a primary office header.

    ``office_text`` has any trailing ordinal district removed; the district
    is re-extracted by the caller via :data:`STATEWIDE_OFFICES`.
    """
    m = PRIMARY_OFFICE_PARTY_RE.match(line)
    if not m:
        return "", line
    party = m.group(1).upper()
    rest = m.group(2).strip()
    return party, rest


def parse_primary_precinct_rows(
    precinct: str, text: str, config: ElectionwareConfig
) -> list[dict]:
    rows: list[dict] = []
    current_office: Optional[str] = None
    current_district: str = ""
    current_party: str = ""
    current_vote_for: int = 1
    # Registered Voters is a precinct-level total, not per-party. Some source
    # PDFs (e.g. Cumberland 2026 primary) repeat the "Registered Voters - Total"
    # line in each party section of a precinct block; emit it only once.
    emitted_registered_voters = False

    # Configurable vote-tail regex: 4-integer (default) or 2-integer (no
    # method breakdown — e.g. Franklin 2024 primary). Falls back to the
    # standard 4-integer regex when the config doesn't define one (e.g. a
    # plain ElectionwareConfig is passed).
    vre = getattr(config, "vote_tail_re", None) or VOTE_TAIL_RE
    breakdown = getattr(config, "vote_breakdown", True)

    lines = [ln.strip() for ln in text.split("\n")]
    if config.line_preprocessor is not None:
        lines = [config.line_preprocessor(ln) for ln in lines]

    # Merge wrapped Write-In continuation lines.
    merged: list[str] = []
    for ln in lines:
        if (
            merged
            and merged[-1].startswith("Write-In:")
            and ln
            and not re.search(r"\d", ln)
        ):
            merged[-1] = merged[-1] + " " + ln
        else:
            merged.append(ln)
    lines = merged

    office_header_idx: dict[int, int] = {}
    for i, ln in enumerate(lines):
        if not ln:
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt:
                continue
            vf = VOTE_FOR_RE.match(nxt)
            if vf:
                office_header_idx[i] = int(vf.group(1))
            break

    def add(office, district, party, candidate, vals, vote_for=None):
        # vals is a tuple of ints; length depends on the vote-tail regex.
        # 4 -> (total, ed, mail, prov); 1 -> (total,). Pad missing breakdown
        # columns with empty strings.
        total = vals[0]
        ed = vals[1] if len(vals) > 1 and breakdown else ""
        mail = vals[2] if len(vals) > 2 and breakdown else ""
        prov = vals[3] if len(vals) > 3 and breakdown else ""
        rows.append(
            {
                "county": config.county,
                "precinct": precinct,
                "office": office,
                "district": district,
                "party": party,
                "candidate": candidate,
                "votes": total,
                "election_day": ed,
                "provisional": prov,
                "absentee": mail,
            }
        )

    for idx, line in enumerate(lines):
        if not line:
            continue
        if line.startswith(config.skip_prefixes):
            # A skipped office header (e.g. per-precinct committee race)
            # must clear the current-office context so subsequent candidate
            # rows aren't attributed to the previous contest.
            if idx in office_header_idx:
                current_office = None
                current_district = ""
            continue
        # The precinct-name banner repeats on every continuation page of
        # a multi-page precinct block (Mercer). Skip lines that match the
        # precinct name so they aren't misread as candidate rows under the
        # previous page's last office.
        if precinct and line == precinct:
            continue
        if line.startswith("Statistics") or line.startswith("STATISTICS"):
            continue

        if idx in office_header_idx:
            party, rest = _split_primary_header(line)
            # Section-based party tracking (Snyder 2026 primary): some
            # reports prefix only the first office in a party section
            # ("DEM GOVERNOR") and leave subsequent offices un-prefixed
            # ("LT. GOVERNOR", "REP. IN CONGRESS 15TH DISTRICT"). Inherit
            # the most recent party when the header has no prefix.
            if party:
                current_party = party
            office, district = _normalize_primary_office(rest, config)
            # Resolve bare "STATE COMMITTEE" (Snyder) via the section party.
            if office == "__STATE_COMMITTEE__":
                office = ("Member of Republican State Committee"
                          if current_party == "REP"
                          else "Member of Democratic State Committee")
            current_office = office
            current_district = district
            current_vote_for = office_header_idx[idx]
            continue

        if line.startswith("Registered Voters - Total"):
            m = SINGLE_TAIL_RE.match(line)
            if m and not emitted_registered_voters:
                rows.append(
                    {
                        "county": config.county,
                        "precinct": precinct,
                        "office": "Registered Voters",
                        "district": "",
                        "party": "",
                        "candidate": "",
                        "votes": int(m.group(2).replace(",", "")),
                        "election_day": "",
                        "provisional": "",
                        "absentee": "",
                    }
                )
                emitted_registered_voters = True
            continue
        if line.startswith("Ballots Cast - Total"):
            m = vre.match(line)
            if m:
                vals = parse_votes(list(m.groups()[1:]))
                add("Ballots Cast", "", "", "", vals)
            continue
        if line.startswith("Ballots Cast - Blank"):
            m = vre.match(line)
            if m:
                vals = parse_votes(list(m.groups()[1:]))
                add("Ballots Cast Blank", "", "", "", vals)
            continue

        vote_m = vre.match(line)
        if vote_m is None:
            continue

        head = vote_m.group(1).strip()
        vals = parse_votes(list(vote_m.groups()[1:]))

        if current_office is None:
            continue

        if head.upper() in ("YES", "NO"):
            add(
                current_office,
                current_district,
                current_party,
                head.upper().capitalize(),
                vals,
                current_vote_for,
            )
            continue

        pm = PARTY_RE.match(head)
        if pm:
            add(
                current_office,
                current_district,
                pm.group(1).upper(),
                _finalize_candidate(pm.group(2).strip()),
                vals,
                current_vote_for,
            )
            continue

        if head == "Write-In Totals":
            add(
                current_office,
                current_district,
                current_party,
                "Write-In Totals",
                vals,
                current_vote_for,
            )
            continue
        if head.startswith("Write-In:"):
            continue
        if head == "Not Assigned":
            add(current_office, current_district, current_party, "Not Assigned", vals, current_vote_for)
            continue
        if head == "Overvotes":
            add(current_office, current_district, current_party, "Overvotes", vals, current_vote_for)
            continue
        if head == "Undervotes":
            add(current_office, current_district, current_party, "Undervotes", vals, current_vote_for)
            continue

        # No per-row party prefix: inherit the office-header party (primary).
        # For party-optional counties (no header party either), emit empty.
        add(
            current_office,
            current_district,
            current_party,
            _finalize_candidate(head),
            vals,
            current_vote_for,
        )

    return _merge_split_aggregates(rows)


def _normalize_primary_office(rest: str, config: ElectionwareConfig) -> tuple[str, str]:
    """Normalize the office portion of a primary header (party already stripped).

    Checks the statewide/federal table first (extracting an ordinal district
    where applicable), then falls through to the general engine's
    ``normalize_office`` so per-county ``exact_offices`` / ``local_offices``
    still handle local contests (township supervisor, school director, etc.).
    """
    upper = rest.upper()
    dm = DISTRICT_ORDINAL_RE.search(upper)
    district = str(int(dm.group(1))) if dm else ""
    office_key = DISTRICT_ORDINAL_RE.sub("", upper).strip() if dm else upper
    if office_key in STATEWIDE_OFFICES:
        norm, extract = STATEWIDE_OFFICES[office_key]
        return (norm, district if extract else "")
    # Some reports omit "DISTRICT" and use just "5TH" — try matching the
    # base office name against the prefix of the header.
    for key, (norm, extract) in STATEWIDE_OFFICES.items():
        if office_key == key or office_key.startswith(key + " "):
            return (norm, district if extract else "")
    return normalize_office(rest, config)


def parse_primary_pdf(
    pdf_path: Path, config: ElectionwareConfig
) -> tuple[list[dict], int]:
    pdf = npdf.PDF(str(pdf_path))
    rows: list[dict] = []
    precinct_count = 0
    extractor = config.precinct_block_extractor or extract_precinct_blocks
    for precinct_name, text in extractor(pdf, config):
        precinct_count += 1
        pretty = re.sub(r"\s{2,}", " ", config.prettify_precinct(precinct_name)).strip()
        rows.extend(parse_primary_precinct_rows(pretty, text, config))
    return rows, precinct_count


def write_primary_csv(rows: list[dict], out_path: Path) -> None:
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRIMARY_FIELDNAMES)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in PRIMARY_FIELDNAMES})


def run_cli(config: ElectionwareConfig, argv: Optional[list[str]] = None) -> None:
    argv = list(argv) if argv is not None else sys.argv
    if len(argv) != 3:
        script = Path(argv[0]).name if argv else "parser"
        sys.exit(f"Usage: {script} <input.pdf> <output.csv>")
    pdf_path = Path(argv[1])
    out_path = Path(argv[2])
    if not pdf_path.exists():
        sys.exit(f"Missing PDF: {pdf_path}")
    rows, precinct_count = parse_primary_pdf(pdf_path, config)
    write_primary_csv(rows, out_path)
    print(
        f"Wrote {len(rows)} rows across {precinct_count} precincts to {out_path}"
    )


# Re-export commonly used symbols so county scripts only need this one import.
from dataclasses import dataclass

__all__ = [
    "PrimaryConfig",
    "STATEWIDE_OFFICES",
    "run_cli",
]


@dataclass
class PrimaryConfig(ElectionwareConfig):
    """Marker subclass; behaves identically to ``ElectionwareConfig``.

    Adds two primary-specific knobs:

    - ``vote_tail_re``: regex matching a candidate/aggregate row's vote tail.
      Defaults to the 4-integer Electionware tail (total, election_day, mail,
      provisional). Counties whose 2024 primary PDF reports only a total and
      a vote percentage (no method breakdown — e.g. Franklin) override this
      with a 2-integer tail regex and set ``vote_breakdown=False`` so the
      breakdown columns are emitted empty.
    - ``vote_breakdown``: when False, rows carry only a total; the
      election_day / provisional / absentee columns are written empty.
    """

    vote_tail_re: "re.Pattern" = VOTE_TAIL_RE
    vote_breakdown: bool = True