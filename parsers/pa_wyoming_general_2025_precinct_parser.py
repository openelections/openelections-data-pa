#!/usr/bin/env python3
"""
Wyoming County, PA 2025 General Election PRECINCT-level results parser.

Source: "Wyoming PA 2025-Municipal-Totals-v2.pdf". The packet mixes three
page styles:

  - Pages 1-16 (retentions, County judges, Common Pleas 44th JD, school
    districts): sweep tables per precinct. These need image extraction
    (Claude), one page per call -- the candidate column headers are stacked
    and pdftotext loses their column alignment.
  - Pages 17-19 (County Coroner, District Attorney, Treasurer): single
    candidate + SCATTERED column; parsed deterministically from pdftotext
    using the known precinct-name anchors.
  - Pages 20-47: one municipality/ward per page, stacked local contest
    blocks with "Votes Cast" + "Write In" columns and SCATTERED/invalid
    rows; parsed deterministically from pdftotext.

Parsed rows are keyed back to the committed county-level file's (office,
party, candidate) conventions (including its "Write-ins" spelling and
office-name quirks like "BRAINTRIM TOWNSHIP AUDITOR"); the final per-precinct
sums must equal every county-file aggregate -- the script verifies this when
2025/counties/20251104__pa__general__wyoming__county.csv exists.

Requires ANTHROPIC_API_KEY in the environment (pages 1-16 only).

Usage:
    python parsers/pa_wyoming_general_2025_precinct_parser.py <input.pdf> <output.csv>
"""

import base64
import csv
import json
import os
import re
import subprocess
import sys
import tempfile

COUNTY = "Wyoming"
Llm_PROMPT = """This image is one page of Wyoming County, Pennsylvania's totals report for the November 4, 2025 Municipal Election. Each page shows one contest as a sweep table: the contest name is in the top block (possibly wrapped), the candidate column headers may be stacked in several lines under the contest, and every row is "precinct name" followed by one number per candidate column (and sometimes a write-in/scattered column).

Return ONLY a JSON array of objects, one per (precinct, candidate column):
{"precinct": "<precinct as printed>", "office": "<contest, VOTE FOR N dropped; for retention questions use e.g. 'Supreme Court Retention - Christine Donohue'>", "candidate": "<candidate name from that column's header, or 'Write-ins' for the write-in/scattered column>", "party": "<3-letter code if shown>", "votes": <number>}
Skip the TOTALS row. Every precinct row must be included, zero-value cells too."""

PDF = None


def page_text(pg):
    return subprocess.run(["pdftotext", "-layout", "-f", str(pg), "-l", str(pg), PDF, "-"],
                          capture_output=True, text=True).stdout


# ---------- sweep pages 17-19 (single candidate + scattered) ----------
SWEEP_COLS = [(17, "COLLIN K LUNGER", "DEM/REP", "COUNTY CORONER"),
              (18, "JOE PETERS", "DEM/REP", "DISTRICT ATTORNEY"),
              (19, "JUDY BARZILOSKI", "REP", "TREASURER")]
NAMES = ["Braintrim Township", "Clinton Township", "Eaton Township", "Exeter Township",
         "Factoryville Borough Ward 1", "Factoryville Borough Ward 2", "Falls Township",
         "Forkston Township", "Laceyville Borough", "Lemon Township", "Mehoopany Township",
         "Meshoppen Borough", "Meshoppen Township", "Monroe Township",
         "Nicholson Borough at Large", "Nicholson Towhsip", "North Branch Township",
         "Northmoreland Township", "Noxen Township", "Overfield Township",
         "Tunkhannock Borough Ward 1", "Tunkhannock Borough Ward 2",
         "Tunkhannock Borough Ward 3", "Tunkhannock Borough Ward 4",
         "Tunkhannock Township #1", "Tunkhannock Township #2", "Washington Township",
         "Windham Township REG", "Windham Township IND"]
PREC_FIX = {"Nicholson Towhsip": "NICHOLSON TOWNSHIP"}


