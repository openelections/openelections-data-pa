#!/usr/bin/env python3
"""Parse the Lehigh County 2023 General Election "Official Results" HTML page
into the standard OpenElections precinct CSV.

Usage: python pa_lehigh_general_2023_results_parser.py <input.html> <output.csv> [--county Lehigh]

The page embeds per-precinct results for every race in
div.list-item-precinct-race blocks (candidate name + total votes only), and a
precinct turnout JSON (`var precinctData = '...'`) with per-precinct
registered voters and check-ins (= ballots cast). Countywide race totals with
election-day / mail / provisional breakdowns are parsed by --county-file mode
(see --county-output) so the aggregated county CSV can carry the official
breakdowns; the precinct CSV itself reports totals only, matching the source.

Output columns:
county,precinct,office,district,party,candidate,votes,election_day,mail,provisional
"""

import argparse
import csv
import json
import re

from bs4 import BeautifulSoup

TERM = {"2YR": "2 Year", "4YR": "4 Year", "6YR": "6 Year"}


def norm_office(title):
    """Map an uppercase contest title to (office, district)."""
    c = re.sub(r"\s+", " ", title).strip()

    m = re.match(r"^MAGISTERIAL DISTRICT JUDGE (\d{2}-\d-\d+)$", c)
    if m:
        return "Magisterial District Judge", m.group(1)

    m = re.match(r"^(.*?) SCHOOL DIRECTOR(?: (AT LARGE|REGION [IVX]+|\d+YR))?$",
                 c, re.I)
    if m:
        dist = m.group(1).title().replace("/", "/")
        suffix = m.group(2)
        if not suffix:
            return "School Director", dist
        s = suffix.upper()
        if s == "AT LARGE":
            return "School Director At Large", dist
        if s.startswith("REGION"):
            return "School Director", f"{dist} {suffix}"
        return f"School Director ({TERM[s]})", dist

    m = re.match(r"^(.*?) BOROUGH COUNCIL(?: (\d+YR))?$", c, re.I)
    if m:
        office = "Borough Council"
        if m.group(2):
            office += f" ({TERM[m.group(2).upper()]})"
        return office, m.group(1).title()

    m = re.match(r"^(.*?) TOWNSHIP COUNCIL (\d+YR)$", c, re.I)
    if m:
        return f"Township Council ({TERM[m.group(2).upper()]})", m.group(1).title()

    m = re.match(r"^(.*?)(?: TOWNSHIP)? (TOWNSHIP SUPERVISOR|AUDITOR)(?: (\d+YR))?$",
                 c, re.I)
    if m:
        base = "Township Supervisor" if "SUPERVISOR" in m.group(2).upper() else "Auditor"
        dist = m.group(1).title()
        if m.group(1).upper().endswith(" TOWNSHIP"):
            dist = dist[: -len(" Township")].strip()
        if m.group(3):
            office = f"{base_name(base)} ({TERM[m.group(3).upper()]})"
        else:
            office = base
        return office, dist

    m = re.match(r"^(.*?) TOWNSHIP COMMISSIONER(?: (.*?))?$", c, re.I)
    if m:
        base = m.group(1).title()
        dist = (m.group(2) or "").strip()
        if not dist:
            dist = base
        elif base.lower() == "salisbury":
            # ward label: keep the municipality ("Salisbury 1st Ward"), and
            # avoid duplicating it when the source already repeats it
            if not dist.lower().startswith("salisbury"):
                dist = f"Salisbury {dist}"
        else:
            dist = f"{base} {dist}".strip()
        return "Township Commissioner", dist

    m = re.match(r"^(.*?) TOWNSHIP MAYOR$", c, re.I)
    if m:
        return "Mayor", m.group(1).title()

    m = re.match(r"^(.*?)(?: CITY)? (CITY COUNCIL|CITY CONTROLLER|CITY TREASURER)$",
                 c, re.I)
    if m:
        dist = m.group(1).title()
        office = "City " + m.group(2).split()[1].title()
        return office, dist

    m = re.match(r"^(.*?) TAX COLLECTOR$", c, re.I)
    if m:
        return "Tax Collector", m.group(1).title()

    m = re.match(r"^(.*?) REFERENDUM (\d+)$", c, re.I)
    if m:
        return f"Referendum {m.group(2)}", m.group(1).title()

    m = re.match(r"^(SUPERIOR|COMMONWEALTH) COURT - RETAIN (.*)$", c, re.I)
    if m:
        court = "Superior" if m.group(1).lower() == "superior" else "Commonwealth"
        return f"{court} Court Retention Election Question ({m.group(2).title()})", ""
    m = re.match(r"^COMMON PLEAS COURT - RETAIN (.*)$", c, re.I)
    if m:
        return f"Judge of the Court of Common Pleas Retention Election Question ({m.group(1).title()})", ""

    countywide = {
        "JUSTICE OF THE SUPREME COURT": "Justice of the Supreme Court",
        "JUDGE OF THE SUPERIOR COURT": "Judge of the Superior Court",
        "JUDGE OF THE COMMONWEALTH COURT": "Judge of the Commonwealth Court",
        "COUNTY COMMISSIONER-AT LARGE": "County Commissioner",
        "COUNTY CONTROLLER": "County Controller",
        "DISTRICT ATTORNEY": "District Attorney",
        "SHERIFF": "Sheriff",
        "CLERK OF JUDICIAL RECORDS": "Clerk of Judicial Records",
        "CORONER": "Coroner",
    }
    if c in countywide:
        return countywide[c], ""

    # Fallback: title-case the whole header, no district.
    return c.title().replace("Of The", "of the"), ""


