#!/usr/bin/env python3
"""
Warren County, PA 2025 General Election PRECINCT-level results parser.

Source: "Warren PA 20251124115652415.pdf" -- a scanned (no text layer)
Election Summary Report printed per precinct, ~7 pages per precinct
(35 sub-reports / 245 pages). Page 1 of each sub-report carries the
precinct name in "Summary for: All Contests, <PRECINCT>, All Tabulators";
later pages do not repeat it, so pages are grouped by sub-report and sent
to Claude together (image extraction, ANTHROPIC_API_KEY required).

The committed county-level file (built from "Warren PA Official
Results_1.pdf") carries local contests keyed by (office, district, party,
candidate) and omits write-in rows entirely, so parsed rows are keyed to
those conventions and verified against it when available.

Usage:
    python parsers/pa_warren_general_2025_precinct_parser.py <input.pdf> <output.csv>
"""

import base64
import csv
import json
import os
import re
import subprocess
import sys
import tempfile

COUNTY = "Warren"
COUNTY_CSV = os.path.join("2025", "counties", "20251104__pa__general__warren__county.csv")

PROMPT = """These are consecutive pages of one precinct's Election Summary Report from the {precinct_name} precinct, Warren County, Pennsylvania, 2025 Municipal General Election (November 4, 2025).

Extract EVERY contest on these pages with its results table. For each contest output:
- office: the contest name without "(Vote for N)" and without term lengths, e.g. "Judge of the Superior Court". For retention questions use "<Court> Court Retention - <Judge Name>" (the source prints e.g. "JUSTICE OF THE SUPREME COURT RETENTION CHRISTINE DONOHUE").
- district: only when the office names a ward/district/region; otherwise empty string.
- party: the party code shown next to the candidate (DEM, REP, DEM/REP, LIB, etc.), empty for Yes/No and Write-in rows.
- candidate: the candidate name; for a retention question the choices are Yes and No; for write-in rows use "Write-In"; for "Unresolved Write-in" rows use "Unresolved Write-In".
- votes: the Total column value.
- election_day, mail, provisional: the Election Day, Mail-In, Provisional columns.
- precinct: exactly "{precinct_name}".

Rules:
- Skip TimesCast/Times Cast, Undervotes, Overvotes and "Precincts Reported" rows.
- Take candidate values only from the candidate block (Election Day, Mail-In, Provisional, Total).
- One row per page header: office="Registered Voters" votes=<first number>, and office="Ballots Cast" votes=<Ballots Cast>.

Return ONLY a JSON array of objects with keys county ("Warren"), precinct, office, district, party, candidate, votes, election_day, mail, provisional."""


def group_pages_png(png_dir):
    """Group rendered pages into sub-reports by OCR of page 1 headers (the
    'Summary for:' line). Return list of {precinct, pages}."""
    import subprocess
    groups = []
    pages = sorted(p for p in os.listdir(png_dir) if p.endswith(".png"))
    for base in pages:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as t:
            out = t.name
        subprocess.run(["tesseract", os.path.join(png_dir, base), out[:-4], "--psm", "6"],
                       capture_output=True)
        txt = open(out[:-4] + ".txt", errors="ignore").read()
        os.remove(out[:-4] + ".txt")
        m = re.search(r"Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators", txt)
        if m:
            groups.append({"precinct": m.group(1).strip(), "pages": [base]})
        elif groups:
            groups[-1]["pages"].append(base)
    return groups


def extract(pdf_path):
    import anthropic
    import pdfplumber
    png_dir = tempfile.mkdtemp()
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            image = pg.to_image(resolution=150)
            image.save(os.path.join(png_dir, f"p-{i:03d}.png"), format="PNG")
    groups = group_pages_png(png_dir)
    print(f"groups: {len(groups)}")
    client = anthropic.Anthropic()
    model = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4.5")
    all_rows = []
    for gi, g in enumerate(groups):
        content = [{"type": "text", "text": PROMPT.format(precinct_name=g["precinct"])}]
        for p in g["pages"]:
            with open(os.path.join(png_dir, p), "rb") as fh:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(fh.read()).decode()}})
        rows = None
        for attempt, mt in enumerate((16000, 32000), 1):
            try:
                with client.messages.stream(model=model, max_tokens=mt,
                                            messages=[{"role": "user", "content": content}]) as stream:
                    text = "".join(t for t in stream.text_stream)
                rows = json.loads(text[text.find("["):text.rfind("]") + 1])
                break
            except Exception as exc:
                print(f"{g['precinct']} attempt {attempt}: {type(exc).__name__} {str(exc)[:100]}")
        if rows:
            for r in rows:
                r["precinct"] = g["precinct"]
            all_rows.extend(rows)
            print(f"[{gi}] {g['precinct']}: {len(rows)} rows")
        else:
            print(f"[{gi}] {g['precinct']}: FAILED")
    return all_rows


