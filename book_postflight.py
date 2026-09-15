#!/usr/bin/env python3
"""
Post-book sanity report: run after all of a book's units are done.

Reports (writes books/<system>/<book>/work/postflight_report.md):
  - validator result (hard failures, unresolved wikilinks with referrers)
  - MOC coverage (notes reachable from no _index note)
  - merge stats for the book (notes created vs merged-into)
  - unit state summary

Usage:
    python3 book_postflight.py --system cosmere --book mistborn-handbook
    python3 book_postflight.py --book <slug> --reconcile   # + headless fixes
    python3 book_postflight.py --book <slug> --reconcile --model opus

Report-only is read-only (safe any time). --reconcile launches a headless
claude worker that applies the standard fixes (hub notes, aliases,
unlinks, defined-elsewhere bookkeeping) per prompt_reconcile.md — don't
use it while another run is writing to the same system's vault.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEMS = ROOT / "systems"
BOOKS = ROOT / "books"
PROMPT = ROOT / "prompt_reconcile.md"
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def gather(vault: Path, book_slug: str) -> dict:
    link_targets: set[str] = set()
    moc_linked: set[str] = set()
    referrers: dict[str, set[str]] = {}
    created, merged = [], []

    for moc in vault.glob("_index/*.md"):
        link_targets.add(moc.stem.lower())
        for target in WIKILINK_RE.findall(moc.read_text(encoding="utf-8").replace("\\|", "|")):
            moc_linked.add(target.strip().lower())

    notes = sorted(vault.glob("notes/*.md")) + sorted(vault.glob("_sources/*.md"))
    alias_re = re.compile(r"^aliases:\s*\[(.*)\]", re.MULTILINE)
    for note in notes:
        text = note.read_text(encoding="utf-8")
        link_targets.add(note.stem.lower())
        m = alias_re.search(text)
        if m:
            for alias in m.group(1).split(","):
                alias = alias.strip().strip("\"'")
                if alias:
                    link_targets.add(alias.lower())
        sources = re.search(r"^sources:\s*\[(.*)\]\s*$", text, re.MULTILINE)
        if sources and note.parent.name == "notes":
            entries = re.findall(r'"([^"]+)"', sources.group(1))
            slugs = [e.partition(":")[0].strip() for e in entries]
            if book_slug in slugs:
                (created if len(slugs) == 1 else merged).append(note.stem)

    for note in sorted(vault.rglob("*.md")):
        if note.parent.name == "_meta":
            continue
        rel = str(note.relative_to(vault))
        for target in WIKILINK_RE.findall(note.read_text(encoding="utf-8").replace("\\|", "|")):
            target = target.strip()
            if "\n" not in target:
                referrers.setdefault(target, set()).add(rel)

    unresolved = {t: sorted(srcs) for t, srcs in referrers.items()
                  if t.lower() not in link_targets}
    uncovered = [n.stem for n in vault.glob("notes/*.md")
                 if n.stem.lower() not in moc_linked]
    return {"unresolved": unresolved, "uncovered": sorted(uncovered),
            "created": created, "merged": merged}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--book", required=True)
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()

    vault = SYSTEMS / args.system
    book_dir = BOOKS / args.system / args.book
    if not vault.is_dir() or not book_dir.is_dir():
        sys.exit(f"Missing {vault} or {book_dir}")
    config = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))

    state_file = book_dir / "work" / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    chapters = json.loads((book_dir / "chapters.json").read_text(encoding="utf-8"))
    pending = [u["id"] for u in chapters if state.get(u["id"], {}).get("status") != "done"]

    validator = subprocess.run(
        [sys.executable, str(ROOT / "validate_vault.py"), "--system", args.system],
        cwd=ROOT, capture_output=True, text=True,
    )
    facts = gather(vault, args.book)

    lines = [f"# Postflight: {config['title']} ({args.system}/{args.book})", ""]
    lines.append(f"Units: {len(chapters) - len(pending)}/{len(chapters)} done"
                 + (f" — PENDING: {pending}" if pending else ""))
    lines.append(f"Notes citing this book: {len(facts['created']) + len(facts['merged'])} "
                 f"({len(facts['created'])} created, {len(facts['merged'])} merged into)")
    lines.append(f"Validator exit {validator.returncode}: "
                 f"{validator.stdout.strip().splitlines()[0] if validator.stdout else ''}")
    if validator.returncode != 0:
        lines.append("\n## Validator hard failures\n")
        lines.append(validator.stdout)

    lines.append("\n## Unresolved wikilinks\n")
    if facts["unresolved"]:
        for target in sorted(facts["unresolved"]):
            lines.append(f"- [[{target}]] <- {', '.join(facts['unresolved'][target])}")
    else:
        lines.append("(none)")

    lines.append("\n## Notes in no MOC\n")
    lines.append("\n".join(f"- {n}" for n in facts["uncovered"]) or "(none)")

    report_path = book_dir / "work" / "postflight_report.md"
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReport written to {report_path}")

    if pending:
        print("Units still pending -- finish the run before reconciling.")
        return 1
    if not args.reconcile:
        return 0

    prompt = PROMPT.read_text(encoding="utf-8").format(
        system=args.system,
        book=args.book,
        book_title=config["title"],
        report=report_path.relative_to(ROOT),
        vault=vault.relative_to(ROOT),
    )
    log_path = book_dir / "logs" / "postflight.log"
    log_path.parent.mkdir(exist_ok=True)
    print(f"Reconciling with claude -p ({args.model})... (log: {log_path.relative_to(ROOT)})",
          flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", args.model, "--permission-mode", "acceptEdits"],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
    print(f"Reconcile worker exit {result.returncode}")
    final = subprocess.run(
        [sys.executable, str(ROOT / "validate_vault.py"), "--fix", "--system", args.system],
        cwd=ROOT,
    )
    return result.returncode or final.returncode


if __name__ == "__main__":
    sys.exit(main())