def parse_sweep_pages():
    rows = []
    for pg, cand, party, office in SWEEP_COLS:
        for ln in page_text(pg).split("\n"):
            s = ln.strip()
            if not s:
                continue
            hit = None
            for name in NAMES:
                pat = re.escape(name).replace(r"\ ", r"\s+")
                m = re.match(r"^" + pat + r"\s+(.*)$", s)
                if m:
                    hit = (name, m.group(1))
                    break
            if not hit:
                continue
            name, rest = hit
            nums = re.findall(r"\d+", rest)
            if not nums:
                continue
            prec = PREC_FIX.get(name, name.replace("#1", " 1").replace("#2", " 2"))
            rows.append(["sweep17", prec, office, party, cand, int(nums[0])])
            rows.append(["sweep17", prec, office, "", "Write-ins", int(nums[1]) if len(nums) > 1 else 0])
    return rows


# ---------- per-municipality pages 20-47 ----------
MUNI_RE = re.compile(r"^\s*([A-Z][A-Z0-9 \.'#\-]*(?:TOWNSHIP|BOROUGH)(?: (?:WARD \d|AT LARGE|REG|IND|#\d))*|TUNKHANNOCK TOWNSHIP \d)\s*$")
OFFICE_RE = re.compile(r"^\s*([A-Z][A-Z0-9 \.'\-#]*?)(?:\s*-{1,2})?\s*VOTE (?:FOR )?(?:ONE|TWO|THREE|FOUR)\s*$")
SKIP_HEADER_RE = re.compile(
    r"^(MUNICIPAL (PRIMARY )?ELECTION.*|.*\b(19|20)\d\d$|DEMOCRATIC.*|.*\bVOTES? CAST\b.*|WRITE ?INS?|SEE WARD.*|-+|\.)\s*$",
    re.IGNORECASE)


def parse_muni_pages(start_page=20):
    rows = []
    for pg in range(start_page, 48):
        txt = page_text(pg)
        lines = txt.split("\n")
        muni = None
        for ln in lines[:12]:
            m = MUNI_RE.match(ln)
            if m:
                muni = m.group(1).strip()
                break
        if not muni:
            continue
        office = None
        for ln in lines:
            s = ln.strip()
            if not s or SKIP_HEADER_RE.match(s):
                continue
            om = OFFICE_RE.match(s)
            if om and "MUNICIPAL" not in s:
                office = om.group(1).strip()
                continue
            if office is None:
                continue
            su = s.upper()
            votes = None
            for num in re.finditer(r"\d+", s):
                rest = s[num.end():]
                prev = s[:num.start()].rstrip()
                if rest.startswith("/"):
                    continue
                if prev.upper().endswith("FILED") or prev.endswith("/"):
                    continue
                votes = int(num.group())
                break
            else:
                continue
            namepart = s[:num.start()].strip()
            namepart = re.sub(r"\bFILED\s+\d+/\d+(/\d+)?\s*$", "", namepart).strip()
            if (not re.search(r"[A-Za-z]", namepart)
                    and not su.startswith("SCAT") and not su.startswith("INVALID")):
                continue
            if su.startswith("SCAT"):
                cand, party = "Write-ins", ""
            elif su.startswith("INVALID"):
                cand, party = "Invalid", ""
            else:
                cand = namepart
                party = ""
                pm = re.match(r"^(.*?)\s+-\s*([A-Z/]{2,8})\s*$", cand)
                if pm:
                    cand, party = pm.group(1).strip(), pm.group(2)
                else:
                    pm = re.match(r"^(.*\S)\s+((?:DEM/REP|REP|DEM|LIB|LBR|GRE|IND|CON|NF|NP))\s*$", cand)
                    if pm:
                        cand, party = pm.group(1).strip(), pm.group(2)
                cand = re.sub(r"\s*-\s*$", "", cand).strip()
            if cand in ("Write-ins", "Invalid"):
                party = ""
            rows.append(["muni", muni, f"{muni.upper()} {office.upper()}", party, cand, votes])
    return rows


# ---------- LLM sweep pages 1-16 ----------
def render_page(png_dir, pg, resolution=150):
    import pdfplumber
    with pdfplumber.open(PDF) as pdf:
        image = pdf.pages[pg - 1].to_image(resolution=resolution)
        path = os.path.join(png_dir, f"p-{pg:02d}.png")
        image.save(path, format="PNG")
        return path


