#!/usr/bin/env python3
"""
pdf_to_tagged_text.py

Phase 1 of PDF -> markdown pipeline: extract text from a PDF, page by page,
with page-number tags so a later LLM transcription pass can preserve
citeable page references.

Usage:
    python3 pdf_to_tagged_text.py input.pdf output.txt
    python3 pdf_to_tagged_text.py input.pdf output.txt --layout
    python3 pdf_to_tagged_text.py input.pdf output.txt --split-dir pages/

Output format:
    Each page is wrapped like this, so downstream tools/agents can
    reliably find page boundaries:

        <!-- pdf_page: 1 -->
        ...extracted text for page 1...

        <!-- pdf_page: 2 -->
        ...extracted text for page 2...

Notes:
    - "pdf_page" is the 1-indexed position in the FILE, not necessarily
      the printed page number in the book (front matter, chapter-based
      numbering, etc. can offset this). See --detect-printed-page below.
    - If a page has zero extractable text, this usually means the PDF
      is scanned/image-based. The tag will still be written, but the
      body will be empty -- that's a signal to fall back to OCR or
      vision-based transcription for that page (see pdf-reading skill /
      ocrmypdf).
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit(
        "PyMuPDF is not installed. Install it with:\n"
        "    pip install pymupdf --break-system-packages\n"
        "(or just: pip install pymupdf, if you're not on a system-managed env)"
    )


def guess_printed_page_number(page_text: str) -> str | None:
    """
    Best-effort heuristic: look for a lone number in the first or last
    few lines of the page text (common footer/header location for page
    numbers). This is NOT reliable -- always spot-check a sample before
    trusting it. Returns None if nothing plausible is found.
    """
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    candidates = lines[:3] + lines[-3:]
    for line in candidates:
        if re.fullmatch(r"\d{1,4}", line):
            return line
        # common "Page N" / "N of M" patterns
        m = re.fullmatch(r"(?:page\s*)?(\d{1,4})(?:\s*of\s*\d+)?", line, re.I)
        if m:
            return m.group(1)
    return None


def extract(
    pdf_path: Path,
    out_path: Path,
    layout: bool = False,
    split_dir: Path | None = None,
    detect_printed_page: bool = False,
) -> None:
    doc = fitz.open(pdf_path)
    chunks = []

    if split_dir:
        split_dir.mkdir(parents=True, exist_ok=True)

    empty_pages = []

    for i, page in enumerate(doc, start=1):
        mode = "text" if not layout else "blocks"
        if mode == "text":
            text = page.get_text("text")
        else:
            # Reconstruct rough reading order from block coordinates --
            # helps somewhat on multi-column pages. Still spot-check output.
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (round(b[1] / 20), b[0]))  # y-band, then x
            text = "\n".join(b[4] for b in blocks if b[4].strip())

        if not text.strip():
            empty_pages.append(i)

        header = f"<!-- pdf_page: {i} -->"
        if detect_printed_page:
            guess = guess_printed_page_number(text)
            if guess:
                header += f"\n<!-- printed_page_guess: {guess} -->"

        chunk = f"{header}\n{text.strip()}\n"
        chunks.append(chunk)

        if split_dir:
            (split_dir / f"page_{i:04d}.txt").write_text(chunk, encoding="utf-8")

    out_path.write_text("\n".join(chunks), encoding="utf-8")

    print(f"Wrote {len(chunks)} pages to {out_path}")
    if split_dir:
        print(f"Also wrote per-page files to {split_dir}/")
    if empty_pages:
        print(
            f"WARNING: {len(empty_pages)} page(s) had no extractable text "
            f"(likely scanned/image pages): {empty_pages}\n"
            f"  -> Consider OCR (ocrmypdf) or vision-based transcription for these."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf_path", type=Path, help="Path to input PDF")
    parser.add_argument("out_path", type=Path, help="Path to write combined tagged text file")
    parser.add_argument(
        "--layout",
        action="store_true",
        help="Use block-sorting mode for better multi-column reading order (slower, still imperfect)",
    )
    parser.add_argument(
        "--split-dir",
        type=Path,
        default=None,
        help="Also write one .txt file per page into this directory",
    )
    parser.add_argument(
        "--detect-printed-page",
        action="store_true",
        help="Best-effort guess at the printed page number from header/footer text (heuristic, verify manually)",
    )
    args = parser.parse_args()

    if not args.pdf_path.exists():
        sys.exit(f"File not found: {args.pdf_path}")

    extract(
        args.pdf_path,
        args.out_path,
        layout=args.layout,
        split_dir=args.split_dir,
        detect_printed_page=args.detect_printed_page,
    )


if __name__ == "__main__":
    main()