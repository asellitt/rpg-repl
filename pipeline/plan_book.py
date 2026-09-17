#!/usr/bin/env python3
"""
Draft a book's chapters.json (unit map) from its extracted text.

For a new system, scaffolds systems/<system>/ first (note dirs plus a
conventions.md seeded from conventions_template.md — review its tag
taxonomy before running units).

Usage:
    python3 pipeline/plan_book.py --system cosmere --book mistborn-handbook
    python3 pipeline/plan_book.py --book <slug> --outline-only   # no LLM step
    python3 pipeline/plan_book.py --book <slug> --model opus

Requires books/<system>/<slug>/{extracted.txt, book.json} (transcribe_pdf.py
with --title produces both). The draft chapters.json is written by a
headless claude worker and MUST be reviewed before process_book.py.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYSTEMS = ROOT / "systems"
BOOKS = ROOT / "books"
PROMPT = ROOT / "pipeline" / "prompts" / "plan_book.md"
TEMPLATE = ROOT / "pipeline" / "conventions_template.md"


def scaffold_system(system: str) -> None:
    system_dir = SYSTEMS / system
    if system_dir.is_dir():
        return
    for sub in ("_meta", "_sources", "_index", "notes"):
        (system_dir / sub).mkdir(parents=True)
    (system_dir / "_meta" / "conventions.md").write_text(
        TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    default_display = system.replace("-", " ").title()
    (system_dir / "_meta" / "system.json").write_text(
        json.dumps({"display_name": default_display}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Scaffolded new system at {system_dir}")
    print("  -> REVIEW _meta/conventions.md: tailor the tag taxonomy before running units.")
    print(f"  -> REVIEW _meta/system.json: display name defaulted to '{default_display}'.")


def write_sources_stub(system: str, slug: str, config: dict, units: list[dict]) -> None:
    """Write the book's _sources note skeleton (chapter table derived from
    the drafted units) unless one already exists."""
    source_note = SYSTEMS / system / "_sources" / f"{slug}.md"
    if source_note.exists():
        return
    offset = config["page_offset"]
    rows: list[list] = []
    for unit in units:
        chapter = unit["title"].split(" — ")[0]
        if rows and rows[-1][0] == chapter:
            rows[-1][2] = unit["pdf_end"] - offset
        else:
            rows.append([chapter, unit["pdf_start"] - offset, unit["pdf_end"] - offset])
    table = "\n".join(f"| {c} | {a}–{b} |" for c, a, b in rows)
    source_note.write_text(f"""---
aliases: [{config['title']}]
tags: [source]
---

# {config['title']}

- Extracted text: `books/{system}/{slug}/extracted.txt`
- Page offset: `pdf_page = printed_page + {offset}`

## Chapters (printed pages)

| Chapter | Pages |
|---|---|
{table}

## Referenced but defined elsewhere

Concepts this book uses without defining them; their links stay
unresolved until a defining book is processed:

(none recorded yet — the postflight pass maintains this list)
""", encoding="utf-8")
    print(f"Wrote source-note stub: {source_note.relative_to(ROOT)}")


def build_outline(extracted: Path, page_offset: int) -> str:
    lines = ["Headings by pdf page (printed page = pdf - "
             f"{page_offset}); EMPTY marks pages with no extractable text:"]
    page = 0
    page_had_text = False
    empty_pages: list[int] = []

    def close_page() -> None:
        if page and not page_had_text:
            empty_pages.append(page)

    for line in extracted.read_text(encoding="utf-8").splitlines():
        m = re.match(r"<!-- pdf_page: (\d+) -->", line)
        if m:
            close_page()
            page = int(m.group(1))
            page_had_text = False
            continue
        if line.strip() and not line.startswith("<!--"):
            page_had_text = True
        if line.startswith("# ") and len(line) > 6:
            lines.append(f"{page}: {line[2:].strip()}")
    close_page()

    lines.append(f"\nTotal pdf pages: {page}")
    lines.append(f"Pages with no extractable text: {empty_pages}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--book", required=True)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--outline-only", action="store_true",
                        help="write the outline and stop (no LLM draft)")
    parser.add_argument("--force", action="store_true",
                        help="redraft even if chapters.json exists")
    args = parser.parse_args()

    book_dir = BOOKS / args.system / args.book
    extracted = book_dir / "extracted.txt"
    book_json = book_dir / "book.json"
    chapters_path = book_dir / "chapters.json"
    if not extracted.exists() or not book_json.exists():
        sys.exit(f"Need {extracted} and {book_json} first "
                 f"(transcribe_pdf.py with --title produces both).")
    if chapters_path.exists() and not args.force:
        sys.exit(f"{chapters_path} already exists (--force to redraft).")

    config = json.loads(book_json.read_text(encoding="utf-8"))
    scaffold_system(args.system)

    (book_dir / "work").mkdir(exist_ok=True)
    outline_path = book_dir / "work" / "outline.txt"
    outline_path.write_text(build_outline(extracted, config["page_offset"]), encoding="utf-8")
    print(f"Wrote outline to {outline_path}")
    if args.outline_only:
        return 0

    prompt = PROMPT.read_text(encoding="utf-8").format(
        book_title=config["title"],
        book=args.book,
        system=args.system,
        page_offset=config["page_offset"],
        outline=outline_path.relative_to(ROOT),
        system_dir=(SYSTEMS / args.system).relative_to(ROOT),
        chapters_path=chapters_path.relative_to(ROOT),
    )
    print(f"Drafting unit map with claude -p ({args.model})...", flush=True)
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", args.model, "--permission-mode", "acceptEdits"],
        cwd=ROOT,
    )
    if result.returncode != 0:
        sys.exit(f"claude exited {result.returncode}")

    units = json.loads(chapters_path.read_text(encoding="utf-8"))
    seen_ids: set[str] = set()
    last_end = 0
    for unit in units:
        for key in ("id", "title", "moc", "pdf_start", "pdf_end"):
            if key not in unit:
                sys.exit(f"Draft invalid: unit missing '{key}': {unit}")
        if unit["id"] in seen_ids:
            sys.exit(f"Draft invalid: duplicate unit id {unit['id']}")
        seen_ids.add(unit["id"])
        if not unit["pdf_start"] <= unit["pdf_end"]:
            sys.exit(f"Draft invalid: bad range in {unit['id']}")
        if unit["pdf_start"] <= last_end:
            sys.exit(f"Draft invalid: {unit['id']} overlaps the previous unit")
        last_end = unit["pdf_end"]

    write_sources_stub(args.system, args.book, config, units)
    print(f"\nDraft OK: {len(units)} units in {chapters_path}")
    print("REVIEW the draft (unit sizes, skips, MOC names), then run:")
    print(f"  python3 pipeline/process_book.py --system {args.system} --book {args.book}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
