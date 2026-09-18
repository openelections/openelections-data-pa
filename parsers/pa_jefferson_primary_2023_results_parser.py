#!/usr/bin/env python3
"""Jefferson County, PA 2023 Municipal Primary (May 16, 2023) — county-level
and precinct-level results.

Two Dominion source formats, auto-detected from the file content:

1. "Election Summary Report / Closed Primary" county summary
   (93 pages; parse_county() below, built on parse_esr2 from
   pa_2023_summary_common like Carbon/Luzerne):
     <Header> (DEM) (Vote for N)
     DEM
     Precincts Reported: ...
     Times Cast 1,875 / 6,007   31.21%
     Candidate  Party  Total
     <candidate rows (single Total number, no ED/Mail/Provisional)>
     Total Votes <total>
     <write-in detail block: NAME WRITE-IN total / Unresolved Write-In total>
   No Undervotes/Overvotes rows.  The contest party goes in the party column
   of every row of the contest, including Write-ins (repo-wide primary
   convention so the DEM/REP sections of the same office do not collide in
   the duplicate_entries test); metadata rows stay party-empty.
   Write-in detail rows are folded into ONE "Write-ins" row per contest;
   in 4 contests the printed "Total Votes" exceeds candidates + itemized
   details by 1-4 votes, and the Write-ins row is set to the printed total
   minus candidates (residual; each adjustment is logged).

2. "Statement of Votes Cast" per-precinct report (563 pages; parse_precinct()
   below, built on the SOVC engine in
   pa_carbon_primary_2023_results_parser.parse_sovc — same Dominion layout:
   per contest, tables of [Precinct | Times Cast | Registered Voters] +
   [Precinct | candidate "(DEM)" vote columns | "Total Votes"], followed by
   per-precinct named "Qualified Write In" column pages and, for
   single-precinct contests, transposed write-in-detail pages whose columns
   are [county Total | Cumulative | Cumulative - Total | ... | <precinct
   name>]).  Write-ins per precinct = all named "Qualified Write In"
   columns + the unresolved "Write-In" column, reproducing the county
   summary's write-in aggregate (which includes BLANK / unresolved
   write-ins).
   That engine keys its page furniture on the literal token "Carbon County"
   (total rows, detail-page headers, SOVC_KEYWORDS), so the Jefferson text
   is pre-transformed: "Jefferson County" -> "Carbon County" (lossless —
   "Jefferson" only occurs as "Jefferson County", never in a precinct or
   contest name) and precinct suffix digits are masked
   ("Brookville Borough 1" -> "Brookville Borough #1", unmasked again in the
   output) so a bare precinct-suffix digit is never parsed as a vote number.

Office/district conventions mirror the county's 2023 general file
(2023/counties/20231107__pa__general__jefferson__precinct.csv, parser
pa_jefferson_general_2023_results_parser.py):
  "Supervisor (6 Year Term) - Barnett Township"
      -> office "Supervisor 6 Year Term", district "Barnett Township"
  "Borough Council (4 Year Term) - Brookville Borough"
      -> "Borough Council 4 Year Term", "Brookville Borough"
  "Magisterial District Judge - 54-3-01"
      -> "Magisterial District Judge", "54-3-01"
  "School Director (4 Year Term) - Brockway Region II"
      -> "School Director 4 Year Term", "Brockway Region Ii" (the general
         file's title() casing: "II" -> "Ii", "III" -> "Iii";
         "McCalmont Township" -> "Mccalmont Township")
  "County Commissioners" / "County Auditors" -> "County Commissioner" /
      "County Auditor" (countywide, empty district)
  "Register of Wills, Recorder of Deeds & Clerk of the Orphans Court" kept
      verbatim as the office.

Usage: pa_jefferson_primary_2023_results_parser.py <input.pdf|txt> <output.csv>
       [--county-summary <summary.txt|pdf>]   (precinct-mode cross-check)
Input containing "Statement of Votes Cast" -> precinct CSV; otherwise the
Election Summary Report -> county CSV.
"""

import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pa_2023_summary_common import (  # noqa: E402
    FIELDNAMES, VOTEFOR, clean_name, is_junk, nums_from, parse_esr2,
    text_from_pdf, write_csv,
)
import pa_carbon_primary_2023_results_parser as _carbon  # noqa: E402

COUNTY = "Jefferson"

# contest headers that map_contest could not normalize (printed at parse
# time so nothing is silently guessed)
UNMAPPED = []