def parse_llm_pages(png_dir):
    import anthropic
    client = anthropic.Anthropic()
    model = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4.5")
    rows = []
    for pg in range(1, 17):
        png = render_page(png_dir, pg)
        content = [{"type": "text", "text": Llm_PROMPT},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.standard_b64encode(open(png, "rb").read()).decode()}}]
        got = None
        for attempt, mt in enumerate((16000, 24000), 1):
            try:
                with client.messages.stream(model=model, max_tokens=mt,
                                            messages=[{"role": "user", "content": content}]) as stream:
                    text = "".join(t for t in stream.text_stream)
                got = json.loads(text[text.find("["):text.rfind("]") + 1])
                break
            except (json.JSONDecodeError, Exception) as exc:
                print(f"page {pg} attempt {attempt}: {type(exc).__name__} {str(exc)[:100]}")
        if got:
            for r in got:
                rows.append(["llm", r.get("precinct"), r.get("office"), r.get("party") or "",
                             r.get("candidate") or "", int(r.get("votes") or 0)])
            print(f"page {pg}: {len(got)} rows")
    return rows


# ---------- keying to the county file's conventions ----------
def county_keys(county_csv):
    keys = set()
    if county_csv and os.path.exists(county_csv):
        with open(county_csv, newline="") as f:
            for r in csv.DictReader(f):
                keys.add((r["office"], r["party"], r["candidate"]))
    return keys


def canonical(rows, keys):
    """Map extracted rows onto the county file's (office, party, candidate)."""
    def canon(s):
        return re.sub(r"[^a-z0-9]", "", s.casefold())

    def toks(s):
        return {t for t in re.split(r"[^a-z0-9]+", s.casefold()) if t}

    KEY_INFO = [(k, toks(k[0]), canon(k[2])) for k in keys]
    word_keys = {canon(w): [k for k in keys if canon(k[2]) == canon(w)] for w in ("Write-ins", "Invalid")}

    ret_map = {
        canon("Supreme Court Retention - Christine Donohue"): "Justice of the Supreme Court Retention - Christine Donahue",
        canon("Supreme Court Retention - Kevin M Dougherty"): "Justice of the Supreme Court Retention - Kevin M Dougherty",
        canon("Supreme Court Retention - David Wecht"): "Justice of the Supreme Court Retention - David Wecht",
        canon("Superior Court Retention - Alice Beck Dubow"): "Judge of the Superior Court Retention - Alice Beck Dubow",
        canon("Commonwealth Court Retention - Michael H Wojcik"): "Judge of the Commonwealth Court Retention - Michael H Wojcik",
    }
    sweep17 = {canon("COUNTY CORONER"), canon("DISTRICT ATTORNEY"), canon("TREASURER")}
    office_map = {"County Coroner": "COUNTY CORONER", "Treasurer": "TREASURER"}
    judge_pages = {canon("Judge of the Superior Court"), canon("Judge of the Commonwealth Court")}

    out = []
    for (_src, prec, office, party, cand, votes) in rows:
        if cand is None:
            continue
        if _src == "muni":
            cand_l = cand.casefold()
            if cand_l in ("see township", "see ward 1"):
                continue
            if re.search(r"(?i)filed", cand):
                cand = re.sub(r"(?i)\s*Filed\s*.*$", "", cand).strip()
            if canon(office) == canon("MUNI OFFICE"):
                continue
        oc = canon(office)
        if _src == "sweep17":
            # keep as parsed; office/candidate already county-style
            out.append([prec, office, party, cand, votes])
            continue
        if oc in ret_map:
            out.append([prec, ret_map[oc], "", "Yes" if cand.casefold() == "yes" else "No", votes])
            continue
        if _src == "llm" and oc in judge_pages:
            off = ("JUDGE OF THE SUPERIOR COURT" if oc == canon("Judge of the Superior Court")
                   else "JUDGE OF THE COMMONWEALTH COURT")
            if canon(cand) in ("writeins", "scattered", "write-in"):
                out.append([prec, off, "", "Write-ins", votes])
            elif oc == canon("Judge of the Superior Court") and canon(cand) == canon("Daniel Wassmer"):
                out.append([prec, off, "LRB", "DANIEL WASSMER", votes])
            else:
                out.append([prec, off, (party or "").upper(), cand.upper(), votes])
            continue
        if _src == "llm":
            # school districts and any other sweep page: candidate-exact key match
            pool = [k for k in keys if canon(k[2]) == canon(cand)]
            if pool:
                off_toks, prec_toks = toks(office), toks(prec or "")
                best = sorted(pool, key=lambda k: -(len(off_toks & toks(k[0])) + len(prec_toks & toks(k[0]))))[0]
                out.append([prec, best[0], best[1], best[2], votes])
                continue
            continue
        # muni pages: match by muni tokens inside the county office name
        prec_toks, off_toks = toks(prec), toks(office)
        cc = canon(cand)
        if cc in ("writeins", "invalid", "scattered"):
            word = "Write-ins" if cc != "invalid" else "Invalid"
            if canon(office) == canon("CONSTABLE 64 YEARS"):
                office = "MUNI CONSTABLE 6 YEARS"
            pool = word_keys[canon(word)]
            scored = sorted(pool, key=lambda k: -(len(off_toks & toks(k[0])) + len(prec_toks & toks(k[0]))))
            if scored and len(off_toks & toks(scored[0][0])) + len(prec_toks & toks(scored[0][0])) >= 2:
                k = scored[0]
                out.append([prec, k[0], k[1], k[2], votes])
            continue
        pool = [k for k in keys if canon(k[2]) == cc and (prec_toks <= toks(k[0]) or len(prec_toks & toks(k[0])) >= 2)]
        if pool:
            best = sorted(pool, key=lambda k: -(len(off_toks & toks(k[0])) + len(prec_toks & toks(k[0]))))[0]
            out.append([prec, best[0], best[1], best[2], votes])
            continue
        print(f"  unmatched: {prec} | {office} | {cand} = {votes}")
    return out


