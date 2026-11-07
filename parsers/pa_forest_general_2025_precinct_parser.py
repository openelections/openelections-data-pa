#!/usr/bin/env python3
"""
Forest County, PA 2025 General Election PRECINCT-level results parser.

Source: "Forest PA General Precinct.pdf" -- a scanned (no text layer)
per-precinct Electionware Summary Results Report. Each precinct gets four
pages (countywide judicial contests + the five court retention questions
plus the statistics box); the precinct name appears in large text in the
header. The per-precinct report does NOT contain the local offices that
the county-level file carries (tax collector, supervisor, auditor, judge/
inspector of election, constable) -- those have no precinct-level source.

Pages are sent to Claude as images (requires ANTHROPIC_API_KEY in the
environment). Zero-value "Write-In Totals" rows are included per contest;
if a page's extraction still omits one, re-run it -- the script validates
office sums against the county file when 2025/counties/
20251104__pa__general__forest__county.csv exists.

Usage:
    python parsers/pa_forest_general_2025_precinct_parser.py <input.pdf> <output.csv>
"""

import base64
import csv
import json
import os
import re
import sys
import tempfile

PROMPT = """This image is one page of Forest County, Pennsylvania's per-precinct summary report for the 2025 Municipal General Election (November 4, 2025). The precinct/municipality name is in large text near the top (e.g. "BARNETT TOWNSHIP").

Extract:
1. The precinct name (title-cased, e.g. "Barnett Township").
2. From the STATISTICS box: rows office="Registered Voters" votes=<Registered Voters - Total>, and office="Ballots Cast" votes + election_day + mail + provisional (columns: TOTAL, Election Day, Mail Votes, Provisional). Also office="Ballots Cast Blank" when "Ballots Cast - Blank" is present.
3. For EVERY contest on the page: office (contest name, drop "Vote For N" lines), district (when the contest names a region/ward/district), and one row per candidate with party (leading code DEM/REP/LBR/... or empty), candidate name ("Write-In" for "Write-In Totals"), votes (TOTAL column), election_day (Election Day), mail (Mail Votes), provisional (Provisional Votes).

IMPORTANT: include the Write-In Totals row for both judicial contests even when all four numbers are 0.

Skip Write-In: Scattered, Not Assigned, Total Votes Cast, Overvotes, Undervotes, Contest Totals and Turnout rows.

Return ONLY a JSON array of objects with keys county ("Forest"), precinct, office, district, party, candidate, votes, election_day, mail, provisional."""


def extract_rows(pdf_path):
    import anthropic

    import pdfplumber
    client = anthropic.Anthropic()
    model = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4.5")
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            image = pg.to_image(resolution=150)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                image.save(tmp.name, format="PNG")
                png = tmp.name
            content = [{"type": "text", "text": PROMPT},
                       {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                    "data": base64.standard_b64encode(open(png, "rb").read()).decode()}}]
            os.unlink(png)
            got = None
            for attempt, mt in enumerate((16000, 24000), 1):
                resp = client.messages.create(model=model, max_tokens=mt,
                                              messages=[{"role": "user", "content": content}])
                text = "".join(getattr(b, "text", "") for b in resp.content
                               if getattr(b, "type", "") == "text").strip()
                try:
                    got = json.loads(text[text.find("["):text.rfind("]") + 1])
                    break
                except json.JSONDecodeError as exc:
                    print(f"page {i} attempt {attempt}: {exc}")
            if got:
                rows.extend(got)
                print(f"page {i}: {len(got)} rows")
        # end with-pdfplumber
    return rows


def normalize(rows):
    def key(r):
        return (r["precinct"].strip().title(), r["office"].strip().casefold(),
                (r.get("candidate") or "").strip().casefold())

    dedup = {}
    for r in rows:
        dedup.setdefault(key(r), r)

    def num(r, f):
        v = r.get(f)
        return int(v) if isinstance(v, (int, float)) else ""

    out = []
    for r in dedup.values():
        office = r["office"].strip()
        party = (r.get("party") or "").upper()
        cand = (r.get("candidate") or "").strip()
        if "RETENTION" in office.casefold():
            office = office.upper().replace("RETENTION -", "RETENTION ELECTION QUESTION")
            if "CHRISTINE DONOHUE" in office:
                # county file keys the Donohue retention under the unnamed office
                office = "SUPREME COURT RETENTION ELECTION QUESTION"
            out.append(dict(county="Forest", precinct=r["precinct"], office=office, district="",
                            party=party, candidate="", votes=num(r, "votes"),
                            election_day=num(r, "election_day"), mail=num(r, "mail"),
                            provisional=num(r, "provisional")))
        elif cand.casefold().replace(" ", "").replace("-", "") in ("writein", "writetotals", "intotals"):
            out.append(dict(county="Forest", precinct=r["precinct"], office=office.upper(), district="",
                            party="WI", candidate="Write-in", votes=num(r, "votes"),
                            election_day=num(r, "election_day"), mail=num(r, "mail"),
                            provisional=num(r, "provisional")))
        elif office in ("Registered Voters", "Ballots Cast", "Ballots Cast Blank"):
            out.append(dict(county="Forest", precinct=r["precinct"], office=office, district="",
                            party="", candidate="", votes=num(r, "votes"),
                            election_day=num(r, "election_day"), mail=num(r, "mail"),
                            provisional=num(r, "provisional")))
        else:
            out.append(dict(county="Forest", precinct=r["precinct"], office=office.upper(), district="",
                            party=party, candidate=cand.upper(), votes=num(r, "votes"),
                            election_day=num(r, "election_day"), mail=num(r, "mail"),
                            provisional=num(r, "provisional")))
    out.sort(key=lambda r: (r["precinct"], r["office"], r["party"], r["candidate"]))
    return out


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_forest_general_2025_precinct_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    rows = extract_rows(sys.argv[1])
    out = normalize(rows)
    with open(sys.argv[2], "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["county", "precinct", "office", "district",
                                               "party", "candidate", "votes", "election_day",
                                               "mail", "provisional"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(out)
    print(f"Wrote {len(out)} rows across {len({r['precinct'] for r in out})} precincts")


if __name__ == "__main__":
    main()