def page_headers_png(png_dir, precincts):
    """ precinct -> (ballots, registered) parsed from each group's page-1
    header: 'Registered Voters: <ballots> of <registered> (<pct>%)'."""
    import subprocess
    out = {}
    pages = sorted(p for p in os.listdir(png_dir) if p.endswith(".png"))
    for base in pages:
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as t:
            o = t.name
        subprocess.run(["tesseract", os.path.join(png_dir, base), o[:-4], "--psm", "6"],
                       capture_output=True)
        txt = open(o[:-4] + ".txt", errors="ignore").read()
        os.remove(o[:-4] + ".txt")
        m = re.search(r"Summary for:\s*All Contests,\s*(.+?),\s*All Tabulators", txt)
        if not m:
            continue
        m1 = re.search(r"Registered Voters:\s*([\d,]+)\s*of\s*([\d,]+)\s*\(\s*([\d.,]+)%", txt)
        mb = re.search(r"Ballots Cast:\s*([\d,]+)", txt)
        if m1 and mb:
            ballots = int(m1.group(1).replace(",", ""))
            reg = int(m1.group(2).replace(",", ""))
            bc = int(mb.group(1).replace(",", ""))
            if abs(ballots / reg * 100 - float(m1.group(3).replace(",", "."))) < 0.6 and ballots == bc:
                out[m.group(1).strip()] = (ballots, reg)
    return out


def canonical(rows, county_rows):
    """Key rows to the county file's (office, district, party, candidate)."""
    def canon(s):
        return re.sub(r"[^a-z0-9]", "", s.casefold())

    def toks(s):
        return {t for t in re.split(r"[^a-z0-9]+", s.casefold()) if t}

    agg = {}
    for r in county_rows:
        k = (r["office"], r["district"], r["party"], r["candidate"])
        agg[k] = agg.get(k, 0) + int(r["votes"])
    by_cand = {}
    for k in agg:
        by_cand.setdefault(canon(k[3]), []).append(k)

    mapped = []
    for r in rows:
        prec = (r.get("precinct") or "").strip()
        off = re.sub(r"\s+", " ", (r.get("office") or "").strip())
        cand = (r.get("candidate") or "").strip()
        votes = int(r.get("votes") or 0)
        ed, mi, pr = r.get("election_day"), r.get("mail"), r.get("provisional")
        if not prec or not off or not cand or votes == 0:
            continue
        if off in ("Registered Voters", "Ballots Cast"):
            continue  # handled via page headers
        if canon(cand) in ("unresolvedwritein", "writein", "writeins", "scattered"):
            mapped.append({"precinct": prec, "office": off, "district": "", "party": "",
                           "candidate": "Unresolved Write-In" if canon(cand) == "unresolvedwritein" else "Write-In",
                           "votes": votes, "election_day": ed, "mail": mi, "provisional": pr})
            continue
        pool = by_cand.get(canon(cand), [])
        if not pool:
            continue
        def score(k):
            return (len(toks(off) & toks(k[0])) + len(toks(prec) & toks(k[0]))
                    + len(toks(prec) & toks(k[1])))
        best = sorted(pool, key=lambda k: -score(k))[0]
        mapped.append({"precinct": prec, "office": best[0], "district": best[1],
                       "party": best[2], "candidate": best[3], "votes": votes,
                       "election_day": ed, "mail": mi, "provisional": pr})
    return mapped


def main():
    if len(sys.argv) != 3:
        print("Usage: python pa_warren_general_2025_precinct_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    rows = extract(sys.argv[1])
    county_rows = []
    if os.path.exists(COUNTY_CSV):
        with open(COUNTY_CSV, newline="") as f:
            county_rows = list(csv.DictReader(f))
    mapped = canonical(rows, county_rows)
    final = []
    for r in mapped:
        final.append({"county": COUNTY, "precinct": r["precinct"], "office": r["office"],
                      "district": r["district"], "party": r["party"], "candidate": r["candidate"],
                      "votes": r["votes"], "election_day": r["election_day"] or "",
                      "mail": r["mail"] or "", "provisional": r["provisional"] or ""})
    final.sort(key=lambda r: (r["precinct"], r["office"], r["candidate"]))
    with open(sys.argv[2], "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["county", "precinct", "office", "district", "party",
                                               "candidate", "votes", "election_day", "mail", "provisional"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(final)
    print(f"Wrote {len(final)} rows to {sys.argv[2]}")


if __name__ == "__main__":
    main()
