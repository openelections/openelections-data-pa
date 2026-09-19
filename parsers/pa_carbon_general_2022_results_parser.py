#!/usr/bin/env python3
"""Carbon County 2022 general — precinct results from the Dominion
"Statement of Votes Cast" (OFFICIAL RESULTS, 11/17/2022) PDF.

Layout (unique to this report, hence a standalone parser):
  * Pages 1-3: per-precinct turnout table (Registered Voters, Cards Cast,
    Voters Cast, % Turnout), one row per precinct, single block.
  * Pages 4-30: one table per contest.  Precincts are rows, candidates are
    right-aligned numeric columns with rotated 90-degree headers.  Each
    contest's precinct list is split into ~22-row chunks; a chunk's first
    page is two-block (left half: Times Cast + Registered Voters; right
    half: precinct name + first candidates), the following page is
    one-block (precinct name + remaining candidates, then Scattered
    Write-in, Total Votes, Qualified Write In).
  * Precinct names wrap across lines around the numeric anchor line, so
    name parts are re-assembled by assigning every non-numeric word to the
    nearest numeric anchor row.

No vote-method breakdown exists in the source, so the 7-column header is
emitted.  Registered Voters / Ballots Cast rows come from the turnout
pages (Voters Cast is used for Ballots Cast; Cards Cast matches for every
precinct).  For the Governor contest the running mate is dropped from the
candidate name, matching the convention in the other 2022 Dominion files.

Usage: pa_carbon_general_2022_results_parser.py <input.pdf> <output.csv>
"""

import csv
import re
import sys

import pdfplumber

NUM_RE = re.compile(r"^[\d,]+$")
PARTY_RE = re.compile(r"\s*\([A-Z]{2,4}\)\s*")
JUNK_NAME_RE = re.compile(
    r"^(carbon county\b.*|precinct\b.*|cumulative\b.*|registered\b.*|"
    r"voters\b.*|cards cast\b.*|times cast\b.*)$",
    re.I,
)
NAME_X_MAX = 150  # precinct name words start at x=20; numeric cols start ~157


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
    if "qualified" in t:
        return "qwi"
    if "write" in t or "scattered" in t:
        return "writein"
    return "candidate"