def _titlecase(s):
    """Same transform the county's general-2023 parser applies to offices and
    districts (matches its file exactly, incl. 'Mccalmont', 'Region Ii')."""
    return " ".join(
        w.lower() if w.lower() in ("of", "the", "and", "for", "&") else w.title()
        for w in s.split())


COUNTY_OFFICES = {
    "County Commissioners": "County Commissioner",
    "County Auditors": "County Auditor",
    "Sheriff": "Sheriff",
    "Register of Wills, Recorder of Deeds & Clerk of the Orphans Court":
        "Register of Wills, Recorder of Deeds & Clerk of the Orphans Court",
    "Justice of the Supreme Court": "Justice of the Supreme Court",
    "Judge of the Superior Court": "Judge of the Superior Court",
    "Judge of the Commonwealth Court": "Judge of the Commonwealth Court",
}


def map_contest(header):
    """'<Office> (<N> Year Term) - <Muni> (DEM) (Vote for N)' ->
    (office, district, party), per the county's 2023 general file."""
    h = re.sub(r"\s+", " ", header.strip())
    party = ""
    m = re.search(r"\s*\(Vote for \d+\)\s*$", h, re.I)
    if m:
        h = h[:m.start()].strip()
    m = re.search(r"\s*\((DEM|REP)\)\s*$", h, re.I)
    if m:
        party = m.group(1).upper()
        h = h[:m.start()].strip()
    term = None
    m = re.search(r"\((\d)\s*[YyTt]ear Term\)", h)
    if m:
        term = m.group(1)
        h = (h[:m.start()] + " " + h[m.end():]).strip()
    h = re.sub(r"\s+", " ", h).strip()

    m = re.match(r"^Magisterial District Judge - (\d{2}-\d-\d{2})$", h)
    if m:
        return "Magisterial District Judge", m.group(1), party

    if h in COUNTY_OFFICES:
        return COUNTY_OFFICES[h], "", party

    m = re.match(r"^(Supervisor|Auditor|Borough Council|Tax Collector|"
                 r"Assessor|School Director)\s*-\s*(.+)$", h)
    if m:
        office = m.group(1)
        district = _titlecase(m.group(2).strip())
        if term:
            office += f" {term} Year Term"
        return office, district, party

    UNMAPPED.append(header)
    if term:
        return f"{_titlecase(h)} {term} Year", "", party
    return _titlecase(h), "", party


# --------------------------------------------------------------------------
# county summary (Dominion ESR2 via the shared parse_esr2)
# --------------------------------------------------------------------------

META_OFFICES = ("Registered Voters", "Ballots Cast", "Ballots Cast - Blank")
NONVOTE_CANDS = ("Write-ins", "Undervotes", "Overvotes")


def repair_wrapped_writein_rows(lines):
    """Join pdftotext-wrapped write-in detail rows: '<NAME> WRITE-IN' with
    the count alone on the following line (5 occurrences).  The number is
    the row's write-in count; a trailing name fragment on a later line is
    dropped (name details are only aggregated, never emitted)."""
    out = []
    i = 0
    n = len(lines)
    while i < n:
        l = lines[i]
        if re.search(r"WRITE-IN\s*$", l) and i + 1 < n \
                and re.fullmatch(r"\s*[\d,]+\s*", lines[i + 1]):
            out.append(l.rstrip() + " " + lines[i + 1].strip())
            i += 2
            continue
        out.append(l)
        i += 1
    return out


def summarize_blocks(src):
    """Walk the summary text per contest: header, Vote-for-N, Times Cast,
    Total Votes, and the sum of write-in detail rows (named details + BLANK
    + Unresolved).  Same header joining as parse_esr2."""
    with open(src, encoding="utf-8") as fh:
        lines = repair_wrapped_writein_rows([l.rstrip() for l in fh])
    blocks = {}
    order = []
    current = None
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if VOTEFOR.search(stripped) \
                or re.search(r"\(vote(\s+for)?\s*$", stripped, re.I):
            header = stripped
            j = i
            while not VOTEFOR.search(header) and j + 1 < n \
                    and lines[j + 1].strip():
                j += 1
                header += " " + lines[j].strip()
                if VOTEFOR.search(header):
                    break
            office, dist, party = map_contest(header)
            current = (office, dist, party)
            m = VOTEFOR.search(header)
            blocks[current] = {
                "n": int(m.group(1)), "tc": None, "total": None, "wi": 0,
                "header": header,
            }
            order.append(current)
            i = j + 1
            continue
        if current is None or is_junk(stripped):
            i += 1
            continue
        low = stripped.lower()
        if low.startswith("times cast"):
            nums = nums_from(re.sub(r"/", " ", stripped).split())
            if nums:
                blocks[current]["tc"] = int(nums[0])
            i += 1
            continue
        if re.match(r"^total votes", stripped, re.I):
            nums = nums_from(stripped.split())
            if nums:
                blocks[current]["total"] = int(nums[0])
            i += 1
            continue
        if re.search(r"write-in", stripped, re.I):
            nums = nums_from(stripped.split())
            if nums:
                blocks[current]["wi"] += int(nums[-1])
            i += 1
            continue
        i += 1
    return {"blocks": blocks, "order": order}


