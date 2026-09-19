#!/usr/bin/env python3
"""Scrape official county-level results from the PA election returns site
(electionreturns.pa.gov) and write them in the OpenElections county-CSV
format:  yyyymmdd__pa__{election_type}__county.csv

Columns: county,office,district,party,candidate,votes,election_day,mail,provisional

Usage:
    # by year + election type
    uv run python parsers/pa_state_enr_county_scraper.py --year 2022 --election-type general
    uv run python parsers/pa_state_enr_county_scraper.py --year 2021 --election-type primary
    # special elections: multiple per year, disambiguate
    uv run python parsers/pa_state_enr_county_scraper.py --year 2022 --election-type special --name-contains 116th
    # or straight from a site URL
    uv run python parsers/pa_state_enr_county_scraper.py \
        --url "https://www.electionreturns.pa.gov/_ENR/General/SummaryResults?ElectionID=94&ElectionType=G&IsActive=0"

    # other options: --out PATH, --overwrite, --list, --election-id N,
    # --delay SECONDS, --quiet

Data source (all under https://www.electionreturns.pa.gov/api):
  - ElectionReturn/GetAllElections              election list (id, type, date)
  - ElectionReturn/GET?methodName=GetSummaryData        statewide offices + Ballot Questions
  - ElectionReturn/GetOfficeNames?countyName=   offices on a county's ballot (with OfficeID)
  - ElectionReturn/GetOfficeData?officeId=      district enumeration (keys "Label$$DistrictId"),
                                                including "(Retention)" contest variants
  - ElectionReturn/GetCountyBreak?officeId=&districtId=   per-county candidate rows (ED/mail/prov splits)
  - ElectionReturn/GetCountyData?countyName=    per-county rows for ballot questions

Not included: county/municipal local offices (the ID-based endpoints carry
only statewide and district offices), and write-in/overvote/undervote rows
(the state site does not publish them). Registered Voters / Ballots Cast
rows are also absent — the API exposes no turnout counts.

Responses are double-encoded JSON (a JSON string containing JSON); rows carry
votes as strings. The site HTML is Incapsula-protected but api/ paths answer
plain requests with a browser User-Agent.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import OrderedDict

BASE = "https://www.electionreturns.pa.gov/api"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

COUNTIES = [
    "Adams", "Allegheny", "Armstrong", "Beaver", "Bedford", "Berks", "Blair",
    "Bradford", "Bucks", "Butler", "Cambria", "Cameron", "Carbon", "Centre",
    "Chester", "Clarion", "Clearfield", "Clinton", "Columbia", "Crawford",
    "Cumberland", "Dauphin", "Delaware", "Elk", "Erie", "Fayette", "Forest",
    "Franklin", "Fulton", "Greene", "Huntingdon", "Indiana", "Jefferson",
    "Juniata", "Lackawanna", "Lancaster", "Lawrence", "Lebanon", "Lehigh",
    "Luzerne",
    "Lycoming", "McKean", "Mercer", "Mifflin", "Monroe", "Montgomery",
    "Montour", "Northampton", "Northumberland", "Perry", "Philadelphia",
    "Pike", "Potter", "Schuylkill", "Snyder", "Somerset", "Sullivan",
    "Susquehanna", "Tioga", "Union", "Venango", "Warren", "Washington",
    "Wayne", "Westmoreland", "Wyoming", "York",
]

# Office-name standardization (CLAUDE.md values)
OFFICE_MAP = {
    "United States Senator": "U.S. Senate",
    "President of the United States": "President",
    "Representative in Congress": "U.S. House",
    "Senator in the General Assembly": "State Senate",
    "Representative in the General Assembly": "State House",
}


def office_name(raw):
    base = re.sub(r"\s*\(Retention\)\s*$", "", raw).strip()
    return OFFICE_MAP.get(base, base)


def norm_county(raw):
    c = raw.strip().title()
    return "McKean" if c.lower() == "mckean" else c


# primaries carry full party names ("Democratic"); repo convention is 3-letter
# codes (CLAUDE.md). Generals already use DEM/REP/LIB.
PARTY_MAP = {
    "Democratic": "DEM", "Republican": "REP", "Libertarian": "LIB",
    "Green": "GRN", "Independent": "IND", "Non Partisan": "NPA",
    "Nonpartisan": "NPA", "Other": "OTH", "Constitution": "CON",
}


def norm_party(raw):
    p = (raw or "").strip()
    if len(p) <= 4:
        return p
    return PARTY_MAP.get(p, p)


def district_number(label):
    """'2nd Senatorial District' -> '2'; 'Statewide' -> ''."""
    m = re.search(r"(\d+)", label or "")
    return m.group(1) if m else ""


def norm_candidate(raw):
    """'FETTERMAN, JOHN K ' -> 'John K. Fetterman'; 'GEORGE, JOSEPH M JR ' ->
    'Joseph M. George Jr'; 'MCCAFFERY, DANIEL D ' -> 'Daniel D. McCaffery'.
    Primary rows carry a trailing party tag — 'KHALIL, ALEXANDRIA GLORIA (DEM)'
    -> 'Alexandria Gloria Khalil'. Passes through anything else."""
    name = " ".join(raw.split())
    # strip trailing "(DEM)"/"(REP)"/... party tag (also glued: "III(REP)")
    name = re.sub(r"\s*\(\s*(dem|rep|lib|grn|ind|oth|npa|con)\s*\)\s*$",
                  "", name, flags=re.I).rstrip()
    suffixes = {"JR": "Jr", "SR": "Sr", "II": "II", "III": "III", "IV": "IV"}

    def fix(tok):
        if len(tok) == 1:
            return tok + "."
        # leave tokens that already contain lowercase (Mc..., O'..., van) as-is
        if any(c.islower() for c in tok[1:]):
            return tok
        if tok.upper() in suffixes:
            return suffixes[tok.upper()]
        t = tok.title()
        # Mccaffery -> McCaffery, Macdonald -> MacDonald
        t = re.sub(r"^(Mc|Mac)(.)", lambda mm: mm.group(1) + mm.group(2).upper(), t)
        return t

    m = re.match(r"^(.+?)\s*,\s*(.+)$", name)
    if not m:
        # no "LAST, FIRST" shape (some specials send "JEN MAZZOCCO"): still
        # normalize case, periods on initials, and a trailing suffix
        toks = [t for t in name.split() if t]
        suffix = ""
        if len(toks) > 1 and toks[-1].upper().rstrip(".") in suffixes:
            suffix = suffixes[toks[-1].upper().rstrip(".")]
            toks = toks[:-1]
        out = " ".join(fix(t) for t in toks)
        out = re.sub(r"(?i)(?<=o')([a-z])", lambda mm: mm.group(1).upper(), out)
        return f"{out} {suffix}".strip()
    last, first = m.group(1), m.group(2)

    first_toks = [t for t in first.split() if t]
    suffix = ""
    if first_toks and first_toks[-1].upper().rstrip(".") in suffixes:
        suffix = suffixes[first_toks[-1].upper().rstrip(".")]
        first_toks = first_toks[:-1]
    out = " ".join(fix(t) for t in first_toks)
    out = re.sub(r"(?i)(?<=o')([a-z])", lambda mm: mm.group(1).upper(), out)
    return f"{out} {fix(last)}" + (f" {suffix}" if suffix else "")


def title_name(raw):
    """For retention rows: 'PANELLA, JACK A' -> 'Jack A Panella'."""
    return norm_candidate(raw)


class Api:
    def __init__(self, delay=0.25, quiet=False):
        import requests
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": UA, "Accept": "application/json"})
        self.delay = delay
        self.quiet = quiet
        self.verify = True
        self._warned_ssl = False

    def get(self, path, params):
        url = f"{BASE}/{path}"
        last = None
        for attempt in range(4):
            try:
                r = self.sess.get(url, params=params, timeout=60, verify=self.verify)
                r.raise_for_status()
                text = r.text
                if "Incapsula" in text[:2000]:
                    raise RuntimeError("Incapsula block page (retry)")
                d = json.loads(text)
                for _ in range(3):          # unwrap double-encoded JSON
                    if isinstance(d, str):
                        try:
                            d = json.loads(d)
                        except json.JSONDecodeError:
                            break
                    else:
                        break
                if isinstance(d, str):      # e.g. response body is '""'
                    d = {}
                time.sleep(self.delay)
                return d
            except Exception as e:
                name = type(e).__name__
                if name in ("SSLError", "SSLCertVerificationError"):
                    if not self._warned_ssl:
                        import urllib3
                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                        print("warning: site certificate not trusted by this "
                              "python; continuing without verification",
                              file=sys.stderr)
                        self._warned_ssl = True
                    self.verify = False
                    continue
                last = e
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after 4 attempts: {last}")


def list_elections(api):
    d = api.get("ElectionReturn/GetAllElections", {"methodName": "x"})
    if isinstance(d, list) and d and isinstance(d[0], dict) and "ElectionData" in d[0]:
        d = json.loads(d[0]["ElectionData"])
        if isinstance(d, str):
            d = json.loads(d)
    return d


def resolve_election(api, args):
    etype_map = {"primary": "P", "general": "G", "special": "S"}
    elections = list_elections(api)
    if args.url:
        q = dict(re.findall(r"([A-Za-z]+)=([^&]+)", args.url))
        eid = q.get("ElectionID", q.get("ElectionId", q.get("Electionid", "")))
        etype = (q.get("ElectionType") or etype_map.get(args.election_type or "", "")
                 or "").upper()
        if etype not in ("P", "G", "S"):
            sys.exit("error: cannot determine election type from URL "
                     "(ElectionType=P|G|S) or --election-type")
        match = [e for e in elections
                 if e["Electionid"] == str(eid) and e["ElectionType"] == etype]
        if not match:
            sys.exit(f"error: URL id {eid} type {etype} not found in GetAllElections")
        sel = match[0]
        isactive = q.get("IsActive", sel.get("ISActive", "0"))
        return sel, etype, isactive
    else:
        if not args.election_type:
            sys.exit("error: --election-type is required without --url")
        etype = etype_map[args.election_type]
        # --election-id is decisive; --year is only an extra filter
        match = [e for e in elections
                 if e["ElectionType"] == etype
                 and (args.year is None or e["ElectionYear"] == str(args.year))]
        if args.name_contains:
            n = args.name_contains.lower()
            match = [e for e in match if n in e["ElectionName"].lower()]
        if args.election_id:
            match = [e for e in match if e["Electionid"] == str(args.election_id)]
        if not match:
            for e in elections:
                if e["ElectionYear"] == str(args.year):
                    print("  available:", e["Electionid"], e["ElectionType"],
                          e["ElectionName"], e["ElectionDate"])
            sys.exit(f"error: no {args.election_type} election matches year "
                     f"{args.year} (see 'available' list above)")
        if len(match) > 1:
            for e in match:
                print("  match:", e["Electionid"], e["ElectionName"],
                      e["ElectionDate"])
            sys.exit("error: multiple matches — add --name-contains or --election-id")
        sel = match[0]
    return sel, etype, sel.get("ISActive", "0")


def collect_offices(api, eid, etype, isactive, quiet):
    """Union of OfficeID/OfficeName over all counties (statewide + district offices)."""
    offices = OrderedDict()
    for county in COUNTIES:
        d = api.get("ElectionReturn/GetOfficeNames",
                    {"countyName": county, "methodName": "GetOfficeNames",
                     "electionid": eid, "electiontype": etype, "isactive": isactive})
        table = d.get("Table", []) if isinstance(d, dict) else (d or [])
        for o in table:
            oid = str(o["OfficeID"])
            offices.setdefault(oid, o["OfficeName"])
    items = sorted(offices.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999)
    if not quiet:
        print(f"offices (union over counties): {[v for _, v in items]}")
    return OrderedDict(items)


def collect_contests(api, eid, etype, isactive, offices, quiet):
    """Each contest: {office_id, office_raw, district_id, district_label}."""
    contests = []
    for oid, oname in offices.items():
        d = api.get("ElectionReturn/GetOfficeData",
                    {"officeId": oid, "methodName": "GetOfficeData",
                     "electionid": eid, "electiontype": etype, "isactive": isactive})
        el = d.get("Election") or {}
        for contest_name, blocks in el.items():
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                for key, payload in block.items():
                    # keys look like "116th Legislative District$$188$$" or
                    # "Statewide$$1$$" — label$$DistrictId$$ with a trailing $$
                    if "$$" not in key:
                        continue
                    label, did = key.rstrip("$").rsplit("$$", 1)
                    contests.append({"office_id": oid, "office_raw": contest_name,
                                     "district_id": did,
                                     "district_label": label.strip()})
    # contested and "(Retention)" variants of an office share the same
    # districtId and GetCountyBreak returns one combined payload for both —
    # keep a single contest per (office_id, district_id), preferring the
    # non-retention office name
    best, order = {}, []
    for c in contests:
        k = (c["office_id"], c["district_id"])
        if k not in best:
            best[k] = c
            order.append(k)
        elif ("(Retention)" not in c["office_raw"]
              and "(Retention)" in best[k]["office_raw"]):
            best[k] = c
    contests = [best[k] for k in order]
    if not quiet:
        print(f"contests: {len(contests)}")
    return contests


def walk_county_rows(obj, out):
    """Collect every dict carrying CountyName plus votes fields, at any depth."""
    if isinstance(obj, dict):
        if "CountyName" in obj and ("CandidateName" in obj or "YesVotes" in obj):
            out.append(obj)
            return
        for v in obj.values():
            walk_county_rows(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_county_rows(v, out)


def county_rows_for(api, eid, etype, isactive, contest):
    d = api.get("ElectionReturn/GetCountyBreak",
                {"officeId": contest["office_id"], "districtId": contest["district_id"],
                 "methodName": "GetCountyBreak", "electionid": eid,
                 "electiontype": etype, "isactive": isactive})
    rows = []
    walk_county_rows(d, rows)
    return rows


def _walk_candidate_rows(obj, out):
    """Candidate rows (with Votes) — not retention Yes/No or question rows."""
    if isinstance(obj, dict):
        if "CandidateName" in obj and "Votes" in obj:
            out.append(obj)
            return
        for v in obj.values():
            _walk_candidate_rows(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_candidate_rows(v, out)


def fill_breakdowns(api, eid, etype, isactive, contest, rows, quiet):
    """GetCountyBreak sometimes reports all-zero ED/Mail/Prov splits for
    archived elections even though per-county GetCountyData carries real
    breakdowns. When that happens, refill from GetCountyData, matching rows
    by county + party + candidate."""
    def zero(r):
        return (int(r.get("ElectionDayVotes") or 0) + int(r.get("MailInVotes") or 0)
                + int(r.get("ProvisionalVotes") or 0))
    if not rows or any(zero(r) for r in rows) or not any(int(r.get("Votes") or 0) for r in rows):
        return
    if not quiet:
        print("  breakdowns all zero — refilling from GetCountyData")
    for county in sorted({r.get("CountyName") or "" for r in rows}):
        if not county:
            continue
        d = api.get("ElectionReturn/GetCountyData",
                    {"countyName": county, "methodName": "GetCountyData",
                     "electionid": eid, "electiontype": etype, "isactive": isactive})
        cands = []
        _walk_candidate_rows(d, cands)
        by_key = {}
        for cr in cands:
            key = ((cr.get("PartyName") or "").strip().upper(),
                   (cr.get("CandidateName") or "").strip().upper())
            by_key[key] = cr
        for r in rows:
            if (r.get("CountyName") or "").strip().upper() != county.strip().upper():
                continue
            cr = by_key.get(((r.get("PartyName") or "").strip().upper(),
                             (r.get("CandidateName") or "").strip().upper()))
            if cr is None:
                continue
            for f in ("ElectionDayVotes", "MailInVotes", "ProvisionalVotes"):
                if cr.get(f):
                    r[f] = cr[f]


def county_questions(api, eid, etype, isactive, quiet):
    """Ballot questions come from GetSummaryData (statewide) and GetCountyData
    (per-county Yes/No rows)."""
    result = {}
    def fetch(county):
        d = api.get("ElectionReturn/GetCountyData",
                    {"countyName": county, "methodName": "GetCountyData",
                     "electionid": eid, "electiontype": etype, "isactive": isactive})
        el = d.get("Election") or {}
        # Election is usually a list of one county-keyed dict, but some
        # counties return the dict directly.
        if isinstance(el, list):
            el = el[0] if el and isinstance(el[0], dict) else {}
        if not isinstance(el, dict):
            return
        block = None
        for k, v in el.items():
            if k.upper() == county.upper():
                block = v
                break
        if isinstance(block, list):
            block = block[0] if block and isinstance(block[0], dict) else {}
        if not isinstance(block, dict):
            return
        qs = block.get("Ballot Questions") or []
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            qlist = qs[0].get("Questions") or []
            if qlist:
                result[county] = qlist

    for county in COUNTIES:
        fetch(county)
    # the API intermittently returns empty responses; retry any county that
    # came back with nothing (only if questions exist somewhere)
    if result:
        for county in COUNTIES:
            if county not in result:
                fetch(county)
    return result


def build_rows(contests_data, questions):
    """-> list of (sort_idx, county, office, district, party, candidate,
                   votes, election_day, mail, provisional)"""
    per_county = {}
    for idx, (contest, rows) in enumerate(contests_data):
        base = office_name(contest["office_raw"])
        is_ret = "(Retention)" in contest["office_raw"]
        district = "" if is_ret else district_number(contest["district_label"])
        for r in rows:
            county = norm_county(r.get("CountyName") or "")
            if not county:
                continue
            if (r.get("PartyName") or "").strip() == "Retention":
                person = title_name(r.get("CandidateName") or "")
                # repo convention drops the "Judge of the "/"Justice of the "
                # prefix on retention office names
                ret_base = re.sub(r"^(Judge|Justice) of the ", "", base)
                office = f"{ret_base} Retention - {person}"
                for side in ("Yes", "No"):
                    votes = int(r.get(f"{side}Votes") or 0)
                    ed = int(r.get(f"ElectionDay{side}Votes") or 0)
                    mail = int(r.get(f"MailIn{side}Votes") or 0)
                    prov = int(r.get(f"Provisional{side}Votes") or 0)
                    ok = votes == 0 or ed + mail + prov == votes
                    row = (idx, county, office, "", "", side, str(votes),
                           str(ed) if ok else "", str(mail) if ok else "",
                           str(prov) if ok else "")
                    per_county.setdefault(county, []).append(row)
                continue
            cand = norm_candidate(r.get("CandidateName") or "")
            votes = int(r.get("Votes") or 0)
            ed = int(r.get("ElectionDayVotes") or 0)
            mail = int(r.get("MailInVotes") or 0)
            prov = int(r.get("ProvisionalVotes") or 0)
            # if breakdowns are entirely zero but votes aren't (older
            # elections), leave breakdown columns blank so the
            # vote_breakdown_totals test doesn't read zeros as real counts
            ok = votes == 0 or ed + mail + prov == votes
            blank = votes != 0 and ed + mail + prov == 0
            row = (idx, county, base, district, norm_party(r.get("PartyName")),
                   cand, str(votes),
                   str(ed) if ok and not blank else "",
                   str(mail) if ok and not blank else "",
                   str(prov) if ok and not blank else "")
            per_county.setdefault(county, []).append(row)

    qidx = len(contests_data)
    for county, qs in questions.items():
        for j, q in enumerate(qs):
            office = q.get("Title") or "Ballot Question"
            for side in ("Yes", "No"):
                votes = int(q.get(f"{side}Votes") or 0)
                ed = int(q.get(f"ElectionDay{side}Votes") or 0)
                mail = int(q.get(f"MailIn{side}Votes") or 0)
                prov = int(q.get(f"Provisional{side}Votes") or 0)
                row = (qidx + j, county, office, "", "", side, str(votes),
                       str(ed), str(mail), str(prov))
                per_county.setdefault(county, []).append(row)

    rows = []
    for county in sorted(per_county):
        # Yes/No rows (retentions, questions) listed Yes first
        rows.extend(sorted(per_county[county],
                           key=lambda t: (t[0], t[3], t[5] != "Yes", t[5])))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", help="electionreturns.pa.gov results URL")
    p.add_argument("--year", help="election year")
    p.add_argument("--election-type", choices=["primary", "general", "special"])
    p.add_argument("--name-contains", help="disambiguate specials by name substring")
    p.add_argument("--election-id", help="disambiguate by ElectionID")
    p.add_argument("--out", help="output CSV path (default: <year>/<date>__pa__<type>__county.csv)")
    p.add_argument("--overwrite", action="store_true", help="overwrite existing output")
    p.add_argument("--list", action="store_true", help="list known elections and exit")
    p.add_argument("--delay", type=float, default=0.25, help="seconds between API calls")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    api = Api(delay=args.delay, quiet=args.quiet)

    if args.list:
        for e in sorted(list_elections(api),
                        key=lambda x: (x["ElectionYear"], x["ElectionType"],
                                       x.get("ElectionDate", ""))):
            print(e["ElectionYear"], e["Electionid"], e["ElectionType"],
                  e["ElectionDate"], e["ElectionName"])
        return

    if not (args.url or (args.election_id and args.election_type)
            or (args.year and args.election_type)):
        p.error("provide --url, or --year and --election-type")

    sel, etype, isactive = resolve_election(api, args)
    eid = sel["Electionid"]
    mm, dd, yy = sel["ElectionDate"].split("/")
    date = f"{yy}{mm}{dd}"
    if not args.quiet:
        print(f"election {eid}/{etype}: {sel['ElectionName']} ({sel['ElectionDate']})")

    if args.out:
        out_path = args.out
    elif etype == "P":
        out_path = f"{yy}/{date}__pa__primary__county.csv"
    elif etype == "G":
        out_path = f"{yy}/{date}__pa__general__county.csv"
    else:
        out_path = None  # decided after contests are known

    # --- enumerate contests -------------------------------------------------
    summary = api.get("ElectionReturn/GET",
                      {"methodName": "GetSummaryData", "electionid": eid,
                       "electiontype": etype, "isactive": isactive}).get("Election", {})
    if not args.quiet:
        print(f"summary contests: {[k for k in summary if k != 'Ballot Questions']}")

    offices = collect_offices(api, eid, etype, isactive, args.quiet)
    contests = collect_contests(api, eid, etype, isactive, offices, args.quiet)

    contests_data = []
    for c in contests:
        rows = county_rows_for(api, eid, etype, isactive, c)
        fill_breakdowns(api, eid, etype, isactive, c, rows, args.quiet)
        contests_data.append((c, rows))
        if not args.quiet:
            print(f"  {office_name(c['office_raw'])} [{c['district_label']}] "
                  f"counties: {len(rows)}")

    questions = {}
    if "Ballot Questions" in summary:
        if not args.quiet:
            print("ballot questions found — fetching per-county question rows")
        questions = county_questions(api, eid, etype, isactive, args.quiet)

    rows = build_rows(contests_data, questions)
    if not rows:
        sys.exit("error: no rows retrieved")

    # --- special-election naming -------------------------------------------
    if etype == "S" and not args.out:
        if len(contests_data) == 1 and not questions:
            c = contests_data[0][0]
            slug = re.sub(r"[^a-z0-9]+", "_", office_name(c["office_raw"]).lower()).strip("_")
            dist = (district_number(c["district_label"])
                    or re.sub(r"[^a-z0-9]+", "_", c["district_label"].lower()).strip("_"))
            out_path = f"{yy}/{date}__pa__special__general__{slug}__{dist}.csv"
        else:
            out_path = f"{yy}/{date}__pa__special__county.csv"

    # --- write --------------------------------------------------------------
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0 and not args.overwrite:
        sys.exit(f"error: {out_path} exists (use --overwrite to replace it)")
    d = os.path.dirname(out_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "office", "district", "party", "candidate",
                    "votes", "election_day", "mail", "provisional"])
        for r in rows:
            w.writerow(r[1:])

    if not args.quiet:
        print(f"wrote {len(rows)} rows -> {out_path}")
        print("next: run the four repo data tests on the output, e.g.")
        print(f"  python3 ~/code/openelections-data-tests/run_tests.py file_format . --files {out_path}")


if __name__ == "__main__":
    main()