def base_name(base):
    return "Township Supervisor" if "SUPERVISOR" in base.upper() else base


PARTY_FROM_CODE = {"DEM": "DEM", "REP": "REP", "DAR": "D/R", "LBR": "LBR",
                   "GRN": "GRN", "CON": "CON", "LIB": "LIB", "IND": "IND"}


def parse_label(label):
    """'DEM Daniel McCaffery (DEM)' / 'Write-in (NON)' / 'Yes (NON)' ->
    (candidate, party)."""
    label = re.sub(r"\s+", " ", label).strip()
    m = re.match(r"^(.*?)\s*\(([A-Z/]+)\)$", label)
    if m:
        name, party_raw = m.group(1).strip(), m.group(2)
    else:
        name, party_raw = label, ""
    party = ""
    if party_raw == "NON":
        party = ""
    elif party_raw:
        party = PARTY_FROM_CODE.get(party_raw, party_raw)
    # strip a leading party code from the name if duplicated
    toks = name.split(" ", 1)
    if len(toks) == 2 and toks[0] in PARTY_FROM_CODE and (not party or party_raw == toks[0]):
        name = toks[1]
        if not party:
            party = PARTY_FROM_CODE[toks[0]]
    if name.lower() == "write-in":
        return "Write-ins", ""
    return name, party


def candidate_rows(container):
    """Yield (candidate, party, votes) from a results table container."""
    for tr in container.select("tr.content-row"):
        cell = tr.select_one('td[dc="true"], td[data-candidate="true"]')
        votes_td = tr.select_one('td[dv="true"], td[data-votes="true"]')
        if cell is None or votes_td is None:
            continue
        span = cell.select_one('span[aria-hidden="true"]')
        label = span.get_text(strip=True) if span else cell.get_text(" ", strip=True)
        name, party = parse_label(label)
        votes = re.sub(r"[^0-9]", "", votes_td.get_text(strip=True)) or "0"
        yield name, party, votes