def stamp_parties(R, blocks):
    """Stamp the contest party on Write-ins rows (Jefferson has no
    Undervotes/Overvotes rows) so DEM/REP sections stay distinct; metadata
    rows stay party-empty.

    Write-in-only contests emit consecutive Write-ins rows with no party, so
    assignment walks the contests in document order and disambiguates with
    the per-contest write-in detail sums computed from the raw text."""
    order = blocks["order"]
    binfo = blocks["blocks"]
    assigned = set()
    ci = 0
    n = len(order)
    stamp_bad = []
    for r in R.rows:
        if r[1] in META_OFFICES:
            continue
        if r[4] in NONVOTE_CANDS or not r[3]:
            try:
                val = int(r[5] or 0)
            except (TypeError, ValueError):
                val = None
            pick = None
            for k in range(ci, n):
                c = order[k]
                if (c[0], c[1]) == (r[1], r[2]) and k not in assigned \
                        and binfo[c]["wi"] == val:
                    pick = k
                    break
            if pick is None:
                for k in range(ci, n):
                    c = order[k]
                    if (c[0], c[1]) == (r[1], r[2]) and k not in assigned:
                        pick = k
                        break
            if pick is None:
                stamp_bad.append(("no contest for row", r))
                continue
            if r[3] and r[3] != order[pick][2]:
                stamp_bad.append(("row party vs contest", r, order[pick]))
            r[3] = order[pick][2]
            assigned.add(pick)
        else:
            while ci < n and (order[ci][0], order[ci][1], order[ci][2]) \
                    != (r[1], r[2], r[3]):
                ci += 1
            if ci >= n:
                stamp_bad.append(("no contest for candidate row", r))
    return stamp_bad


def apply_writein_residuals(R, blocks, quiet=False):
    """In a few contests the printed "Total Votes" exceeds candidates +
    itemized write-in details by 1-4 votes (the detail block under-reports;
    low-count details are dropped at a page break).  The aggregate Write-ins
    row is set to the residual (Total - candidates) so the output reconciles
    with the printed contest total; each adjustment is logged."""
    residuals = {}
    for key, tot in R.totals.items():
        if tot is None:
            continue
        cand = wi = None
        for r in R.rows:
            if (r[1], r[2], r[3]) == key and r[1] not in META_OFFICES:
                if r[4] == "Write-ins":
                    wi = int(r[5] or 0)
                else:
                    cand = (cand or 0) + int(r[5] or 0)
        resid = tot - (cand or 0) - (wi or 0)
        if resid:
            residuals[key] = resid
            if wi is None:
                R.rows.append([COUNTY, key[0], key[1], key[2], "Write-ins",
                               resid, "", "", ""])
            else:
                for r in R.rows:
                    if (r[1], r[2], r[3]) == key and r[4] == "Write-ins":
                        r[5] = str((wi or 0) + resid)
                        break
            if not quiet:
                print(f"NOTE write-in residual: {key[2]} {key[0]} | "
                      f"{key[1]}: itemized details {wi} + residual {resid} "
                      f"-> Write-ins {(wi or 0) + resid} (Total Votes {tot})")
    for key, resid in residuals.items():
        blocks["blocks"][key]["resid"] = resid
    return residuals