class PageTable:
    """One data table parsed out of a single PDF page.

    Two-block pages (chunk first pages) carry a left block (Times Cast +
    Registered Voters) and a right block (precinct name + first candidate
    columns); the blocks are parsed independently and merged row-by-row.
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

    def _apply_role_fallbacks(self, columns):
        """Positional fallbacks for unlabelled contest-page columns."""
        roles = [r for _, r, _ in columns]
        if "turnout" in roles:
            return columns
        if "qwi" not in roles and columns:
            columns[-1] = (columns[-1][0], "qwi", columns[-1][2])
            roles[-1] = "qwi"
        if "total" not in roles and columns:
            columns[-2] = (columns[-2][0], "total", columns[-2][2])
            roles[-2] = "total"
        if "writein" not in roles and len(columns) >= 3:
            columns[-3] = (columns[-3][0], "writein", columns[-3][2])
            roles[-3] = "writein"
        return columns

    def _parse_block(self, words, name_x_max=NAME_X_MAX, block_split=None):
        """Parse one block: returns (rows, columns).

        block_split: None (single block) or ('left'|'right') selecting the
        page-wide columns that belong to this half of a two-block page.
        """
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
            if not name_words:
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
        self.columns_all = [(c, classify_header(headers[c]), headers[c])
                            for c in centers]

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
                raise ValueError(f"block row mismatch {len(left_rows)} vs "
                                 f"{len(right_rows)}")
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
            self.columns = self._apply_role_fallbacks(cols)
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
    # Column identity is (role, label, party): the same candidate reappears
    # at slightly different x-centers on each chunk page, so values must be
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
            if role == "candidate":
                label = re.sub(r"\s+", " ", PARTY_RE.sub(" ", header)).strip()
                party_m = re.search(r"\(([A-Z]{2,4})\)", header)
                party = party_m.group(1) if party_m else ""
                if not label:
                    raise ValueError(f"unlabelled candidate column {center} "
                                     f"(header={header!r})")
                key = (role, label, party)
            elif role == "writein":
                key = (role, "Write Ins", "")
            elif role == "total":
                key = (role, "Total Votes", "")
            else:  # turnout / qwi: tracked so row values can be looked up
                key = (role, header, "")
            page_keys[center] = key
            if role in ("turnout", "qwi"):
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


def map_office(title):
    t = re.sub(r"\s*\(Vote for\s+\d+\)\s*$", "", title).strip()
    t = re.sub(r"\s+", " ", t)
    if re.match(r"^United States Senator$", t, re.I):
        return "U.S. Senate", ""
    if re.match(r"^Governor and Lieutenant Governor$", t, re.I):
        return "Governor", ""
    m = re.match(r"^Representative in Congress - (\d+)", t, re.I)
    if m:
        return "U.S. House", m.group(1)
    m = re.match(r"^Representative in the General Assembly - (\d+)", t, re.I)
    if m:
        return "State House", m.group(1)
    return t, ""


def normalize_candidate(label, office):
    name = re.sub(r"\s+", " ", label).strip()
    if office == "Governor" and "/" in name:
        name = name.split("/")[0].strip()
    # match the middle-initial punctuation used by the other 2022 Dominion
    # county files (Schuylkill) for these candidates
    name = {"Douglas V Mastriano": "Douglas V. Mastriano",
            "Richard L Weiss": "Richard L. Weiss"}.get(name, name)
    return name


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = sys.argv[1:3]
    turnout, county_turnout, contests = parse(src)

    precinct_names = [t[0] for t in turnout]
    rows = []
    problems = []
    for name, reg, ballots in turnout:
        rows.append(["Carbon", name, "Registered Voters", "", "", "", reg])
        rows.append(["Carbon", name, "Ballots Cast", "", "", "", ballots])

    for contest in contests:
        office, district = map_office(contest["title"])
        # col_order is insertion-ordered by page, so candidates keep their
        # printed order (chunk page-A candidates first, then B-page ones)
        cand_keys = [k for k in contest["col_order"]
                     if k[0] == "candidate"]
        writein_key = next((k for k in contest["col_order"]
                            if k[0] == "writein"), None)
        total_key = next((k for k in contest["col_order"]
                          if k[0] == "total"), None)

        precinct_vals = contest["values"]
        missing = [n for n in precinct_names if n not in precinct_vals]
        extra = [n for n in precinct_vals if n not in precinct_names]
        if missing or extra:
            problems.append(f"{office}: precinct mismatch missing={missing} "
                            f"extra={extra}")

        for pname in precinct_names:
            vals = precinct_vals.get(pname, {})
            cand_total = 0
            for key in cand_keys:
                _role, label, party = key
                votes = int(vals.get(key, 0))
                rows.append(["Carbon", pname, office, district, party,
                             normalize_candidate(label, office), votes])
                cand_total += votes
            if writein_key is not None:
                wi = int(vals.get(writein_key, 0))
                rows.append(["Carbon", pname, office, district, "",
                             "Write Ins", wi])
                cand_total += wi
            if total_key is not None and total_key in vals and \
                    int(vals[total_key]) != cand_total:
                problems.append(f"{office} / {pname}: candidate sum {cand_total} "
                                f"!= Total Votes {vals[total_key]}")

        # county-total cross-check (source's own printed totals)
        for key in contest["col_order"]:
            if key not in contest["county_totals"]:
                continue
            printed = int(contest["county_totals"][key])
            summed = sum(int(contest["values"].get(n, {}).get(key, 0))
                         for n in precinct_names)
            if printed != summed:
                problems.append(f"{office} / {key[1]}: county total {printed} "
                                f"!= precinct sum {summed}")

    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["county", "precinct", "office", "district", "party",
                    "candidate", "votes"])
        w.writerows(rows)

    if county_turnout:
        print(f"county turnout: registered={county_turnout[0]} "
              f"ballots={county_turnout[2] if len(county_turnout) > 2 else county_turnout[1]}")
    print(f"precincts: {len(precinct_names)}; rows written: {len(rows)}")
    for contest in contests:
        office, district = map_office(contest["title"])
        print(f"contest: {office} (district={district!r}) "
              f"columns={[k[1:] for k in contest['col_order']]} "
              f"county_totals={ {k[1]: v for k, v in contest['county_totals'].items()} }")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()