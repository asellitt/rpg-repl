#!/usr/bin/env python3
"""
Drive headless Claude Code over a book's chapter units, writing rules
notes into that system's vault under systems/.

Each book lives at books/<system>/<book-slug>/ containing:
    book.json       {"title": ..., "page_offset": N}
    chapters.json   the unit map (pdf page ranges, MOC names)
    extracted.txt   output of transcribe_pdf.py
    work/           sliced chunks + state.json (created on first run)
    logs/           headless worker transcripts

Usage:
    python3 run_chapters.py                          # all pending units
    python3 run_chapters.py 01 05b                   # unit ids/prefixes
    python3 run_chapters.py --system cosmere --book stormlight-handbook 02
    python3 run_chapters.py --model opus 04
    python3 run_chapters.py --dry-run 01
    python3 run_chapters.py --force 01
    python3 run_chapters.py --status

--system defaults to the sole directory under systems/ (else cosmere);
--book defaults to the sole book under books/<system>/ and is required
when there are several. State lives per book, so runs are resumable.
A validator hard failure stops the run.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEMS = ROOT / "systems"
BOOKS = ROOT / "books"
PROMPT_TEMPLATE = ROOT / "prompt_template.md"


def sole_or_default(parent: Path, default: str | None, kind: str) -> str:
    candidates = sorted(
        p.name for p in parent.iterdir() if p.is_dir() and not p.name.startswith(".")
    ) if parent.is_dir() else []
    if default:
        return default
    if len(candidates) == 1:
        return candidates[0]
    sys.exit(f"Specify --{kind}: found {candidates or 'none'} under {parent}")


class Book:
    def __init__(self, system: str, slug: str):
        self.system = system
        self.slug = slug
        self.dir = BOOKS / system / slug
        self.vault = SYSTEMS / system
        if not self.dir.is_dir():
            sys.exit(f"No book at {self.dir}")
        if not self.vault.is_dir():
            sys.exit(f"No system vault at {self.vault}")
        config = json.loads((self.dir / "book.json").read_text(encoding="utf-8"))
        self.title = config["title"]
        self.page_offset = config["page_offset"]
        self.chapters = json.loads((self.dir / "chapters.json").read_text(encoding="utf-8"))
        self.extracted = self.dir / "extracted.txt"
        self.work = self.dir / "work"
        self.logs = self.dir / "logs"
        self.state_file = self.work / "state.json"

    def load_state(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {}

    def save_state(self, state: dict) -> None:
        self.work.mkdir(exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def load_pages(book: Book) -> dict[int, str]:
    pages: dict[int, str] = {}
    current = None
    buf: list[str] = []
    for line in book.extracted.read_text(encoding="utf-8").splitlines():
        m = re.match(r"<!-- pdf_page: (\d+) -->", line)
        if m:
            if current is not None:
                pages[current] = "\n".join(buf)
            current = int(m.group(1))
            buf = [line]
        else:
            buf.append(line)
    if current is not None:
        pages[current] = "\n".join(buf)
    return pages


def slice_unit(book: Book, pages: dict[int, str], unit: dict) -> Path:
    book.work.mkdir(exist_ok=True)
    chunk = "\n\n".join(
        pages[p] for p in range(unit["pdf_start"], unit["pdf_end"] + 1) if p in pages
    )
    path = book.work / f"{unit['id']}.txt"
    path.write_text(chunk, encoding="utf-8")
    return path


def build_prompt(book: Book, unit: dict, chunk_path: Path) -> str:
    return PROMPT_TEMPLATE.read_text(encoding="utf-8").format(
        title=unit["title"],
        moc=(book.vault / "_index" / f"{unit['moc']}.md").relative_to(ROOT),
        chunk=chunk_path.relative_to(ROOT),
        vault=book.vault.relative_to(ROOT),
        system=book.system,
        book=book.slug,
        book_title=book.title,
        printed_start=unit["pdf_start"] - book.page_offset,
        printed_end=unit["pdf_end"] - book.page_offset,
    )


def rules_snapshot(book: Book) -> set[str]:
    return {p.name for p in (book.vault / "rules").glob("*.md")}


def run_unit(book: Book, unit: dict, pages: dict[int, str], model: str, dry_run: bool) -> dict:
    chunk_path = slice_unit(book, pages, unit)
    prompt = build_prompt(book, unit, chunk_path)

    if dry_run:
        print(f"--- {unit['id']} prompt ---\n{prompt}\n--- end prompt ---")
        return {"status": "dry-run"}

    book.logs.mkdir(exist_ok=True)
    log_path = book.logs / f"{unit['id']}.log"
    before = rules_snapshot(book)
    started = time.monotonic()
    print(f"[{unit['id']}] {unit['title']} -> claude -p ({model})...", flush=True)

    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", model, "--permission-mode", "acceptEdits"],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

    elapsed = int(time.monotonic() - started)
    added = sorted(rules_snapshot(book) - before)
    print(f"[{unit['id']}] exit={result.returncode} in {elapsed}s, "
          f"{len(added)} new notes (log: {log_path.relative_to(ROOT)})")
    for name in added:
        print(f"    + {name}")

    if result.returncode != 0:
        return {"status": "failed", "exit": result.returncode}

    validator = subprocess.run(
        [sys.executable, str(ROOT / "validate_vault.py"), "--fix", "--system", book.system],
        cwd=ROOT,
    )
    if validator.returncode != 0:
        return {"status": "invalid", "notes_added": len(added)}
    if not added:
        print(f"[{unit['id']}] WARNING: no new notes -- check the log before trusting this unit")
    return {"status": "done", "notes_added": len(added), "seconds": elapsed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("units", nargs="*", help="unit ids or prefixes (default: all pending)")
    parser.add_argument("--system", default=None, help="system under systems/")
    parser.add_argument("--book", default=None, help="book slug under books/<system>/")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-run units already done")
    parser.add_argument("--status", action="store_true", help="show unit states and exit")
    args = parser.parse_args()

    system = sole_or_default(SYSTEMS, args.system, "system")
    book = Book(system, sole_or_default(BOOKS / system, args.book, "book"))
    state = book.load_state()

    if args.status:
        print(f"{book.system} / {book.slug} ({book.title})")
        for unit in book.chapters:
            info = state.get(unit["id"], {})
            print(f"  {unit['id']:>4}  {info.get('status', 'pending'):>8}  {unit['title']}")
        return 0

    if args.units:
        selected = [u for u in book.chapters if any(u["id"].startswith(p) for p in args.units)]
        if not selected:
            sys.exit(f"No units match: {args.units}")
    else:
        selected = book.chapters

    pages = load_pages(book)
    for unit in selected:
        if not args.force and state.get(unit["id"], {}).get("status") == "done":
            print(f"[{unit['id']}] already done, skipping (--force to re-run)")
            continue
        outcome = run_unit(book, unit, pages, args.model, args.dry_run)
        if outcome["status"] != "dry-run":
            outcome["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state[unit["id"]] = outcome
            book.save_state(state)
        if outcome["status"] in ("failed", "invalid"):
            print(f"[{unit['id']}] stopping run: {outcome['status']}")
            return 1

    done = sum(1 for u in book.chapters if state.get(u["id"], {}).get("status") == "done")
    print(f"\n{done}/{len(book.chapters)} units done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
