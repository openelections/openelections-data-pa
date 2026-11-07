#!/usr/bin/env python3
"""Parse the Delaware County 2024 Primary "Official Detailed Results" report.

Unlike the county's 2022-primary source (Clarity cumulative HTML,
countywide-only), the 2024-primary source IS precinct-level: an ES&S-style
fixed-width text report ("Detailed Results") that carries, for the whole
county first (Registered Voters / Ballots Cast / Voter Turnout per precinct)
and then per contest (DEM section first, REP section second for the
party-primary contests):

    <contest title, right-aligned, "( n)" vote-for count>
    <candidate names line, "(nn)" ballot-number prefixes, columns aligned>
    Precinct   Registered Voters   Election Day Votes   Mail Voting
               Provisional Voting   Total   ... (repeats per candidate)
               ... Total  <- grand-total column at end
    <one data row per precinct: name [RV] ED Mail Prov Total per candidate,
     then the contest grand total>
    Totals:  <same column structure, countywide/district-wide sums>

Long precinct names overflow their 30-character field, so the first number
glues onto the name ("UPPER CHICHESTER TWP WD 01 PRECINCT 01841").  That is
resolved against the countywide section (Ballots Cast / Voter Turnout pins
the Registered Voters split) and every later section is then resolved by
prefix-matching the known names (the leftover digits must equal the
precinct's known RV).

Output (10-column precinct file, matches source-name spellings):

    county,precinct,office,district,party,candidate,votes,election_day,
    mail,provisional

  * "Registered Voters" / "Ballots Cast" rows per precinct from the county
    section (no breakdown, party empty).
  * Every contest section, in source order, including the county's
    "Committeeman, ..." / "Committeewoman, ..." precinct-party committee
    contests (one data row each; office carries the contest's precinct name
    exactly as the source prints it).
  * Write-ins: the source prints a single "Unassigned write-ins" candidate
    column per contest -> candidate name "Write-In Totals" (repo convention),
    carrying the contest party.
  * Delegates: "Delegate to the {Democratic,Republican} National Convention"
    / "Alternate Delegate to the Republican National Convention", with the
    congressional district in `district`.

Verification (printed by --summary): per section, the printed "Totals:" row
must equal the sums of the parsed precinct rows (per candidate and grand
total); every parsed row must satisfy ED+Mail+Prov == Total and candidate
totals == the row's contest grand total; and per
(contest, party, candidate) sums are compared against the paired
"Official Summary Results" CSV, which is the countywide anchor.

Usage:
    python pa_delaware_primary_2024_results_parser.py detail.txt output.csv \
        [--summary summary.csv]
"""

import argparse
import csv
import re
import sys

COUNTY = "Delaware"
COLS = ["county", "precinct", "office", "district", "party", "candidate",
        "votes", "election_day", "mail", "provisional"]

# (all-caps source title fragment, office, district-from-title?)
OFFICE_MAP = [
    (r"^\d*PRESIDENT OF THE UNITED STATES", "President", False),
    (r"^\d*UNITED STATES SENATOR", "U.S. Senate", False),
    (r"^\d*ATTORNEY GENERAL", "Attorney General", False),
    (r"^\d*AUDITOR GENERAL", "Auditor General", False),
    (r"^\d*STATE TREASURER", "State Treasurer", False),
    (r"^\d*REPRESENTATIVE IN CONGRESS (\d+)\w* DISTRICT", "U.S. House", True),
    (r"^\d*SENATOR IN THE GENERAL ASSEMBLY (\d+)\w* DISTRICT", "State Senate",
     True),
    (r"^\d*REPRESENTATIVE IN THE GENERAL ASSEMBLY (\d+)\w* DISTRICT",
     "State House", True),
    (r"^\d*DELEGATE TO DEMOCRATIC NATIONAL CONVENTION (\d+)\w* DISTRICT",
     "Delegate to the Democratic National Convention", True),
    (r"^\d*DELEGATE TO REPUBLICAN NATIONAL CONVENTION (\d+)\w* DISTRICT",
     "Delegate to the Republican National Convention", True),
    (r"^\d*ALTERNATE DELEGATE TO REPUBLICAN NATIONAL CONVENTION "
     r"(\d+)\w* DISTRICT",
     "Alternate Delegate to the Republican National Convention", True),
]