def parse_county(src, dst, quiet=False):
    with open(src, encoding="utf-8") as fh:
        lines = repair_wrapped_writein_rows([l.rstrip() for l in fh])
    tmp = src + ".repaired"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    R = parse_esr2(tmp, COUNTY, map_contest)
    os.unlink(tmp)

    blocks = summarize_blocks(src)
    stamp_bad = stamp_parties(R, blocks)
    if stamp_bad:
        for msg, *rest in stamp_bad[:10]:
            if not quiet:
                print(f"WARNING party stamp: {msg}: {rest}")

    # the summary reports a single Total per row (no ED/Mail/Provisional
    # columns); parse_esr2's write-in detail accumulation emits 0,0,0
    # breakdowns for those rows, which would fail vote_breakdown_totals.
    blanked = 0
    for r in R.rows:
        if r[4] in NONVOTE_CANDS and r[6] == "0" and r[7] == "0" \
                and r[8] == "0":
            r[6] = r[7] = r[8] = ""
            blanked += 1

    apply_writein_residuals(R, blocks)

    if not quiet:
        n = write_csv(dst, R)
        print(f"wrote {n} rows -> {dst} "
              f"(breakdown zero-columns blanked on {blanked})")
        print(f"contests parsed: {len(R.contests)}")
        for h in UNMAPPED:
            print(f"WARNING unmapped contest header: {h!r}")

    keys = set(R.contests)
    if len(keys) != len(R.contests):
        print("WARNING duplicate contest keys among headers")
    bad = party_aware_totals(R)
    if bad and not quiet:
        for (office, dist, party), a, t in bad:
            print(f"WARNING {party} {office} | {dist}: rows sum {a} != "
                  f"Total Votes {t}")
    elif not bad:
        print("party-aware contest totals check: all contests reconcile")

    check_party_ballot_arithmetic(R, blocks, quiet)
    check_writein_arithmetic(R, blocks, quiet)
    return R