def parse_html(path, county="Lehigh"):
    with open(path, encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    html = soup.decode()

    # --- per-precinct turnout / registration ---
    m = re.search(r"var precinctData = '(.*?)';", html, re.S)
    turnout = {}
    if m:
        turnout = json.loads(m.group(1))

    # --- per-precinct race results ---
    # precinct sections: <span class="precinct-title">Name- CODE</span> followed
    # by div#pC_N with div#pRL_N_RACE blocks
    rows = []
    for span in soup.find_all("span", class_="precinct-title"):
        title = span.get_text(" ", strip=True)
        code_m = re.search(r"-\s*(\d+)\s*$", title)
        code = code_m.group(1) if code_m else title
        info = turnout.get(code, {})
        precinct = info.get("PrecinctName", title)

        heading_a = span.find_parent("a")
        if heading_a is None:
            continue
        container = heading_a.find_next_sibling("div")
        if container is None:
            continue

        # metadata rows from turnout JSON
        rv = info.get("VoterCount", "")
        bc = info.get("RegularCheckIns", "")
        for office, votes in (("Registered Voters", rv), ("Ballots Cast", bc)):
            if votes != "":
                rows.append({"county": county, "precinct": precinct,
                             "office": office, "district": "", "party": "",
                             "candidate": "", "votes": votes,
                             "election_day": "", "mail": "", "provisional": ""})

        for block in container.select("div.list-item-precinct-race"):
            rt = block.select_one("span.race-title")
            if rt is None:
                continue
            race_title = rt.get_text(" ", strip=True)
            # strip the "Precinct - " prefix (everything up to ' - ' where the
            # head matches the precinct title)
            if race_title.startswith(title):
                race_title = race_title[len(title):].lstrip("- ").strip()
            office, district = norm_office(race_title)
            for name, party, votes in candidate_rows(block):
                rows.append({"county": county, "precinct": precinct,
                             "office": office, "district": district,
                             "party": party, "candidate": name, "votes": votes,
                             "election_day": "", "mail": "",
                             "provisional": ""})
    return rows, soup, html


def county_race_data(soup, html):
    """Race-level (countywide) rows with ED/mail/provisional breakdowns."""
    out = []
    layer = soup.find(id="contestListLayer") or soup
    for item in layer.find_all("div", id=re.compile(r"^listid_\d+$")):
        title = item.find("span", class_="race-title").get_text(" ", strip=True)
        office, district = norm_office(title)
        # candidate labels + votes from the visible table
        candidates = []
        for tr in item.select(".table-results tr.content-row"):
            span = tr.select_one('td[data-candidate="true"] span[aria-hidden="true"]')
            votes_td = tr.select_one('td[data-votes="true"]')
            if span is None or votes_td is None:
                continue
            name, party = parse_label(span.get_text(strip=True))
            votes = re.sub(r"[^0-9]", "", votes_td.get_text(strip=True)) or "0"
            candidates.append((name, party, votes))
        # breakdowns from the embedded pie-chart array (inside HTML comments,
        # so parse the raw HTML): [['Name', 0, ED, mail, prov, total], ...]
        rid_target = int(item["id"].split("_")[1])
        breaks = {}
        for m in re.finditer(r"\[\[(.*?)\]\]", html, re.S):
            rid_m = re.search(r"var RaceIdVal = (\d+)", html[m.end():m.end() + 500])
            if not rid_m or int(rid_m.group(1)) != rid_target:
                continue
            for row in re.split(r"\]\s*,\s*\[", m.group(1)):
                parts = [p.strip().strip("[]'\"").replace("\\'", "'")
                         for p in row.split(",")]
                if len(parts) >= 6:
                    name, _party = parse_label(re.sub(r"\s+", " ", parts[0]).strip())
                    breaks[name] = parts[2:5]
            break
        out.append((office, district, candidates, breaks, title))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_path")
    ap.add_argument("output_path")
    ap.add_argument("--county", default="Lehigh")
    ap.add_argument("--county-output", default=None,
                    help="optional path: also write aggregated county-level "
                         "rows with the official ED/mail/provisional "
                         "breakdowns")
    args = ap.parse_args()

    rows, soup, html = parse_html(args.input_path, args.county)
    cols = ["county", "precinct", "office", "district", "party", "candidate",
            "votes", "election_day", "mail", "provisional"]
    with open(args.output_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} precinct rows to {args.output_path}")

    if args.county_output:
        # aggregate the precinct rows
        agg = {}
        order = []
        for r in rows:
            key = (r["office"], r["district"], r["party"], r["candidate"])
            if key in agg:
                agg[key]["votes"] = str(int(agg[key]["votes"]) + int(r["votes"]))
            else:
                agg[key] = {"county": args.county, "office": r["office"],
                            "district": r["district"], "party": r["party"],
                            "candidate": r["candidate"], "votes": r["votes"],
                            "election_day": "0", "mail": "0",
                            "provisional": "0"}
                order.append(key)
        # fill in the official countywide breakdowns per race
        for office, district, candidates, breaks, title in county_race_data(soup, html):
            for name, party, _votes in candidates:
                b = breaks.get(name)
                if b is None:
                    continue
                key = (office, district, party, name)
                if key in agg:
                    agg[key]["election_day"], agg[key]["mail"], \
                        agg[key]["provisional"] = b
        with open(args.county_output, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["county", "office", "district",
                                              "party", "candidate", "votes",
                                              "election_day", "mail",
                                              "provisional"])
            w.writeheader()
            for key in order:
                w.writerow(agg[key])
        print(f"wrote {len(order)} county rows to {args.county_output}")


if __name__ == "__main__":
    main()