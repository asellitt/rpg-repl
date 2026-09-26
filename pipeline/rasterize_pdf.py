#!/usr/bin/env python3
"""
rasterize_pdf.py — render a PDF's pages to standalone images so the source
PDF no longer needs to be kept around.

Independent of transcribe_pdf.py: this script only cares about page images,
not text/chapters. Run it once per book, any time before or after
transcribe_pdf.py, then delete the source PDF once pages/manifest.json
exists and looks right.

Front matter (anything before printed page 1) is never rendered, and
output files are named by PRINTED page number, not PDF file order — so
a viewer never has to do offset math, it just opens page-000N.png for
printed page N. Concretely:

    --page-offset 3     PDF page 3 IS printed page 1 (and everything
                         before it, PDF pages 1-2, is skipped)
                         -> written as page-0001.png, PDF page 4 as
                            page-0002.png, and so on

    --known-pair PDF_PAGE=PRINTED_PAGE   e.g. --known-pair 10=3
                         PDF page 10 is printed page 3 (general form,
                         doesn't have to be printed page 1) -- PDF
                         pages before the implied printed-page-1 are
                         skipped the same way.

NOTE: this --page-offset is defined differently from transcribe_pdf.py's
own --page-offset flag, which is the raw difference
(pdf_page_number - printed_page_number) rather than "which PDF page is
printed page 1". Don't assume the same number means the same thing in
both scripts -- check with the worked example above if unsure.
--known-pair's meaning (a PDF_PAGE=PRINTED_PAGE pair) IS the same in
both scripts.

If you give neither flag, this looks for books/<system>/<book>/book.json
(written by `transcribe_pdf.py --title ...`) and reuses its
"page_offset" -- which is in transcribe_pdf.py's difference convention,
converted automatically to line up with this script's own numbering, so
you only have to work the offset out once. Falls back to no offset
(PDF page 1 = printed page 1) if nothing is found.

This only handles a single CONSTANT offset -- not per-section numbering
resets, same limitation as transcribe_pdf.py.

Usage:
    python3 pipeline/rasterize_pdf.py BOOK.pdf --system <system> --book <slug>
    python3 pipeline/rasterize_pdf.py BOOK.pdf --system <system> --book <slug> \
        --page-offset 3 --dpi 150 --format png

By default this writes to pages/<system>/<slug>/ (repo root, alongside
systems/ — not under books/), matching the served-vs-pipeline directory
split. Pass --out-dir to write somewhere else instead.

Output:
    <out-dir>/page-0001.png   (printed page 1 -- whichever PDF page that is)
    <out-dir>/page-0002.png   (printed page 2)
    ...
    <out-dir>/manifest.json   {"source": "...", "sha256": "...",
                                "pdf_page_count": N, "printed_page_count": M,
                                "dpi": 150, "format": "png",
                                "page_offset_pdf_page_of_printed_1": 3,
                                "generated": "..."}

Re-running against the same PDF/dpi/format is safe and idempotent and
skips re-rendering; if only the offset changed, files are renamed/
re-numbered without re-rasterizing. Use --force to redo images anyway.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF; `import fitz` directly is deprecated
except ImportError:
    sys.exit(
        "rasterize_pdf.py requires PyMuPDF.\n"
        "Install it with: pip install pymupdf"
    )


BOOKS_ROOT = Path("books")  # read-only here — where transcribe_pdf.py writes book.json
PAGES_ROOT = Path("pages")  # repo-root pages/, parallel to systems/ — not under books/


def resolve_first_pdf_page(system: str, book: str, page_offset_arg: int | None, known_pair_arg: str | None) -> int:
    """
    Returns the PDF page number that is printed page 1 (this script's own
    convention -- see module docstring for why it differs from
    transcribe_pdf.py's --page-offset).

    --known-pair PDF_PAGE=PRINTED_PAGE is general (doesn't have to name
    printed page 1) and is converted: first_pdf_page = PDF_PAGE - PRINTED_PAGE + 1.

    If neither flag is given, falls back to books/<system>/<book>/book.json's
    "page_offset" (transcribe_pdf.py's difference convention: pdf_page -
    printed_page), converted the same way: first_pdf_page = difference + 1.
    Defaults to 1 (no front matter) if nothing is found.
    """
    if known_pair_arg:
        if page_offset_arg is not None:
            sys.exit("Use either --page-offset or --known-pair, not both.")
        try:
            pdf_page_str, printed_page_str = known_pair_arg.split("=")
            return int(pdf_page_str) - int(printed_page_str) + 1
        except ValueError:
            sys.exit(
                f"--known-pair must look like PDF_PAGE=PRINTED_PAGE "
                f"(e.g. 5=1), got: {known_pair_arg!r}"
            )

    if page_offset_arg is not None:
        return page_offset_arg

    book_json_path = BOOKS_ROOT / system / book / "book.json"
    if book_json_path.exists():
        try:
            data = json.loads(book_json_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        difference = data.get("page_offset")
        if difference is not None:
            first_pdf_page = int(difference) + 1
            print(
                f"using page_offset={difference} from {book_json_path} "
                f"(set by transcribe_pdf.py) -> PDF page {first_pdf_page} is printed page 1"
            )
            return first_pdf_page

    return 1


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def existing_manifest(out_dir: Path) -> dict | None:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def rasterize(pdf_path: Path, out_dir: Path, dpi: int, fmt: str, quality: int, first_pdf_page: int, force: bool) -> None:
    if not pdf_path.exists():
        sys.exit(f"error: source PDF not found: {pdf_path}")
    if first_pdf_page < 1:
        sys.exit(f"error: first_pdf_page must be >= 1, got {first_pdf_page}")

    source_hash = sha256_of(pdf_path)
    prior = existing_manifest(out_dir)
    if prior and not force:
        images_match = prior.get("sha256") == source_hash and prior.get("dpi") == dpi and prior.get("format") == fmt
        prior_first = prior.get("page_offset_pdf_page_of_printed_1")
        if images_match and prior_first == first_pdf_page:
            print(f"up to date: {out_dir} already matches {pdf_path.name} at {dpi} DPI ({fmt}); skipping")
            return
        if images_match:
            print(f"offset changed (PDF page of printed 1: {prior_first} -> {first_pdf_page}); re-rendering to renumber files")
        else:
            print("manifest exists but is stale (source/dpi/format changed); re-rendering")

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.*"):
        old.unlink()

    doc = fitz.open(pdf_path)
    pdf_page_count = doc.page_count
    printed_page_count = pdf_page_count - first_pdf_page + 1
    if printed_page_count < 1:
        doc.close()
        sys.exit(
            f"error: first_pdf_page ({first_pdf_page}) is past the end of the "
            f"PDF ({pdf_page_count} pages) — nothing would be rendered"
        )
    pad = max(4, len(str(printed_page_count)))

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    # pix.save() natively writes png/pnm/pgm/ppm/pbm/pam/psd/ps/jpg/jpeg.
    save_kwargs = {"jpg_quality": quality} if fmt in ("jpg", "jpeg") else {}

    for pdf_page_num in range(first_pdf_page, pdf_page_count + 1):
        printed = pdf_page_num - first_pdf_page + 1
        page = doc.load_page(pdf_page_num - 1)  # PyMuPDF is 0-indexed
        pix = page.get_pixmap(matrix=matrix)
        filename = f"page-{printed:0{pad}d}.{fmt}"
        pix.save(str(out_dir / filename), **save_kwargs)
        print(f"  {filename}  (pdf p.{pdf_page_num})")

    doc.close()

    skipped = first_pdf_page - 1

    manifest = {
        "source": pdf_path.name,
        "sha256": source_hash,
        "pdf_page_count": pdf_page_count,
        "printed_page_count": printed_page_count,
        "dpi": dpi,
        "format": fmt,
        "page_offset_pdf_page_of_printed_1": first_pdf_page,
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {printed_page_count} pages (printed 1-{printed_page_count}) + manifest.json to {out_dir}")
    if skipped:
        print(f"skipped {skipped} front-matter PDF page(s) (pdf 1-{skipped})")
    print(f"source PDF ({pdf_path}) is no longer needed and can be deleted.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="source PDF to rasterize")
    parser.add_argument("--system", required=True, help="RPG system directory name, e.g. 'blades-in-the-dark'")
    parser.add_argument("--book", required=True, help="book slug, e.g. 'core-rulebook'")
    parser.add_argument("--out-dir", type=Path, default=None, help=f"override output dir (default: {PAGES_ROOT}/<system>/<book>/)")
    parser.add_argument("--dpi", type=int, default=150, help="render resolution (default: 150; try 200 for dense tables/small print)")
    parser.add_argument("--format", choices=["png", "jpg"], default="png", help="output image format (default: png)")
    parser.add_argument("--quality", type=int, default=80, help="jpg quality 1-100, ignored for png (default: 80)")
    parser.add_argument(
        "--page-offset",
        type=int,
        default=None,
        metavar="N",
        help="Which PDF page IS printed page 1 (e.g. 3 -> PDF pages 1-2 are "
        "skipped, PDF page 3 becomes page-0001). NOT the same convention as "
        "transcribe_pdf.py's --page-offset -- see module docstring. If "
        "omitted (and --known-pair isn't given), derived from "
        "books/<system>/<book>/book.json.",
    )
    parser.add_argument(
        "--known-pair",
        type=str,
        default=None,
        metavar="PDF_PAGE=PRINTED_PAGE",
        help="Same meaning as transcribe_pdf.py's flag of the same name, e.g. --known-pair 5=1",
    )
    parser.add_argument("--force", action="store_true", help="re-render even if manifest.json already matches this source")
    args = parser.parse_args()

    out_dir = args.out_dir if args.out_dir is not None else PAGES_ROOT / args.system / args.book
    first_pdf_page = resolve_first_pdf_page(args.system, args.book, args.page_offset, args.known_pair)

    rasterize(args.pdf, out_dir, args.dpi, args.format, args.quality, first_pdf_page, args.force)


if __name__ == "__main__":
    main()