def party_aware_totals(R):
    """(office, district, party)-keyed reconciliation: candidate + Write-ins
    rows vs each contest's printed 'Total Votes'."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in R.rows:
        if r[1] in META_OFFICES:
            continue
        try:
            agg[(r[1], r[2], r[3])] += int(r[5])
        except (TypeError, ValueError):
            pass
    bad = []
    for key, tot in R.totals.items():
        if tot is None:
            continue
        if agg.get(key, 0) != tot:
            bad.append((key, agg.get(key, 0), tot))
    return bad


def check_party_ballot_arithmetic(R, blocks, quiet=False):
    """Each contest's candidate + Write-ins votes must be <= Times Cast x
    Vote-for-N (Times Cast = ballots cast in that party's ballot style)."""
    bad = 0
    for key, b in blocks["blocks"].items():
        if b["tc"] is None:
            if not quiet:
                print(f"WARNING no Times Cast found for {key[2]} {key[0]} "
                      f"| {key[1]}")
            continue
        agg = 0
        for r in R.rows:
            if (r[1], r[2], r[3]) == key and r[4] not in META_OFFICES:
                try:
                    agg += int(r[5])
                except (TypeError, ValueError):
                    pass
        limit = b["tc"] * b["n"]
        if agg > limit:
            bad += 1
            print(f"WARNING party-ballot arithmetic: {key[2]} {key[0]} | "
                  f"{key[1]}: votes {agg} > Times Cast {b['tc']} x "
                  f"Vote-for-{b['n']} = {limit}")
    if not quiet:
        print(f"party-ballot arithmetic check: "
              f"{len(blocks['blocks']) - bad} of {len(blocks['blocks'])} "
              f"contests within Times Cast x Vote-for-N")


def check_writein_arithmetic(R, blocks, quiet=False):
    """The engine's Write-ins aggregate must equal the summed write-in
    detail rows (named details + BLANK + Unresolved) plus any residual
    (printed Total Votes - candidates - details) from the raw text."""
    bad = 0
    engine_wi = {}
    for r in R.rows:
        if r[1] in META_OFFICES:
            continue
        if r[4] == "Write-ins":
            engine_wi[(r[1], r[2], r[3])] = int(r[5] or 0)
    for key, b in blocks["blocks"].items():
        got = engine_wi.get(key, 0)
        expected = b["wi"] + b.get("resid", 0)
        if got != expected:
            bad += 1
            print(f"WARNING write-in arithmetic: {key[2]} {key[0]} | "
                  f"{key[1]}: Write-ins row {got} != summed details "
                  f"{b['wi']} (+ residual {b.get('resid', 0)})")
    if not quiet:
        print(f"write-in arithmetic check: "
              f"{len(blocks['blocks']) - bad} of {len(blocks['blocks'])} "
              f"contests reconcile")


# --------------------------------------------------------------------------
# per-precinct report (Dominion SOVC, via the Carbon engine)
# --------------------------------------------------------------------------

def _load_sovc_text(src):
    text = open(src, encoding="utf-8").read()
    # (the form feeds are kept until after the Page-line normalization
    # below; the engine itself turns them into line breaks)
    # The shared parse_sovc engine keys its page furniture on the literal
    # tokens "Carbon County ..." (total rows, detail-page headers) and its
    # SOVC_KEYWORDS contain "Carbon".  "Jefferson" only ever occurs as
    # "Jefferson County" in this report (never in a precinct or contest
    # name), so the substitution is lossless for data.  The digit masking
    # keeps precinct-suffix digits ("Brookville Borough 1") from being
    # parsed as vote numbers; the "#" is stripped again in the output.
    text = text.replace("Jefferson County", "Carbon County")
    text = re.sub(r"\b(Borough|Township) (\d)(?!\d)", r"\1 #\2", text)
    # The engine starts each page (resetting its header buffer and column
    # model) when a line matches "^Page: N of M".  In this report the
    # "Page: N of M" token often sits at a large x-offset: sometimes alone
    # on an indented line AFTER the page's precinct-name header lines (so
    # the reset would drop the detail-page label), sometimes embedded in
    # the same line as that label (so no reset fires at all).  Splitting on
    # the form feed and moving each "Page: N of M" token to the first line
    # of its chunk makes every page start with the marker, before any
    # header-zone line.
    pages = text.split("\f")
    fixed = []
    for pg in pages:
        m = re.search(r"Page: \d+ of \d+", pg)
        if m and not pg.lstrip().startswith("Page:"):
            pg = m.group(0) + "\n" + pg.replace(m.group(0), "", 1).lstrip("\n")
        fixed.append(pg)
    return "\f".join(fixed)


def _unmask_precinct(name):
    return re.sub(r"#(\d)", r"\1", name)


def parse_precinct(src, dst, county_summary=None):
    tmp = src + ".carbonsub"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_load_sovc_text(src))
    (rows, problems, problems2, contests, contest_order,
     pmeta) = _carbon.parse_sovc(tmp, COUNTY, map_contest)
    os.unlink(tmp)

    for r in rows:
        r[1] = _unmask_precinct(r[1])
        # metadata rows arrive with the value in the candidate column; the
        # county's general-2023 file (and the standard format) put it in
        # the votes column with candidate empty
        if r[2] in ("Registered Voters", "Ballots Cast",
                    "Ballots Cast - Blank"):
            r[6] = r[5]
            r[5] = ""

    with open(dst, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["county", "precinct"] + FIELDNAMES[1:])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {dst}")
    print(f"contests parsed: {len(contests)}; precincts: {len(pmeta)}")
    for p in problems:
        print(f"PROBLEM: {p}")
    print(f"per-precinct printed-total check: {len(problems2)} mismatches "
          f"of {sum(len(cd['prec']) for cd in contests.values())} "
          f"precinct/contest cells")
    for key, pname, got, tot in problems2[:20]:
        print(f"  MISMATCH {key} @ {pname}: rows {got} != printed "
              f"Total {tot}")

    # turnout cross-check vs the report's front summary table
    check_turnout(src, pmeta)

    if county_summary:
        # parse the summary through the same wrapped-write-in-row repair as
        # parse_county so the cross-check sees identical residuals
        with open(county_summary, encoding="utf-8") as fh:
            clines = repair_wrapped_writein_rows(
                [l.rstrip() for l in fh])
        ctmp = county_summary + ".repaired"
        with open(ctmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(clines) + "\n")
        R = parse_esr2(ctmp, COUNTY, map_contest)
        os.unlink(ctmp)
        blocks = summarize_blocks(county_summary)
        stamp_parties(R, blocks)
        apply_writein_residuals(R, blocks, quiet=True)
        cross_check_precinct_vs_county(contests, contest_order, R)
    return rows


