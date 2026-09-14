#!/usr/bin/env python3
"""
transcribe_pdf.py

Phase 1 of PDF -> markdown pipeline: extract text from a PDF, page by page,
with page-number tags so a later LLM transcription pass can preserve
citeable page references.

Usage:
    python3 transcribe_pdf.py input.pdf output.txt
    python3 transcribe_pdf.py input.pdf output.txt --layout
    python3 transcribe_pdf.py input.pdf output.txt --split-dir pages/
    python3 transcribe_pdf.py input.pdf output.txt --page-offset 4
    python3 transcribe_pdf.py input.pdf output.txt --known-pair 5=1

Output format:
    Each page is wrapped like this, so downstream tools/agents can
    reliably find page boundaries. A printed_page tag is always
    included alongside pdf_page -- if no --page-offset/--known-pair is
    given, the offset defaults to 0, so printed_page just matches
    pdf_page:

        <!-- pdf_page: 1 -->
        <!-- printed_page: 1 -->
        # Chapter 3: Combat
        Body text follows normally...

        <!-- pdf_page: 2 -->
        <!-- printed_page: 2 -->
        ...extracted text for page 2...

    With --page-offset/--known-pair set, printed_page reflects the
    computed offset instead:

        <!-- pdf_page: 5 -->
        <!-- printed_page: 1 -->
        ...extracted text for page 5...

Heading detection (automatic, no flag needed):
    The script does a first pass over the whole document to find the
    most common font size (the body-text baseline), then a second pass
    where lines meaningfully larger and/or bolder than that baseline
    get prefixed with markdown heading markers (#, ##, ###) based on
    how much larger they are. Short line length is also required, to
    avoid mistaking large pull-quotes/callouts for headings.

    This is a heuristic, same idea as what dedicated tools like
    `marker` use internally. It won't be perfect on every layout
    (e.g. headings styled with color/caps instead of size, or
    idiosyncratic stat-block titles) -- spot check a sample of pages
    against the source PDF, especially chapter openers and sidebars.

Running heads / footers (automatic):
    The first pass also finds "page furniture": lines near the very
    top or bottom of the page whose text (with digits collapsed, so
    page numbers match each other) repeats across several pages AT THE
    SAME vertical position. These are dropped from the output entirely
    -- otherwise a corner footer gets bucketed into a column and lands
    mid-sentence in the extracted text. The fixed-y requirement keeps
    repeated BODY text safe (a formulaic sentence that happens to fall
    near the page edge on several pages sits at varying heights).
    The detected patterns are printed at the end of the run with the
    printed (book) pages they were seen on, using the same offset as
    the printed_page tags -- check them against the source, and re-run
    with --not-furniture TEXT to keep any false positive.

Dehyphenation (automatic):
    A body line ending in "-" whose next body line starts with a
    lowercase letter is joined (hyphen removed). Column layouts
    hyphenate aggressively, and unjoined fragments pollute grep
    results in the extracted text. Heading lines are never joined.

Notes:
    - "pdf_page" is the 1-indexed position in the FILE, not necessarily
      the printed page number in the book (front matter, chapter-based
      numbering, etc. can offset this).
    - Use --page-offset when the book's printed page numbers are a
      constant amount behind the file's page count (e.g. a cover and
      table of contents push everything back by N pages). The offset
      is: pdf_page_number - printed_page_number.
    - Easier alternative: --known-pair PDF_PAGE=PRINTED_PAGE lets you
      give one known pair (e.g. "5=1" if PDF page 5 is printed page 1)
      and the offset is computed for you. Use whichever pair is
      convenient to check by eye -- doesn't have to be page 1.
    - This only works for a CONSTANT offset. If numbering resets or
      shifts partway through (common with chapter-based numbering, or
      unnumbered inserts), a single offset can't describe the whole
      book -- you'd need a per-section offset table instead.
    - Pages that fall before printed page 1 (i.e. the front matter
      itself) are tagged as printed_page: front-matter rather than a
      negative or zero number.
    - If a page has zero extractable text, this usually means the PDF
      is scanned/image-based. The tag will still be written, but the
      body will be empty -- that's a signal to fall back to OCR or
      vision-based transcription for that page (see pdf-reading skill /
      ocrmypdf).
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz  # older PyMuPDF only exposes the fitz name
    except ImportError:
        sys.exit(
            "PyMuPDF is not installed. Install it with:\n"
            "    pip install pymupdf --break-system-packages\n"
            "(or just: pip install pymupdf, if you're not on a system-managed env)"
        )

BOLD_FLAG = 1 << 4  # PyMuPDF span flags bit for bold

# Fraction of the page height at the top and bottom treated as the
# running-head/footer band for furniture detection.
FURNITURE_BAND = 0.08
# A band line's normalized text must appear on at least this many pages
# to be treated as furniture.
FURNITURE_MIN_PAGES = 3
# Real furniture sits at a fixed vertical position; occurrences are
# grouped into clusters no looser than this many points, and only a
# cluster with enough pages qualifies. Repeated body text at varying
# heights never clusters, so it survives.
FURNITURE_Y_CLUSTER = 2.0
# At drop time, a line must sit within this many points of a
# qualifying cluster's position.
FURNITURE_Y_MATCH = 3.0


def normalize_furniture(text: str) -> str:
    """Collapse digits so 'page 142' and 'page 17' count as the same
    furniture pattern, then case-fold."""
    return re.sub(r"\d+", "#", text).strip().lower()


def reorder_columns(lines: list[dict], page_width: float) -> list[dict]:
    """
    Reading-order fix for multi-column pages. The naive approach of
    sorting by (y-band, x) assumes text rows line up across columns --
    they usually don't, since paragraphs run different lengths in each
    column, which is what causes jumbled/interleaved output.

    Instead: sort top-to-bottom first, then walk down the page bucketing
    each line into a left or right column based on its horizontal
    position. A line wide enough to span most of the page (a heading,
    a full-width caption) is treated as a break -- it flushes whatever
    has accumulated in the left column, then the right column, in that
    order, before the full-width line itself is emitted. This handles
    the common pattern of a heading interrupting two columns partway
    down the page, not just a single header/footer at the very top.

    The column split point is derived from the page's actual text
    extent, not the page rectangle -- asymmetric/mirrored margins move
    the gutter off the page midpoint, and a split point sitting inside
    one column's text misbuckets lines and triggers spurious flushes.
    For the same reason, "straddles the split" requires meaningful
    overlap on BOTH sides, so a justified line poking a point or two
    past the split doesn't fracture the columns.
    """
    if not lines:
        return lines

    min_x0 = min(l["bbox"][0] for l in lines)
    max_x1 = max(l["bbox"][2] for l in lines)
    text_width = max_x1 - min_x0
    col_split = (min_x0 + max_x1) / 2
    full_width_threshold = 0.6 * text_width
    straddle_margin = 0.03 * page_width

    sorted_lines = sorted(lines, key=lambda l: l["bbox"][1])

    output: list[dict] = []
    left_buf: list[dict] = []
    right_buf: list[dict] = []

    for line in sorted_lines:
        x0, _, x1, _ = line["bbox"]
        width = x1 - x0
        # A line counts as "full width" (breaks both columns) if it's
        # wide relative to the text area, OR if it extends meaningfully
        # past the column split on both sides -- catches short, centered
        # titles that don't meet the width threshold but still aren't
        # part of either column.
        straddles_split = (
            x0 < col_split - straddle_margin and x1 > col_split + straddle_margin
        )
        if width >= full_width_threshold or straddles_split:
            output.extend(left_buf)
            output.extend(right_buf)
            left_buf, right_buf = [], []
            output.append(line)
        else:
            center = (x0 + x1) / 2
            (left_buf if center < col_split else right_buf).append(line)

    output.extend(left_buf)
    output.extend(right_buf)
    return output


def get_page_lines(
    page,
    layout: bool = False,
    furniture: dict[str, list[float]] | None = None,
) -> list[dict]:
    """
    Extract each line of text on a page along with its font size and
    whether it's bold, using PyMuPDF's structured dict output. This is
    the basis for both plain text assembly and heading detection.

    Size/bold come from the line's dominant span (most text), so a
    decorative drop cap or ornament span can't inflate a body line
    into a heading.

    If a furniture map (normalized text -> qualifying y positions) is
    given, lines in the top/bottom band whose normalized text matches
    AND that sit at one of those y positions are dropped.
    """
    raw = page.get_text("dict")
    page_height = page.rect.height
    band_top = FURNITURE_BAND * page_height
    band_bottom = (1 - FURNITURE_BAND) * page_height

    lines = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block; skip images
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s["text"] for s in spans).strip()
            if not text:
                continue

            if furniture is not None:
                y_center = (line["bbox"][1] + line["bbox"][3]) / 2
                in_band = y_center < band_top or y_center > band_bottom
                if in_band:
                    positions = furniture.get(normalize_furniture(text))
                    if positions is not None and any(
                        abs(y_center - fy) <= FURNITURE_Y_MATCH for fy in positions
                    ):
                        continue

            dominant = max(spans, key=lambda s: len(s["text"].strip()))
            lines.append(
                {
                    "bbox": line["bbox"],
                    "text": text,
                    "size": dominant["size"],
                    "bold": bool(dominant["flags"] & BOLD_FLAG),
                }
            )

    if layout:
        lines = reorder_columns(lines, page.rect.width)

    return lines


def analyze_document(doc) -> tuple[float, dict[str, list[float]], dict[str, list[int]]]:
    """
    First pass over the whole document. Two things come out of it:

    - The most common font size, weighted by character count: the
      "body text" baseline that headings are measured against.
    - The furniture map: normalized text of lines in the top/bottom
      band (digits collapsed, so page numbers match each other) that
      recur on at least FURNITURE_MIN_PAGES pages AT THE SAME vertical
      position. Running heads, footers, and page numbers sit at a
      fixed y on every page; repeated body text (e.g. a formulaic
      sentence that happens to fall near the page edge on several
      pages) lands at varying heights and is left alone.

    Returns (body_size, furniture y-positions by pattern, pdf pages by
    pattern -- the latter only for reporting).

    Column reordering is skipped here since neither result depends on
    line order.
    """
    size_counter: Counter = Counter()
    band_hits: dict[str, list[tuple[int, float]]] = {}

    for page_number, page in enumerate(doc, start=1):
        page_height = page.rect.height
        band_top = FURNITURE_BAND * page_height
        band_bottom = (1 - FURNITURE_BAND) * page_height
        for line in get_page_lines(page):
            size_counter[round(line["size"], 1)] += len(line["text"])
            y_center = (line["bbox"][1] + line["bbox"][3]) / 2
            if y_center < band_top or y_center > band_bottom:
                band_hits.setdefault(normalize_furniture(line["text"]), []).append(
                    (page_number, y_center)
                )

    body_size = size_counter.most_common(1)[0][0] if size_counter else 10.0

    furniture_positions: dict[str, list[float]] = {}
    furniture_pages: dict[str, list[int]] = {}
    for text, hits in band_hits.items():
        hits.sort(key=lambda h: h[1])
        clusters: list[list[tuple[int, float]]] = []
        for page_number, y in hits:
            if clusters and y - clusters[-1][-1][1] <= FURNITURE_Y_CLUSTER:
                clusters[-1].append((page_number, y))
            else:
                clusters.append([(page_number, y)])
        for cluster in clusters:
            pages = sorted({p for p, _ in cluster})
            if len(pages) >= FURNITURE_MIN_PAGES:
                ys = [y for _, y in cluster]
                furniture_positions.setdefault(text, []).append(sum(ys) / len(ys))
                furniture_pages.setdefault(text, []).extend(pages)
    for pages in furniture_pages.values():
        pages.sort()

    return body_size, furniture_positions, furniture_pages


def heading_prefix(line: dict, body_size: float) -> str:
    """
    Decide whether a line looks like a heading based on its size/weight
    relative to the document's body-text baseline, and if so, return
    the markdown prefix ("# ", "## ", "### ") to use. Returns "" for
    ordinary body text.

    Short-line requirement guards against large pull-quotes/callouts
    being mistaken for headings.
    """
    if body_size <= 0:
        return ""
    ratio = line["size"] / body_size
    word_count = len(line["text"].split())
    if word_count > 12:
        return ""

    if ratio >= 1.8:
        return "# "
    if ratio >= 1.4:
        return "## "
    if ratio >= 1.15 or (line["bold"] and ratio >= 1.05):
        return "### "
    return ""


def assemble_page_text(lines: list[dict], body_size: float) -> str:
    """
    Turn a page's ordered lines into output text: apply heading
    prefixes, and join hyphenated line breaks (a body line ending in
    "-" followed by a body line starting with a lowercase letter).
    """
    out_lines: list[str] = []
    for line in lines:
        prefix = heading_prefix(line, body_size)
        text = line["text"]
        if (
            not prefix
            and out_lines
            and not out_lines[-1].startswith("#")
            and out_lines[-1].endswith("-")
            and text[:1].islower()
        ):
            out_lines[-1] = out_lines[-1][:-1] + text
            continue
        out_lines.append(f"{prefix}{text}")
    return "\n".join(out_lines)


def write_book_json(out_path: Path, title: str, page_offset: int) -> None:
    book_json = out_path.parent / "book.json"
    if book_json.exists():
        print(f"NOTE: {book_json} already exists; not overwriting it.")
        return
    book_json.write_text(
        json.dumps({"title": title, "page_offset": page_offset}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {book_json}")


def extract(
    pdf_path: Path,
    out_path: Path,
    layout: bool = False,
    split_dir: Path | None = None,
    page_offset: int = 0,
    not_furniture: list[str] | None = None,
    title: str | None = None,
) -> None:
    doc = fitz.open(pdf_path)
    chunks = []

    if split_dir:
        split_dir.mkdir(parents=True, exist_ok=True)

    empty_pages = []

    print("Analyzing fonts and page furniture...", file=sys.stderr)
    body_size, furniture, furniture_pages = analyze_document(doc)
    for needle in not_furniture or []:
        for pattern in [p for p in furniture if needle.lower() in p]:
            del furniture[pattern]
            del furniture_pages[pattern]
    total_pages = len(doc)

    for i, page in enumerate(doc, start=1):
        print(f"\rProcessing page {i}/{total_pages} ({i * 100 // total_pages}%)", end="", file=sys.stderr, flush=True)

        lines = get_page_lines(page, layout=layout, furniture=furniture)
        text = assemble_page_text(lines, body_size)

        if not text.strip():
            empty_pages.append(i)

        header = f"<!-- pdf_page: {i} -->"

        printed = i - page_offset
        header += f"\n<!-- printed_page: {printed if printed >= 1 else 'front-matter'} -->"

        chunk = f"{header}\n{text.strip()}\n"
        chunks.append(chunk)

        if split_dir:
            (split_dir / f"page_{i:04d}.txt").write_text(chunk, encoding="utf-8")

    out_path.write_text("\n".join(chunks), encoding="utf-8")

    print(file=sys.stderr)  # newline after the \r progress line
    print(f"Wrote {len(chunks)} pages to {out_path}")
    if title:
        write_book_json(out_path, title, page_offset)
    print(f"Detected body text size: {body_size}pt (headings are sized/weighted relative to this)")
    if furniture_pages:
        print(
            f"Dropped {len(furniture_pages)} running-head/footer pattern(s) "
            f"(digits shown as #) -- check none are real content; re-run with "
            f"--not-furniture TEXT to keep a false positive:"
        )
        for pattern in sorted(furniture_pages):
            labels = [
                str(p - page_offset) if p - page_offset >= 1 else f"front-matter (pdf {p})"
                for p in furniture_pages[pattern]
            ]
            shown = ", ".join(labels[:10])
            more = f", ... (+{len(labels) - 10} more)" if len(labels) > 10 else ""
            print(f"  {pattern!r} -- book pages {shown}{more}")
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
        help="Detect left/right columns and read each one top-to-bottom in full "
        "before moving to the next, instead of the raw PDF content-stream order. "
        "Use this for multi-column rulebooks.",
    )
    parser.add_argument(
        "--split-dir",
        type=Path,
        default=None,
        help="Also write one .txt file per page into this directory",
    )
    parser.add_argument(
        "--page-offset",
        type=int,
        default=0,
        help="Constant offset: pdf_page_number - printed_page_number. "
        "E.g. if PDF page 5 is printed page 1, pass 4.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Book title; when given, writes book.json ({title, page_offset}) "
        "next to the output file for the run_chapters.py pipeline.",
    )
    parser.add_argument(
        "--not-furniture",
        action="append",
        default=[],
        metavar="TEXT",
        help="Keep any detected running-head/footer pattern containing TEXT "
        "(case-insensitive substring; repeatable). Use after checking the "
        "dropped-pattern report for false positives.",
    )
    parser.add_argument(
        "--known-pair",
        type=str,
        default=None,
        metavar="PDF_PAGE=PRINTED_PAGE",
        help="Compute the offset from one known pair instead of doing the "
        "subtraction yourself, e.g. --known-pair 5=1",
    )
    args = parser.parse_args()

    page_offset = args.page_offset
    if args.known_pair:
        if args.page_offset:
            sys.exit("Use either --page-offset or --known-pair, not both.")
        try:
            pdf_page_str, printed_page_str = args.known_pair.split("=")
            page_offset = int(pdf_page_str) - int(printed_page_str)
        except ValueError:
            sys.exit(
                f"--known-pair must look like PDF_PAGE=PRINTED_PAGE "
                f"(e.g. 5=1), got: {args.known_pair!r}"
            )

    if not args.pdf_path.exists():
        sys.exit(f"File not found: {args.pdf_path}")

    extract(
        args.pdf_path,
        args.out_path,
        layout=args.layout,
        split_dir=args.split_dir,
        page_offset=page_offset,
        not_furniture=args.not_furniture,
        title=args.title,
    )


if __name__ == "__main__":
    main()
