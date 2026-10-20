#!/usr/bin/env python3
"""Carbon County 2022 primary (May 17, 2022 General Primary) — precinct results
from the Dominion "Statement of Votes Cast" (OFFICIAL RESULTS, 9/23/2022) PDF.

Layout (same family as Carbon's 2022 SOVC-general parser, adapted for the
closed primary):
  * Pages 1-3: per-precinct turnout table (Registered Voters, Cards Cast,
    Voters Cast, % Turnout), one row per precinct, total (both parties).
  * All following pages: one table per contest.  Precincts are rows,
    candidates are right-aligned numeric columns with rotated 90-degree
    headers.  Countywide contests are split into ~22-precinct chunks
    (chunk pages with a left half "Times Cast / Registered Voters" block,
    then continuation pages); county-committee contests occupy one precinct
    per table (title carries the municipality).
  * Every contest title carries the ballot party, e.g.
    "United States Senator (DEM) (Vote for 1)"; candidate column headers
    repeat it ("Alex Khalil (DEM)").  The left-block "Times Cast"/
    "Registered Voters" are DEM-or-REP turnout for that contest's ballot.

Write-in columns are labelled either "Unresolved Write-In"/"Write-In" or a
specific name followed by "Qualified Write In" (Dominion prints write-in
candidates that reached the report threshold); all write-in columns of a
contest are summed into a single "Write Ins" row carrying the contest party
(primary rule: write-ins must carry the contest party).

No per-precinct vote-method breakdown exists in the SOVC (it is printed for
"All Counting Groups" combined), so the 7-column header is emitted.
Registered Voters / Ballots Cast rows are emitted per precinct for the total
(party="") from the turnout pages, and per party (DEM/REP) from the contest
left blocks ("Registered Voters" / "Times Cast"), matching the 2022-primary
per-party RV/BC convention (Indiana/McKean precedent).

Usage: pa_carbon_primary_2022_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys

import pdfplumber

NUM_RE = re.compile(r"^[\d,]+$")
PARTY_RE = re.compile(r"\s*\((DEM|REP)\)\s*")
JUNK_NAME_RE = re.compile(
    r"^(carbon county\b.*|precinct\b.*|cumulative\b.*|registered\b.*|"
    r"voters\b.*|cards cast\b.*|times cast\b.*)$",
    re.I,
)
NAME_X_MAX = 150  # precinct name words start at x=20; numeric cols start ~157

# header text that marks a write-in column, even when it carries a name
WRITEIN_SUFFIX = "qualified write in"


def clean_num(val):
    return val.replace(",", "").strip()


def decode_rotated(word):
    return word[::-1].strip()


def group_lines(words, tol=3.0):
    """Group upright words into visual lines by top coordinate."""
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for line in lines:
            if abs(line["top"] - w["top"]) <= tol:
                line["words"].append(w)
                break
        else:
            lines.append({"top": w["top"], "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
    lines.sort(key=lambda l: l["top"])
    return lines


def cluster_x(values, threshold=25):
    """Cluster numeric word x0 values into column groups; return centers."""
    centers = []
    for v in sorted(values):
        if centers and v - centers[-1][-1] <= threshold:
            centers[-1].append(v)
        else:
            centers.append([v])
    return [sum(c) / len(c) for c in centers]


def classify_header(text):
    """Column role from its decoded rotated header text."""
    t = text.lower()
    if "times cast" in t or "registered" in t:
        return "turnout"
    if "total votes" in t:
        return "total"
    if "write" in t or "scattered" in t or "qualified" in t:
        return "writein"
    return "candidate"


class PageTable:
    """One data table parsed out of a single PDF page.

    Two-block pages (chunk first pages; every countywide contest's A-pages
    and every single-precinct committee table) carry a left block
    (Times Cast + Registered Voters) and a right block (precinct name +
    right-hand candidate columns); the blocks are parsed independently and
    merged row-by-row.
    """

    def __init__(self, page):
        words = page.extract_words(extra_attrs=["upright"])
        self.upright = [w for w in words if w["upright"]]
        self.rotated = [w for w in words if not w["upright"]]
        self.lines = group_lines(self.upright)
        self.title = self._find_title()
        self.two_block = sum(1 for w in self.upright if w["text"] == "Precinct") >= 2
        self._build()

    def _find_title(self):
        for line in self.lines:
            text = " ".join(w["text"] for w in line["words"])
            if "(Vote for" in text and line["top"] < 80:
                return re.sub(r"\s+", " ", text).strip()
        return None

    def _number_columns(self, words):
        xs = []
        for w in words:
            if NUM_RE.match(w["text"]) and w["x0"] >= NAME_X_MAX:
                xs.append(w["x0"])
        return cluster_x(xs)

    def _column_headers(self, centers):
        """Map each numeric column to its decoded rotated header text."""
        parts = {c: [] for c in centers}
        for w in sorted(self.rotated, key=lambda w: (w["x0"], -w["top"])):
            best = min(centers, key=lambda c: abs(c - w["x0"]))
            if abs(best - w["x0"]) <= 26:
                parts[best].append(decode_rotated(w["text"]))
        return {c: re.sub(r"\s+", " ", " ".join(p)).strip()
                for c, p in parts.items()}

    def _parse_block(self, words, name_x_max=NAME_X_MAX, block_split=None):
        """Parse one block: returns (rows, columns)."""
        lines = group_lines(words)
        if block_split == "left":
            block_cols = [c for c in self.columns_all if c[0] < 380]
        elif block_split == "right":
            block_cols = [c for c in self.columns_all if c[0] >= 380]
        else:
            block_cols = self.columns_all
        centers = [c for c, _r, _h in block_cols]

        anchors = []
        for line in lines:
            nums = [(w, min(centers, key=lambda c: abs(c - w["x0"])))
                    for w in line["words"] if NUM_RE.match(w["text"])
                    and w["x0"] >= name_x_max]
            if nums:
                anchors.append({"top": line["top"], "nums": nums,
                                "name_words": [w for w in line["words"]
                                               if not NUM_RE.match(w["text"])
                                               and w["x0"] < name_x_max]})
        anchor_tops = [a["top"] for a in anchors]
        for line in lines:
            if line["top"] in anchor_tops:
                continue
            name_words = [w for w in line["words"]
                          if not NUM_RE.match(w["text"]) and w["x0"] < name_x_max]
            if not name_words or not anchors:
                continue
            text = " ".join(w["text"] for w in name_words).strip()
            if JUNK_NAME_RE.match(text):
                continue
            best = min(anchors, key=lambda a: abs(a["top"] - line["top"]))
            if abs(best["top"] - line["top"]) <= 12:
                best["name_words"].extend(name_words)

        rows = []
        for a in anchors:
            name_words = sorted(a["name_words"], key=lambda w: (w["top"], w["x0"]))
            name = re.sub(r"\s+", " ", " ".join(w["text"] for w in name_words)).strip()
            row = {"name": name, "values": {}}
            for w, col in a["nums"]:
                row["values"][col] = clean_num(w["text"])
            rows.append(row)
        return rows, block_cols

    def _build(self):
        centers = self._number_columns(
            [w for w in self.upright if w["x0"] >= NAME_X_MAX])
        headers = self._column_headers(centers)
        columns = []
        for c in centers:
            header = headers[c]
            role = classify_header(header) if header else "candidate"
            if self.title and not header:
                raise ValueError(f"unlabelled number column at x={c} on "
                                 f"contest page {pt.title!r}")
            columns.append((c, role, header))
        self.columns_all = columns

        if self.two_block:
            left_words = [w for w in self.upright if w["x0"] < 380]
            right_words = [w for w in self.upright if w["x0"] >= 380]
            left_rows, left_cols = self._parse_block(left_words,
                                                     block_split="left")
            right_rows, right_cols = self._parse_block(right_words,
                                                       name_x_max=500,
                                                       block_split="right")
            self.columns = left_cols + right_cols
            if len(left_rows) != len(right_rows):
                left_names = [r["name"] for r in left_rows]
                right_names = [r["name"] for r in right_rows]
                raise ValueError(f"block row mismatch {len(left_rows)} vs "
                                 f"{len(right_rows)}: left={left_names} "
                                 f"right={right_names}")
            self.rows = []
            for lrow, rrow in zip(left_rows, right_rows):
                lname, rname = lrow["name"], rrow["name"]
                if lname.replace(" ", "") != rname.replace(" ", ""):
                    raise ValueError(f"left/right name mismatch: {lname!r} vs "
                                     f"{rname!r}")
                row = {"name": lname,
                       "values": {**lrow["values"], **rrow["values"]}}
                self.rows.append(row)
        else:
            rows, cols = self._parse_block(self.upright)
            self.columns = cols
            self.rows = rows


def parse(path):
    pdf = pdfplumber.open(path)
    pages = [PageTable(p) for p in pdf.pages]

    # ---- turnout pages (before the first contest title) ----
    turnout = []
    county_turnout = None
    for pt in pages:
        if pt.title:
            break
        for row in pt.rows:
            name = row["name"]
            nums = [v for _, v in sorted(row["values"].items())]
            if re.match(r"carbon county\b", name, re.I):
                if "Total" in name and county_turnout is None:
                    county_turnout = nums
                continue
            if not name or "umulative" in name:
                continue
            if len(nums) < 3:
                raise ValueError(f"turnout row with {len(nums)} numbers: {name!r}")
            reg, cards, voters = nums[0], nums[1], nums[2]
            if cards != voters:
                sys.stderr.write(f"note: cards cast != voters cast for {name!r}: "
                                 f"{cards} vs {voters}\n")
            turnout.append((name, int(reg), int(voters)))

    # ---- contest pages ----
    # Column identity is (role, label, party); pages of the same contest are
    # keyed by label, not by geometry.
    contests = []
    current = None
    started = False
    for pt in pages:
        if not pt.title and not started:
            continue
        started = True
        if pt.title:
            current = {"title": pt.title, "values": {}, "county_totals": {},
                       "col_order": []}
            contests.append(current)
        if current is None:
            continue
        page_keys = {}
        for center, role, header in pt.columns:
            label = re.sub(r"\s+", " ", header).strip()
            if role == "candidate":
                if not label:
                    raise ValueError(f"unlabelled candidate column {center}")
                if not re.search(r"\((DEM|REP)\)$", label):
                    label = label + " (no party suffix!)"
                    role = "candidate"
                m = PARTY_RE.search(label)
                party = m.group(1) if m else ""
                label = re.sub(r"\s+", " ", PARTY_RE.sub("", label)).strip()
                key = ("candidate", label, party)
            elif role == "writein":
                # named write-ins ("Xxx Qualified Write In") and generic
                # ("Unresolved Write-In"/"Write-In") both sum into Write Ins
                low = label.lower()
                if low.endswith(WRITEIN_SUFFIX) and low != WRITEIN_SUFFIX:
                    label = label[: -len("qualified write in")].strip()
                else:
                    label = "Write Ins"
                key = ("writein", label, "")
            elif role == "total":
                key = (role, "Total Votes" if "total" in label.lower()
                       else label, "")
            else:  # turnout
                key = (role, label, "")
            page_keys[center] = key
            if role in ("turnout",):
                continue
            if key not in current["col_order"]:
                current["col_order"].append(key)
        for row in pt.rows:
            name = row["name"]
            if re.match(r"carbon county\b", name, re.I):
                if "Total" in name:
                    for col, v in row["values"].items():
                        current["county_totals"].setdefault(page_keys[col], v)
                continue
            if not name or "umulative" in name:
                continue
            slot = current["values"].setdefault(name, {})
            for col, v in row["values"].items():
                key = page_keys[col]
                if key in slot and slot[key] != v:
                    raise ValueError(f"conflicting value for {name!r} {key}: "
                                     f"{slot[key]} vs {v}")
                slot[key] = v

    return turnout, county_turnout, contests


def map_title(title):
    """(office, district, party) from a contest title."""
    t = re.sub(r"\s+", " ", title).strip()
    m = re.search(r"\((DEM|REP)\)", t)
    party = m.group(1) if m else ""
    t = re.sub(r"\s*\(Vote for \d+\)\s*$", "", t.strip()).strip()
    t = re.sub(r"\s*\(DEM\)\s*$", "", t.strip(), flags=re.I)
    t = re.sub(r"\s*\(REP\)\s*$", "", t.strip()).strip()
    if re.match(r"^United States Senator$", t, re.I):
        return "U.S. Senate", "", party
    if re.match(r"^Governor$", t, re.I):
        return "Governor", "", party
    if re.match(r"^Lieutenant Governor$", t, re.I):
        return "Lieutenant Governor", "", party
    m = re.match(r"^Representative in Congress - (\d+)", t, re.I)
    if m:
        return "U.S. House", m.group(1), party
    m = re.match(r"^Representative in the General Assembly - (\d+)", t, re.I)
    if m:
        return "State House", m.group(1), party
    t = re.sub(r"^Member of the (Democratic|Republican) State Committee$",
               r"Member of \1 State Committee", t, flags=re.I)
    return t, "", party


def normalize_candidate(label, office):
    name = re.sub(r"\s+", " ", label).strip()
    # match the middle-initial punctuation used by the 2022 Dominion files
    name = {"Douglas V Mastriano": "Douglas V. Mastriano"}.get(name, name)
    return name


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1:3]
    turnout, county_turnout, contests = parse(src)

    precinct_names = [t[0] for t in turnout]

    # --- per-party turnout harvested from contest-page left blocks ---
    # {(party, precinct): {"Registered Voters": n, "Times Cast": n,
    #                     "county": {...}}}
    party_turnout = {}
    for contest in contests:
        _office, _district, party = map_title(contest["title"])
        if party not in ("DEM", "REP"):
            continue
        for key, kind in ((("turnout", "Times Cast", ""), "Times Cast"),
                          (("turnout", "Registered Voters", ""),
                           "Registered Voters")):
            for pname, vals in contest["values"].items():
                if key in vals:
                    got = party_turnout.setdefault(
                        (party, pname), {}).setdefault(kind, int(vals[key]))
                    if got != int(vals[key]):
                        raise ValueError(f"turnout conflict {party} {pname} "
                                         f"{kind}: {got} vs {vals[key]}")

    rows = []
    problems = []
    overvotes = []
    for name, reg, ballots in turnout:
        rows.append(["Carbon", name, "Registered Voters", "", "", "", reg])
        rows.append(["Carbon", name, "Ballots Cast", "", "", "", ballots])
    for (party, pname), kinds in sorted(party_turnout.items()):
        for kind, office in (("Registered Voters", "Registered Voters"),
                             ("Times Cast", "Ballots Cast")):
            val = kinds.get(kind)
            if val is None:
                problems.append(f"missing party turnout {party} {kind} "
                                f"for {pname!r}")
                continue
            rows.append(["Carbon", pname, office, "", party, "", val])

    for contest in contests:
        office, district, party = map_title(contest["title"])
        if not party:
            problems.append(f"contest without party: {contest['title']!r}")
            continue
        cand_keys = [k for k in contest["col_order"] if k[0] == "candidate"]
        writein_keys = [k for k in contest["col_order"] if k[0] == "writein"]
        total_key = next((k for k in contest["col_order"]
                          if k[0] == "total" and "total" in k[1].lower()), None)

        precinct_vals = contest["values"]
        if "County Committee" not in office:
            missing = [n for n in precinct_names if n not in precinct_vals]
            extra = [n for n in precinct_vals if n not in precinct_names]
            if missing or extra:
                problems.append(f"{office} ({party}): precinct mismatch "
                                f"missing={missing} extra={extra}")

        for pname in sorted(set(precinct_names) | set(precinct_vals)):
            vals = precinct_vals.get(pname)
            if vals is None:
                continue
            cand_total = 0
            for key in cand_keys:
                _role, label, _party_col = key
                votes = int(vals.get(key, 0))
                rows.append(["Carbon", pname, office, district, party,
                             normalize_candidate(label, office), votes])
                cand_total += votes
            wi_total = 0
            for key in writein_keys:
                wi_total += int(vals.get(key, 0))
            if writein_keys:
                rows.append(["Carbon", pname, office, district, party,
                             "Write Ins", wi_total])
                cand_total += wi_total
            if total_key is not None and total_key in vals and \
                    int(vals[total_key]) != cand_total:
                # write-in detail columns count every distinct write-in
                # selection; "Total Votes" caps at the seats voted for, so a
                # precinct whose write-in detail sums above Total Votes is a
                # genuine overvote in the source.  Report, keep source values.
                diff = cand_total - int(vals[total_key])
                overvotes.append(f"{office} ({party}) / {pname}: write-in "
                                 f"detail sum {cand_total} exceeds Total "
                                 f"Votes {vals[total_key]} by {diff} "
                                 f"(source overvote, kept as printed)")

        # county-total cross-check (source's own printed totals)
        summed_cache = {}
        for key in contest["col_order"]:
            if key not in contest["county_totals"]:
                continue
            printed = int(contest["county_totals"][key])
            summed = sum(int(contest["values"].get(n, {}).get(key, 0))
                         for n in precinct_vals)
            summed_cache[key] = (printed, summed)
            if printed != summed:
                problems.append(f"{office} ({party}) / {key[1]}: county total "
                                f"{printed} != precinct sum {summed}")
        print(f"contest: {office} ({party}) district={district!r} "
              f"candidates={[k[1] for k in cand_keys]} "
              f"precincts={len(precinct_vals)} "
              f"county_totals={[[k[1], tuple(v)] for k, v in summed_cache.items()]}")

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)

    if county_turnout:
        print(f"county turnout (page 1-3 table): "
              f"registered={county_turnout[0]} cards={county_turnout[1]} "
              f"voters={county_turnout[2] if len(county_turnout) > 2 else county_turnout[1]}")
    d_rv = sum(v.get("Registered Voters", 0) for (p, _n), v in
               party_turnout.items() if p == "DEM")
    r_rv = sum(v.get("Registered Voters", 0) for (p, _n), v in
               party_turnout.items() if p == "REP")
    d_bc = sum(v.get("Times Cast", 0) for (p, _n), v in
               party_turnout.items() if p == "DEM")
    r_bc = sum(v.get("Times Cast", 0) for (p, _n), v in
               party_turnout.items() if p == "REP")
    print(f"party turnout sums: RV DEM={d_rv} REP={r_rv} "
          f"(total={d_rv + r_rv}); BC DEM={d_bc} REP={r_bc} "
          f"(total={d_bc + r_bc})")
    print(f"precincts: {len(precinct_names)}; rows written: {len(rows)}")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    if overvotes:
        print("NOTES (source overvotes, kept as printed):")
        for p in overvotes:
            print(" -", p)
    print("OK")


if __name__ == "__main__":
    main()