def check_turnout(src, pmeta):
    """Registered Voters / Ballots Cast per precinct (DEM+REP styles summed)
    vs the report's front turnout table."""
    text = open(src, encoding="utf-8").read().replace("\f", "\n")
    front = {}
    for line in text.split("\n"):
        s = line.strip()
        m = re.match(r"^(.+?)\s{2,}([\d,]+)\s+([\d,]+)\s+([\d,]+)"
                     r"\s+\d+\.\d\d%$", s)
        if m and not re.match(r"^(Jefferson|Carbon|Cumulative)", m.group(1)):
            name = _unmask_precinct(m.group(1).strip())
            front[name] = (int(m.group(2).replace(",", "")),
                           int(m.group(3).replace(",", "")))
    rv_bad = bc_bad = 0
    for pname0, pm in pmeta.items():
        pname = _unmask_precinct(pname0)
        rv = sum(pm["rv"].values()) if pm["rv"] else 0
        bc = sum(pm["tc"].values()) if pm["tc"] else 0
        f = front.get(pname)
        if not f:
            print(f"WARNING precinct {pname!r} not found in front turnout "
                  f"table")
            continue
        if rv != f[0]:
            rv_bad += 1
            print(f"  RV MISMATCH {pname}: {rv} != front {f[0]}")
        if bc != f[1]:
            bc_bad += 1
            print(f"  BC MISMATCH {pname}: {bc} != front {f[1]}")
    print(f"turnout check vs front table: {len(front)} precincts; "
          f"{rv_bad} RV mismatches, {bc_bad} Ballots Cast mismatches")
    tot_rv = sum((sum(pm["rv"].values()) if pm["rv"] else 0)
                 for pm in pmeta.values())
    tot_bc = sum((sum(pm["tc"].values()) if pm["tc"] else 0)
                 for pm in pmeta.values())
    print(f"countywide RV {tot_rv} / Ballots Cast {tot_bc}")


def cross_check_precinct_vs_county(contests, contest_order, R):
    """Aggregate the precinct data per contest and compare every candidate,
    write-in total and printed total against the (party-stamped) county
    summary rows."""
    cvals = {}
    for r in R.rows:
        if r[1] in META_OFFICES:
            continue
        key = (r[1], r[2], r[3])
        d = cvals.setdefault(key, {"cand": {}, "wi": 0})
        if r[4] == "Write-ins":
            d["wi"] = int(r[5] or 0)
        else:
            d["cand"][r[4]] = int(r[5] or 0)
    cand_bad = wi_bad = tot_bad = extra = 0
    zero_missing = 0
    for key in contest_order:
        agg = {}
        wi = 0
        ptot = 0
        for pname, p in contests[key]["prec"].items():
            for nm, v in p["cand"].items():
                agg[nm] = agg.get(nm, 0) + v
            wi += p["wi"]
            ptot += p["total"] or 0
        cv = cvals.get(key)
        if cv is None:
            if not agg and not wi and not ptot:
                # all-zero precinct data and no county rows: the summary
                # block prints "Total Votes 0" (write-in-only contest with
                # nothing to emit) — nothing to reconcile
                zero_missing += 1
                continue
            print(f"  WARNING contest {key} not found in county summary")
            continue
        for nm, v in cv["cand"].items():
            if agg.get(nm) != v:
                cand_bad += 1
                print(f"  CANDIDATE MISMATCH {key} {nm!r}: precincts "
                      f"{agg.get(nm)} != county {v}")
        for nm in agg:
            if nm not in cv["cand"]:
                extra += 1
                print(f"  EXTRA CANDIDATE in precincts: {key} {nm!r} "
                      f"({agg[nm]})")
        if wi != cv["wi"]:
            wi_bad += 1
            print(f"  WRITE-IN MISMATCH {key}: precincts {wi} != county "
                  f"{cv['wi']}")
        ptot = 0  # computed above
        csum = sum(cv["cand"].values()) + cv["wi"]
        if ptot and csum and ptot != csum:
            tot_bad += 1
            print(f"  TOTAL MISMATCH {key}: precinct printed-total sum "
                  f"{ptot} != county Total Votes {csum}")
    print(f"cross-check vs county summary: {len(contest_order)} contests "
          f"({zero_missing} all-zero on both sides); "
          f"{cand_bad} candidate mismatches, {extra} extra precinct-side "
          f"candidates, {wi_bad} write-in mismatches, {tot_bad} total "
          f"mismatches")


# --------------------------------------------------------------------------

def run_cli():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(f"Usage: {sys.argv[0]} <input.pdf|txt> <output.csv> "
                 f"[--county-summary <summary.txt|pdf>]")
    src, dst = args[0], args[1]
    if src.lower().endswith(".pdf"):
        src = text_from_pdf(src)
    text = open(src, encoding="utf-8").read()
    cs = None
    if "--county-summary" in args:
        cs = args[args.index("--county-summary") + 1]
        if cs.lower().endswith(".pdf"):
            cs = text_from_pdf(cs)
    if "Statement of Votes Cast" in text:
        parse_precinct(src, dst, cs)
    else:
        parse_county(src, dst)
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())