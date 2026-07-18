#!/usr/bin/env python3
"""
Armstrong County, PA Official Precinct Results PDF Fetcher

Armstrong County publishes official results as one PDF per municipality, linked
from its "Election Results - Official" page:

    https://co.armstrong.pa.us/images/resources/electionresults/official/1.pdf
    https://co.armstrong.pa.us/images/resources/electionresults/official/2.pdf
    ...

This downloads every PDF whose filename is a number and concatenates them into a
single PDF. The number ordering is the county's own and matches the alphabetical
municipality ordering on the page (1 = Apollo Borough, 62 = Worthington Borough).

Files with non-numeric names are skipped by design -- notably totals.pdf, which is
the countywide summary rather than a precinct report.

Note the county overwrites this "official" directory each election, so the PDFs it
returns are whichever election is current (as of this writing, the May 19, 2026
primary). Pass --page to point at a different or saved copy of the page.

Usage:
    python pa_armstrong_official_pdf_fetcher.py
    python pa_armstrong_official_pdf_fetcher.py -o "Armstrong County Precinct Results.pdf"
    python pa_armstrong_official_pdf_fetcher.py --page saved.html --keep-parts parts/
"""

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup
from pypdf import PdfWriter

PAGE_URL = "https://co.armstrong.pa.us/index.php/resourses-m/election-results-official-m"
DEFAULT_OUTPUT = "Armstrong County Precinct Results.pdf"

# e.g. ".../official/12.pdf" -> 12. Anchored to the whole stem so that
# totals.pdf, 2024-summary.pdf etc. do not match.
NUMBERED_PDF_RE = re.compile(r"^(\d+)\.pdf$", re.IGNORECASE)


def parse_pdf_links(html: str, base_url: str = PAGE_URL) -> List[Tuple[int, str]]:
    """Extract (number, absolute_url) for every PDF link whose name is a number.

    Returned in ascending numeric order -- not the lexicographic order the raw
    hrefs would give, which would interleave 10 between 1 and 2.
    """
    soup = BeautifulSoup(html, "html.parser")

    found = {}
    for link in soup.find_all("a", href=True):
        url = urllib.parse.urljoin(base_url, link["href"])
        filename = Path(urllib.parse.urlparse(url).path).name
        match = NUMBERED_PDF_RE.match(filename)
        if match:
            found[int(match.group(1))] = url

    return sorted(found.items())


def read_page(source: str) -> str:
    """Read the results page from a URL or a local file path."""
    if source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=60)
        response.raise_for_status()
        return response.text
    return Path(source).read_text(encoding="utf-8", errors="replace")


def download(url: str, dest: Path) -> None:
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    # The site serves an HTML error page with a 200 for missing files, so verify
    # this is really a PDF before it reaches the merger.
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"not a PDF (got {response.content[:20]!r})")

    dest.write_bytes(response.content)


def merge(paths: List[Path], output: Path) -> int:
    """Concatenate PDFs in the given order. Returns the total page count."""
    writer = PdfWriter()
    for path in paths:
        writer.append(str(path))
    pages = len(writer.pages)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as f:
        writer.write(f)
    writer.close()
    return pages


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT, help=f"output PDF (default: {DEFAULT_OUTPUT!r})")
    parser.add_argument("--page", default=PAGE_URL, help="results page URL or saved HTML file")
    parser.add_argument("--keep-parts", metavar="DIR", help="keep the downloaded per-municipality PDFs in DIR")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between downloads (default: 0.5)")
    args = parser.parse_args()

    links = parse_pdf_links(read_page(args.page), base_url=PAGE_URL)
    if not links:
        sys.exit(f"No numbered PDF links found on {args.page}")
    print(f"Found {len(links)} numbered PDFs ({links[0][0]}-{links[-1][0]})")

    if args.keep_parts:
        parts_dir = Path(args.keep_parts)
        parts_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        import tempfile
        parts_dir = Path(tempfile.mkdtemp(prefix="armstrong-pdfs-"))
        cleanup = True

    try:
        downloaded, failed = [], []
        for number, url in links:
            dest = parts_dir / f"{number}.pdf"
            try:
                download(url, dest)
                downloaded.append(dest)
                print(f"  [{number:>2}] {url.rsplit('/', 1)[-1]:<10} {dest.stat().st_size:>8,} bytes")
            except (requests.RequestException, ValueError) as e:
                failed.append((number, e))
                print(f"  [{number:>2}] FAILED: {e}")
            time.sleep(args.delay)

        if not downloaded:
            sys.exit("Nothing downloaded; not writing an output PDF.")

        output = Path(args.output)
        pages = merge(downloaded, output)
        print(f"\nWrote {output} ({len(downloaded)} PDFs, {pages} pages, {output.stat().st_size:,} bytes)")

        if failed:
            # Loud, and a non-zero exit: a silently short merge looks like a
            # complete one, and these feed precinct results.
            print(f"\nWARNING: {len(failed)} PDF(s) missing from the merge: "
                  f"{', '.join(str(n) for n, _ in failed)}")
            sys.exit(1)
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(parts_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
