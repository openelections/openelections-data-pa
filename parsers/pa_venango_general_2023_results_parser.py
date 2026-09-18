#!/usr/bin/env python3
"""Venango County 2023 general — county-level results from the official
"Official Results Summary" (Electionware ESR).

Header quirks handled:
- "Township Supervisor T6 Allegheny" -> office "Township Supervisor (6 Year)",
  district "Allegheny Township" (T<n> is the term length)
- "Borough Council T2V1 Polk" -> office "Borough Council (2 Year)",
  district "Polk Borough" (V<n> is the vote-for count, repeated on the
  "Vote For n" line)
- "Panella Retention" / "Stabile Retention" -> Superior Court retention
  questions (Yes/No)

Usage: pa_venango_general_2023_results_parser.py <input.pdf|txt> <output.csv>
"""

import re

from pa_2023_summary_common import parse_esr, run_cli

CITY_COUNCIL = re.compile(r"^City Council (District \d+|At Large OC Oil City)$")


def map_contest(header):
    h = re.sub(r"\s+", " ", header.strip())

    if h == "Panella Retention":
        return "Judge of the Superior Court Retention - Jack Panella", ""
    if h == "Stabile Retention":
        return "Judge of the Superior Court Retention - Victor P. Stabile", ""

    m = re.match(r"^(Mayor|City Council)\s+(Oil City)$", h)
    if m:
        return m.group(1), "Oil City"

    m = CITY_COUNCIL.match(h)
    if m:
        tail = m.group(1)
        if tail.startswith("District"):
            return "City Council", "Oil City " + tail
        return "City Council", "Oil City At Large"

    m = re.match(r"^Magisterial District Judge\s+(Magisterial District .+)$", h)
    if m:
        return "Magisterial District Judge", m.group(1)

    m = re.match(r"^(Constable)\s+(.+)$", h)
    if m:
        return "Constable", m.group(2)

    # term-coded offices: "<base> [T<n>[V<k>]] <jurisdiction...> [T<n> ...]"
    m = re.match(r"^(Township Supervisor|Township Tax Collector|"
                 r"Township Auditor|Borough Council|Borough Tax Collector|"
                 r"Borough Auditor|School Director)(?:\s+T(\d)(?:V\d+))?"
                 r"\s+(.+)$", h)
    if not m:
        return h, ""
    base = m.group(1)
    term = m.group(2)
    rest = m.group(3).strip()
    if not term:
        m2 = re.search(r"\s+T(\d)(?:V\d+)?\s+", rest)
        if m2:
            term = m2.group(1)
            rest = (rest[:m2.start()] + " " + rest[m2.end():]).strip()
        else:
            return h, ""

    if base.startswith("Township"):
        office = f"{base} ({term} Year)"
        toks = rest.split(None, 1)
        # "Mineral" -> Mineral Township ; "Sugarcreek 2" -> keep as given
        if re.match(r"^\d+$", toks[-1]) and len(toks) > 1:
            return office, toks[0] + " Township Ward " + toks[1]
        return office, rest + " Township"
    if base.startswith("Borough"):
        office = f"{base} ({term} Year)"
        parts = rest.split()
        if parts and re.match(r"^\d+$", parts[-1]) and len(parts) > 1:
            return office, parts[0] + " Borough Ward " + parts[-1]
        return office, rest + " Borough"
    # School Director: rest = "<school district>[- <municipality>]"
    office = f"School Director ({term} Year)"
    return office, rest


if __name__ == "__main__":
    run_cli("Venango", map_contest, parse_esr, titlecase=True)