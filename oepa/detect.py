"""Fingerprint a source file to guess which parser family it belongs to."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from oepa.registry import find_entries
from oepa.signatures import SIGNATURES, FormatSignature


@dataclass
class FamilyScore:
    family: str
    score: float
    matched: list
    suggested_engine: str
    description: str


@dataclass
class DetectionResult:
    input_path: str
    ranked: list  # list[FamilyScore], best first
    variant_hints: dict
    min_confidence: float = 0.35

    @property
    def top(self):
        return self.ranked[0] if self.ranked else None

    @property
    def family(self):
        return self.top.family if self.top else "unknown"

    @property
    def confidence(self):
        return self.top.score if self.top else 0.0

    def to_dict(self) -> dict:
        return {
            "input": self.input_path,
            "family": self.family,
            "confidence": round(self.confidence, 3),
            "ranked": [
                {"family": r.family, "score": round(r.score, 3), "matched": r.matched}
                for r in self.ranked
            ],
            "variant_hints": self.variant_hints,
        }

    def print_report(self) -> None:
        print(f"Detected format for {self.input_path}:")
        if not self.ranked or self.confidence < self.min_confidence:
            print("  No confident match (best guess below min_confidence).")
        for r in self.ranked[:5]:
            marker = "->" if r is self.top else "  "
            print(f"  {marker} {r.family:<22} score={r.score:.2f}  {r.description}")
        print()

        if self.confidence < self.min_confidence:
            print("Recommendation: no strong signature match.")
            print("  Try `oepa parse --family llm-image --level precinct --county <Name>` as a fallback,")
            print("  or inspect the PDF manually and pick the closest family from `oepa list`.")
            return

        top = self.top
        print(f"Best match: {top.family} (confidence {top.score:.2f})")
        print(f"  Suggested engine: {top.suggested_engine}")

        county_hint = self.variant_hints.get("county_guess")
        if county_hint:
            existing = find_entries(county=county_hint, family=top.family)
            if existing:
                e = existing[0]
                print(f"  A parser already exists for {county_hint}: python {e.script} <pdf> <output.csv>")
                return

        print(f"  No existing county config detected. Scaffold one with:")
        cty = county_hint or "<county>"
        print(f"    uv run oepa scaffold {self.input_path} --county {cty} --family {top.family}")

        if self.variant_hints.get("skip_prefix_candidates"):
            print(f"  Harvested repeated header lines (candidate skip_prefixes):")
            for line in self.variant_hints["skip_prefix_candidates"][:8]:
                print(f"    - {line!r}")


def extract_text(input_path: str, pages: int = 3) -> str:
    path = Path(input_path)
    if path.suffix.lower() == ".pdf":
        import pdfplumber

        chunks = []
        with pdfplumber.open(str(path)) as pdf:
            for page in pdf.pages[:pages]:
                chunks.append(page.extract_text() or "")
        return "\n".join(chunks)
    # Non-PDF inputs: read as text directly (EL30 HTML exports, XML, etc.)
    return path.read_text(errors="ignore")


def _guess_county(text: str) -> str:
    match = re.search(r"([A-Z][A-Z ]+?)\s+COUNTY,\s*PENNSYLVANIA", text)
    if match:
        return match.group(1).strip().title()
    match = re.search(r"^([A-Z][A-Z ]{2,})\s+COUNTY\s*$", text, re.MULTILINE)
    if match:
        return match.group(1).strip().title()
    return ""


def _harvest_skip_prefix_candidates(text: str, min_repeats: int = 2) -> list:
    """Lines that repeat verbatim across the sampled pages are usually page
    headers/footers -- good starting candidates for a new config's
    skip_prefixes list."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    counts = Counter(lines)
    repeated = [line for line, n in counts.items() if n >= min_repeats and len(line) > 3]
    # Prefer shorter, header-shaped lines over long data rows.
    repeated.sort(key=len)
    return repeated[:15]


def _non_pdf_shortcut(input_path: str) -> DetectionResult | None:
    path = Path(input_path)
    suffix = path.suffix.lower()
    if suffix == ".xml" or suffix == ".zip":
        return DetectionResult(
            input_path=input_path,
            ranked=[FamilyScore("clarity", 1.0, ["file extension"],
                                 "parsers/clarity_parser.py", "Clarity Elections XML export")],
            variant_hints={},
        )
    if suffix in (".html", ".htm", ".txt"):
        text = path.read_text(errors="ignore")
        if re.search(r"\.\s{2}\.", text):
            return DetectionResult(
                input_path=input_path,
                ranked=[FamilyScore("el30", 0.9, ["'.  .' delimiter pattern"],
                                     "parsers/el30_parser.py / el30a_parser.py / el30b_parser.py",
                                     "PA state EL30 report export")],
                variant_hints={},
            )
    return None


def detect_format(input_path: str, pages: int = 3) -> DetectionResult:
    shortcut = _non_pdf_shortcut(input_path)
    if shortcut:
        return shortcut

    text = extract_text(input_path, pages=pages)

    ranked = []
    for sig in SIGNATURES:
        score, matched = sig.score(text)
        ranked.append(FamilyScore(sig.family, score, matched, sig.suggested_engine, sig.description))
    ranked.sort(key=lambda r: r.score, reverse=True)

    variant_hints = {
        "county_guess": _guess_county(text),
        "skip_prefix_candidates": _harvest_skip_prefix_candidates(text),
    }
    return DetectionResult(input_path=input_path, ranked=ranked, variant_hints=variant_hints)
