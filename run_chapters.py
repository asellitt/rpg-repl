#!/usr/bin/env python3
"""
Drive headless Claude Code over the chapter units in chapters.json,
one at a time, writing rules notes into vault/.

Usage:
    python3 run_chapters.py                # run all pending units, in order
    python3 run_chapters.py 01 02          # run specific units
    python3 run_chapters.py 05             # prefix match: all 05* units
    python3 run_chapters.py --dry-run 01   # slice + print the prompt, no claude
    python3 run_chapters.py --force 01     # re-run a unit already marked done
    python3 run_chapters.py --model opus   # override the worker model
    python3 run_chapters.py --status       # show unit states and exit

Each unit: slice its pdf-page range from output/stormlight.txt into
work/, invoke `claude -p` with the prompt from prompt_template.md
(permission mode acceptEdits, so the worker can write vault files but
nothing else is auto-approved), capture the transcript into logs/, then
run validate_vault.py. A validator hard failure stops the run; fix the
notes (or re-run the unit with --force) before continuing.

State lives in work/state.json -- completed units are skipped, so the
script is resumable and safe to re-run after an interruption.
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
OUTPUT_TXT = ROOT / "output" / "stormlight.txt"
CHAPTERS_JSON = ROOT / "chapters.json"
PROMPT_TEMPLATE = ROOT / "prompt_template.md"
WORK = ROOT / "work"
LOGS = ROOT / "logs"
STATE_FILE = WORK / "state.json"
RULES_DIR = ROOT / "vault" / "rules"
PAGE_OFFSET = 4  # pdf_page = printed_page + 4


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    WORK.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def load_pages() -> dict[int, str]:
    pages: dict[int, str] = {}
    current = None
    buf: list[str] = []
    for line in OUTPUT_TXT.read_text(encoding="utf-8").splitlines():
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


def slice_unit(pages: dict[int, str], unit: dict) -> Path:
    WORK.mkdir(exist_ok=True)
    chunk = "\n\n".join(
        pages[p] for p in range(unit["pdf_start"], unit["pdf_end"] + 1) if p in pages
    )
    path = WORK / f"{unit['id']}.txt"
    path.write_text(chunk, encoding="utf-8")
    return path


def build_prompt(unit: dict, chunk_path: Path) -> str:
    return PROMPT_TEMPLATE.read_text(encoding="utf-8").format(
        title=unit["title"],
        moc=unit["moc"],
        chunk=chunk_path.relative_to(ROOT),
        printed_start=unit["pdf_start"] - PAGE_OFFSET,
        printed_end=unit["pdf_end"] - PAGE_OFFSET,
    )


def rules_snapshot() -> set[str]:
    return {p.name for p in RULES_DIR.glob("*.md")}


def run_unit(unit: dict, pages: dict[int, str], model: str, dry_run: bool) -> dict:
    chunk_path = slice_unit(pages, unit)
    prompt = build_prompt(unit, chunk_path)

    if dry_run:
        print(f"--- {unit['id']} prompt ---\n{prompt}\n--- end prompt ---")
        return {"status": "dry-run"}

    LOGS.mkdir(exist_ok=True)
    log_path = LOGS / f"{unit['id']}.log"
    before = rules_snapshot()
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
    added = sorted(rules_snapshot() - before)
    print(f"[{unit['id']}] exit={result.returncode} in {elapsed}s, "
          f"{len(added)} new notes (log: {log_path.relative_to(ROOT)})")
    for name in added:
        print(f"    + {name}")

    if result.returncode != 0:
        return {"status": "failed", "exit": result.returncode}

    validator = subprocess.run(
        [sys.executable, str(ROOT / "validate_vault.py"), "--fix"], cwd=ROOT
    )
    if validator.returncode != 0:
        return {"status": "invalid", "notes_added": len(added)}
    if not added:
        print(f"[{unit['id']}] WARNING: no new notes -- check the log before trusting this unit")
    return {"status": "done", "notes_added": len(added), "seconds": elapsed}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("units", nargs="*", help="unit ids or prefixes (default: all pending)")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-run units already done")
    parser.add_argument("--status", action="store_true", help="show unit states and exit")
    args = parser.parse_args()

    chapters = json.loads(CHAPTERS_JSON.read_text(encoding="utf-8"))
    state = load_state()

    if args.status:
        for unit in chapters:
            info = state.get(unit["id"], {})
            print(f"  {unit['id']:>4}  {info.get('status', 'pending'):>8}  {unit['title']}")
        return 0

    if args.units:
        selected = [u for u in chapters if any(u["id"].startswith(p) for p in args.units)]
        if not selected:
            sys.exit(f"No units match: {args.units}")
    else:
        selected = chapters

    pages = load_pages()
    for unit in selected:
        if not args.force and state.get(unit["id"], {}).get("status") == "done":
            print(f"[{unit['id']}] already done, skipping (--force to re-run)")
            continue
        outcome = run_unit(unit, pages, args.model, args.dry_run)
        if outcome["status"] != "dry-run":
            outcome["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            state[unit["id"]] = outcome
            save_state(state)
        if outcome["status"] in ("failed", "invalid"):
            print(f"[{unit['id']}] stopping run: {outcome['status']}")
            return 1

    done = sum(1 for u in chapters if state.get(u["id"], {}).get("status") == "done")
    print(f"\n{done}/{len(chapters)} units done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