def main():
    global PDF
    if len(sys.argv) != 3:
        print("Usage: python pa_wyoming_general_2025_precinct_parser.py <input.pdf> <output.csv>")
        sys.exit(1)
    PDF = sys.argv[1]
    out_csv = sys.argv[2]
    county_csv = os.path.join("2025", "counties", "20251104__pa__general__wyoming__county.csv")
    keys = county_keys(county_csv)

    raw = parse_sweep_pages() + parse_muni_pages(start_page=20)
    raw += parse_llm_pages(tempfile.mkdtemp())
    canon = canonical(raw, keys)

    final = []
    seen = set()
    for (prec, office, party, cand, votes) in canon:
        prec = PREC_FIX.get(prec, prec.replace("#1", " 1").replace("#2", " 2"))
        prec = re.sub(r"\s+", " ", prec).title().replace("Reg", "REG").replace("Ind", "IND")
        key = (prec, office, party, cand, votes)
        if key in seen:
            continue
        seen.add(key)
        final.append({"county": COUNTY, "precinct": prec, "office": office, "district": "",
                      "party": party, "candidate": cand, "votes": votes,
                      "election_day": "", "mail": "", "provisional": ""})
    final.sort(key=lambda r: (r["precinct"], r["office"], r["party"], r["candidate"]))
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["county", "precinct", "office", "district", "party",
                                               "candidate", "votes", "election_day", "mail", "provisional"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(final)
    print(f"Wrote {len(final)} rows across {len({r['precinct'] for r in final})} precincts to {out_csv}")

    # verify against the county file when available
    if keys:
        sums = {}
        for r in final:
            k = (r["office"], r["party"], r["candidate"])
            sums[k] = sums.get(k, 0) + int(r["votes"])
        bad = 0
        with open(county_csv, newline="") as f:
            for r in csv.DictReader(f):
                k = (r["office"], r["party"], r["candidate"])
                if sums.get(k, 0) != int(r["votes"]):
                    print(f"  MISMATCH {k} county={r['votes']} precinct={sums.get(k, 0)}")
                    bad += 1
        print("verification mismatches:", bad)


if __name__ == "__main__":
    main()