WRITE_IN_NAME = "Write-In Totals"

# candidate-group label sequence inside the column-header line
GROUP_LABELS = ("Election Day Votes", "Mail Voting", "Provisional Voting",
                "Total")
LABEL_RE = re.compile(
    r"(Registered Voters|Election Day Votes|Mail Voting|Provisional Voting"
    r"|Total)")

WORD_RE = re.compile(r"(\d)([A-Za-z])")     # "159TH" -> "159 TH"
_NUM_RE = re.compile(r"^-?\d+$")
_NUM2_RE = re.compile(r"^-?\d+")


def _digits_before_alpha(word):
    """'159TH DISTRICT' -> 159 (ordinal suffix tolerant)."""
    m = re.match(r"^(\d+)", word)
    return int(m.group(1)) if m else None


def norm_contest_title(title):
    """Source title -> comparison key (no vote-for, single spaces, upper)."""
    t = title.strip()
    # trailing vote-for marker: "( 1)" (detail) or "(Vote For 1)" (summary)
    t = re.sub(r"\(\s*Vote\s*For\s*\d+\s*\)\s*$", "", t, flags=re.I)
    t = re.sub(r"\(\s*\d+\s*\)\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip().upper()
    t = re.sub(r"(\d)\s+([A-Z]{2,})", r"\1 \2", t)   # "159 TH" -> "159TH"
    return t


def map_office(title):
    """Detail title text -> (office, district) or (None, None)."""
    t = re.sub(r"\s+", " ", title.strip().upper())
    t = re.sub(r"\((\s*\d+\s*)\)\s*$", "", t).strip()
    for rx, office, has_dist in OFFICE_MAP:
        m = re.search(rx, t)
        if m:
            return (office, str(_digits_before_alpha(m.group(1)))
                    if has_dist else "")
    return (None, None)


def parse_county_section(lines, start, errors):
    """First block: per-precinct Registered Voters / Ballots Cast / Turnout.

    Returns (ordered list of (name, rv, bc), end-line-index).
    """
    precincts = []
    i = start
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            break
        if re.match(r"^\s*Total", line.strip()):
            break
        parts = [p for p in re.split(r"\s{2,}", line.rstrip()) if p != ""]
        turnout = None
        if len(parts) == 4:
            name, rv_s, bc_s, pct_s = parts
        elif len(parts) == 3:
            # long name glued to the first (Registered Voters) number
            glue = parts[0]
            m = re.search(r"(\d+)\s*$", glue)
            if not m:
                errors.append("county row %d: cannot split %r" % (i + 1, glue))
                i += 1
                continue
            run = m.group(1)
            base = glue[:len(glue) - len(run)]
            bc_s, pct_s = parts[1], parts[2]
            # turnout% pins where the name ends: pct == bc / rv * 100
            bc = int(bc_s)
            want = float(pct_s.rstrip("%"))
            matches = []
            for r in range(1, min(len(run), 5) + 1):
                rv_s = run[len(run) - r:]
                if len(rv_s) > 1 and rv_s.startswith("0"):
                    continue        # RVs print without leading zeros
                nm = (base + run[:len(run) - r]).rstrip()
                if not nm or nm[-1] in (" ", ","):
                    continue
                rv = int(rv_s)
                if rv <= 0:
                    continue
                got = bc * 100.0 / rv
                if abs(got - want) <= 0.0055:
                    matches.append((nm, rv))
            best = None
            for nm, rv in matches:
                if best is not None:
                    errors.append(
                        "county row %d: ambiguous split %r (%r=%d / %r=%d)"
                        % (i + 1, glue, nm, rv, best[0], best[1]))
                best = (nm, rv)
            if best is None:
                errors.append(
                    "county row %d: no turnout-consistent split for %r "
                    "(bc=%s pct=%s)" % (i + 1, glue, bc, want))
                i += 1
                continue
            name, rv_s = best
        else:
            errors.append("county row %d: %d field(s): %r" % (i + 1, len(parts),
                                                              line[:120]))
            i += 1
            continue
        precincts.append((name, int(rv_s), int(bc_s)))
        i += 1
    return precincts, i


def parse_sections(lines, first_end, errors):
    """Yield one dict per contest section between first_end and EOF."""
    i = first_end
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or not re.search(r"\(\s*\d+\s*\)\s*$", stripped):
            i += 1
            continue
        if not line.startswith((" ", "\t")):
            errors.append("line %d: title not indented: %r" % (i + 1, line[:80]))
        title = stripped
        i += 1
        # candidate-name line (skip blanks defensively)
        while i < len(lines) and not lines[i].strip():
            i += 1
        cand_line = lines[i] if i < len(lines) else ""
        i += 1
        # column-header line starts with "Precinct"
        while i < len(lines) and not lines[i].startswith("Precinct"):
            i += 1
        if i >= len(lines):
            errors.append("section %r: no column header" % title)
            break
        header = lines[i]
        i += 1
        labels = [(m.group(1), m.start()) for m in LABEL_RE.finditer(header)]
        has_rv = labels and labels[0][0] == "Registered Voters"
        # candidate groups: each "Election Day Votes" starts one group
        groups = [(ev_off, off) for lab, off in labels
                  if lab == "Election Day Votes"
                  for ev_off in [off]]
        # grand-total column: last "Total" not part of a group of 4
        n_group_total = len(groups) * 4 + (1 if has_rv else 0)
        grand_off = labels[n_group_total][1] if len(labels) > n_group_total \
            else None
        if len(labels) not in (n_group_total + 1, n_group_total):
            errors.append("section %r: unexpected header labels %r"
                          % (title, labels))
        # candidates in order, left-aligned above their candidate-group
        # columns: pair each group's "Election Day Votes" offset with the
        # candidate token whose position is the largest <= that offset
        tok_spans = [(m.start(), m.group(0)) for m in
                     re.finditer(r"\S+(?:\s\S+)*", cand_line)
                     if any(seg for seg in re.split(r"\s{2,}", m.group(0))
                            if seg.strip())]
        toks = []
        for m in re.finditer(r"[^\n]+", cand_line):
            pos = m.start()
            for chunk in re.split(r"\s{2,}", m.group(0)):
                idx = cand_line.find(chunk, pos)
                if chunk.strip():
                    toks.append((idx, chunk.strip()))
                pos = idx + len(chunk)
        cands = []
        for g_off, _ in groups:
            best = None
            for off, tok in toks:
                if off <= g_off and (best is None or off > best[0]):
                    best = (off, tok)
            cands.append(best[1] if best else "")
        cand_tokens = [t for _, t in toks]
        if len(cand_tokens) != len(groups):
            errors.append("section %r: candidate tokens %r != groups (%d)"
                          % (title, cand_tokens, len(groups)))

        # data rows
        n_nums = (1 if has_rv else 0) + 4 * len(groups) + (1 if grand_off
                                                           is not None else 0)
        data = []
        while i < len(lines):
            line = lines[i].rstrip()
            s = line.strip()
            if not s:
                break
            if s.startswith("Total"):
                data.append(("__totals__", s))
                i += 1
                break
            data.append(("__row__", line))
            i += 1
            if len(data) > 500:
                errors.append("section %r: >500 data rows" % title)
                break

        yield {
            "title": title, "groups": groups, "grand_off": grand_off,
            "candidates": cands, "n_nums": n_nums, "data": data,
            "line": i,
        }


def parse_rows(section, precinct_map, errors):
    """Parse the data rows of one section into (precinct, values) tuples.

    values = [rv, (ed, mail, prov, total) per candidate, grand].
    """
    out = []
    titles = [n for n, _ in section["data"]]
    # totals row first pass to learn expected numbers
    for kind, payload in section["data"]:
        if kind == "__totals__":
            continue
        line = payload
        parts = [p for p in re.split(r"\s{2,}", line) if p != ""]
        n_nums = section["n_nums"]
        vals = None
        name = None
        if len(parts) == n_nums + 1 and all(_NUM_RE.match(p) for p in parts[1:]):
            name = parts[0]
            vals = [int(p) for p in parts[1:]]
        else:
            # glued case: name + trailing digits for the first value
            glue = parts[0] if parts else ""
            matches = []
            for pname, rev in precinct_map.items():
                if glue.startswith(pname):
                    rest = glue[len(pname):]
                    # 29-char names pad to column 30 with one space, so the
                    # glue can carry a single leading space before the RV
                    rest = rest.lstrip()
                    if rest and _NUM_RE.match(rest):
                        matches.append((len(pname), pname, rev, rest))
            matches.sort(reverse=True)
            ok = None
            for _, pname, rev, rest in matches:
                if int(rest) == rev:
                    ok = (pname, rev)
                    break
            if ok is None:
                errors.append("section %r: unresolved row %r"
                              % (section["title"], glue[:60]))
                continue
            name, rv = ok
            rest_vals = [p for p in parts[1:] if p.strip() != ""]
            if len(rest_vals) != n_nums - 1 or \
                    not all(_NUM_RE.match(p) for p in rest_vals):
                errors.append("section %r: bound numbers wrong for %r: %r"
                              % (section["title"], name, rest_vals))
                continue
            vals = [rv] + [int(p) for p in rest_vals]
        if name not in precinct_map:
            errors.append("section %r: unknown precinct %r"
                          % (section["title"], name))
            continue
        out.append((name, vals))

    # totals row
    totals_vals = None
    for kind, payload in section["data"]:
        if kind == "__totals__":
            nums = [p for p in re.split(r"\s{2,}", payload) if p.strip()]
            # "Totals:" first token
            if nums[0].lower().startswith("total"):
                nums = nums[1:]
            if not all(_NUM_RE.match(p) for p in nums):
                errors.append("section %r: bad totals row %r"
                              % (section["title"], payload[:100]))
            else:
                totals_vals = [int(p) for p in nums]
    # cross-checks
    for name, vals in out:
        if len(vals) != section["n_nums"]:
            errors.append("section %r: %s: %d numbers vs expected %d"
                          % (section["title"], name, len(vals),
                             section["n_nums"]))
            continue
        base = 1
        k = len(section["groups"])
        groups_total = 0
        for g in range(k):
            ed, mail, prov, tot = vals[base:base + 4]
            if ed + mail + prov != tot:
                errors.append("section %r: %s: ED+Mail+Prov=%d != Total=%d"
                              % (section["title"], name, ed + mail + prov,
                                 tot))
            groups_total += tot
            base += 4
        if base < len(vals) and vals[base] != groups_total:
            errors.append("section %r: %s: grand %d != candidate sums %d"
                          % (section["title"], name, vals[base],
                             groups_total))
    return out, totals_vals


def parse(input_path, summary_path=None, county=COUNTY):
    with open(input_path, newline="", encoding="latin-1") as fh:
        text = fh.read().replace("\r\n", "\n")
    lines = text.split("\n")
    errors = []

    # ---- countywide RV/BC section starts at the "Precinct  Registered
    # Voters  Ballots Cast  Voter Turnout" header (line 6)
    start = 0
    for i, l in enumerate(lines):
        if l.startswith("Precinct") and "Ballots Cast" in l:
            start = i + 1
            break
    precinct_list, first_end = parse_county_section(lines, start, errors)
    precinct_list = [p for p in precinct_list if p[0]]
    precinct_map = {name: rv for name, rv, _bc in precinct_list}
    if len(precinct_map) != len(precinct_list):
        errors.append("duplicate precinct names: %d names, %d unique"
                      % (len(precinct_list), len(precinct_map)))

    rows = []
    for name, rv, bc in precinct_list:
        rows.append({
            "county": county, "precinct": name, "office": "Registered Voters",
            "district": "", "party": "", "candidate": "", "votes": rv,
            "election_day": "", "mail": "", "provisional": "",
        })
        rows.append({
            "county": county, "precinct": name, "office": "Ballots Cast",
            "district": "", "party": "", "candidate": "", "votes": bc,
            "election_day": "", "mail": "", "provisional": "",
        })

    # ---- contest sections
    sections = list(parse_sections(lines, first_end, errors))

    # party attribution from the paired summary file
    sum_parties = {}       # normalized title -> [party, ...] in file order
    sum_candidates = {}    # normalized title -> {party: {candidate: votes}}
    sum_rows = {}          # normalized title -> {party: [(candidate, votes)]}
    if summary_path:
        with open(summary_path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                key = norm_contest_title(r["contest name"])
                p = r["party name"].upper()
                cand = re.sub(r"^\(\s*\d+\s*\)\s*", "", r["choice name"]
                              ).strip()
                if cand.lower().startswith("unassigned write-in"):
                    cand = WRITE_IN_NAME
                sum_parties.setdefault(key, [])
                if p not in sum_parties[key]:
                    sum_parties[key].append(p)
                byparty = sum_candidates.setdefault(key, {}).setdefault(p, {})
                # the county reuses one contest title for more than one
                # precinct (e.g. "Committeewoman, Chadds Ford Township");
                # those rows sum to our precinct-row sums
                byparty[cand] = byparty.get(cand, 0) + int(r["total votes"])
                sum_rows.setdefault(key, {}).setdefault(p, []).append(
                    (cand, int(r["total votes"])))

    section_reports = []
    key_office = {}        # normalized summary title -> (office, district)
    for sec_idx, sec in enumerate(sections):
        title = sec["title"]
        office, district = map_office(title)
        if office is None:
            # precinct party-committee contests ("Committeeman, Aldan
            # Borough Precinct Eastern"): office = contest title as printed
            if re.match(r"^Committe(e[ew]?man|ewoman)", title, re.I) \
                    or title.lower().startswith("committeeman") \
                    or title.lower().startswith("committeewoman"):
                office = re.sub(r"\(\s*\d+\s*\)\s*$", "", title).strip()
                office = re.sub(r"\s+", " ", office)
                district = ""
            else:
                errors.append("unmapped contest title: %r" % title)
                continue
        key = norm_contest_title(title)
        key_office.setdefault(key, (office, district))
        parties = sum_parties.get(key, [])
        parsed, totals_vals = parse_rows(sec, precinct_map, errors)
        # assign party: sections of one title pair with the summary's party
        # blocks in file order (party primaries print the DEM block first);
        # when counts differ (repeated per-precinct committee titles), each
        # section is matched to the party whose summary candidate vote values
        # equal the section's per-candidate sums
        party = ""
        if parties:
            same_title = [j for j, s in enumerate(sections)
                          if norm_contest_title(s["title"]) == key]
            cand_sums = {}
            for _name, vals in parsed:
                base_v = 1
                for c in sec["candidates"]:
                    tot = vals[base_v + 3]
                    cn = c if not c.lower().startswith("unassigned write-in") \
                        else WRITE_IN_NAME
                    cn = re.sub(r"^\(\s*\d+\s*\)\s*", "", cn).strip()
                    cand_sums[cn] = cand_sums.get(cn, 0) + tot
                    base_v += 4
                break
            if len(same_title) == len(parties):
                party = parties[same_title.index(sec_idx)]
            else:
                matches_p = []
                for p in parties:
                    # individual summary rows, matched greedily (the county
                    # reuses per-precinct committee titles, so each section
                    # maps onto one row per candidate)
                    rows_p = list(sum_rows.get(key, {}).get(p, []))
                    used = set()
                    ok = True
                    for cn, v in cand_sums.items():
                        hit = next((j for j, (rc, rv_v) in enumerate(rows_p)
                                    if rc == cn and rv_v == v
                                    and j not in used), None)
                        if hit is None:
                            ok = False
                            break
                        used.add(hit)
                    if ok:
                        matches_p.append(p)
                if len(matches_p) == 1:
                    party = matches_p[0]
                else:
                    errors.append("section %r: cannot assign party "
                                  "(parties=%r, sums=%r, matches=%r)"
                                  % (title, parties, cand_sums, matches_p))
        else:
            errors.append("section %r: no summary party info" % title)

        k = len(sec["groups"])
        # per-candidate sums vs printed totals row
        sums = [0] * sec["n_nums"]
        for _name, vals in parsed:
            for j, v in enumerate(vals):
                sums[j] += v
        if totals_vals is not None:
            if len(totals_vals) == len(sums) and totals_vals != sums:
                diff = [j for j in range(len(sums))
                        if sums[j] != totals_vals[j]]
                if diff:
                    j = diff[0]
                    errors.append(
                        "section %r: parsed sums != printed Totals "
                        "(col %d: ours %d vs printed %d; %d other col(s))"
                        % (title, j, sums[j], totals_vals[j],
                           len(diff) - 1))
        else:
            errors.append("section %r: no totals row parsed" % title)

        base = 1
        for gi, cand in enumerate(sec["candidates"]):
            cand_name = cand.strip()
            if cand_name.lower().startswith("unassigned write-in"):
                cand_name = WRITE_IN_NAME
            else:
                cand_name = re.sub(r"^\(\s*\d+\s*\)\s*", "", cand_name).strip()
            for _name, vals in parsed:
                ed, mail, prov, tot = vals[base:base + 4]
                rows.append({
                    "county": county, "precinct": _name, "office": office,
                    "district": district, "party": party,
                    "candidate": cand_name, "votes": tot,
                    "election_day": ed, "mail": mail, "provisional": prov,
                })
            base += 4
        section_reports.append((title, party, office, district,
                                len(parsed), k, len(sec["candidates"])))

    # ---- cross-check vs summary (per contest / party / candidate)
    xcheck = None
    if summary_path:
        mine = {}
        for r in rows:
            if r["office"] in ("Registered Voters", "Ballots Cast"):
                continue
            k = (r["office"], r["district"], r["party"], r["candidate"])
            mine[k] = mine.get(k, 0) + r["votes"]
        exact = missing = mismatch = 0
        deltas = []
        seen = set()
        for key, byparty in sum_candidates.items():
            for party, cands in byparty.items():
                office, district = key_office.get(key, (None, None))
                if office is None:
                    missing += len(cands)
                    deltas.append(("UNMAPPED", key, party))
                    continue
                for cand, want in cands.items():
                    seen.add((office, district, party, cand))
                    got = mine.get((office, district, party, cand), 0)
                    if got == want:
                        exact += 1
                    else:
                        mismatch += 1
                        deltas.append(("MISMATCH", office, district, party,
                                       cand, got, want))
        extra_out = [k for k in mine if k not in seen]
        xcheck = (exact, missing, mismatch, deltas, extra_out)

    report = {
        "precincts": len(precinct_map),
        "sections": len(section_reports),
        "errors": errors,
    }
    return rows, report, xcheck


# ------------------------------------------------------------- summary keys


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--summary", help="paired Official Summary Results CSV")
    ap.add_argument("--county", default=COUNTY)
    args = ap.parse_args(argv)

    rows, report, xcheck = parse(args.input, args.summary, args.county)
    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)

    print("wrote %d rows (%d precincts, %d contest sections) -> %s"
          % (len(rows), report["precincts"], report["sections"], args.output))
    if xcheck:
        exact, missing, mismatch, deltas, extra_out = xcheck
        print("summary cross-check: %d candidate keys exact, %d mismatched, "
              "%d contests unmapped, %d ours-only key(s)"
              % (exact, mismatch, missing, len(extra_out)))
        for d in deltas[:20]:
            print("  " + repr(d))
        for d in extra_out[:10]:
            print("  ours-only: " + repr(d))
    errs = report["errors"]
    if errs:
        print("VALIDATION PROBLEMS (%d):" % len(errs), file=sys.stderr)
        for e in errs[:40]:
            print("  " + e, file=sys.stderr)
        return 1
    print("all internal checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
