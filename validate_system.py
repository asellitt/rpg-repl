#!/usr/bin/env python3
"""
Validate a system's notes: frontmatter completeness, H1/filename agreement,
and wikilink resolution.

Usage:
    python3 validate_system.py                    # validate the default system
    python3 validate_system.py --system cosmere   # validate one system
    python3 validate_system.py --links            # also print every unresolved link
    python3 validate_system.py --fix              # first unwrap line-wrapped wikilinks

Exit code 1 on hard failures (bad frontmatter, H1 mismatch), 0 otherwise.
Unresolved wikilinks are informational -- forward links are expected
until all chapters are processed.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEMS = ROOT / "systems"
SYSTEM_DIR = SYSTEMS / "cosmere"
REQUIRED_KEYS = ("aliases", "tags", "sources")

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
WRAPPED_LINK_RE = re.compile(r"\[\[([^\]\n]*)\n[ \t]*([^\]\n]*)\]\]")


def unwrap_wikilinks(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = WRAPPED_LINK_RE.sub(
            lambda m: f"[[{m.group(1).rstrip()} {m.group(2).lstrip()}]]", text
        )
    return text


def parse_frontmatter(text: str) -> dict | None:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def parse_aliases(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        return [a.strip().strip("\"'") for a in value[1:-1].split(",") if a.strip()]
    return []


def main() -> int:
    global SYSTEM_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--links", action="store_true", help="print every unresolved wikilink")
    parser.add_argument("--fix", action="store_true", help="unwrap line-wrapped wikilinks first")
    parser.add_argument("--system", default="cosmere", help="system directory under systems/")
    args = parser.parse_args()

    SYSTEM_DIR = SYSTEMS / args.system
    if not SYSTEM_DIR.is_dir():
        print(f"No system at {SYSTEM_DIR}")
        return 1

    if args.fix:
        for note in sorted(SYSTEM_DIR.rglob("*.md")):
            if note.parent.name == "_meta":
                continue
            text = note.read_text(encoding="utf-8")
            fixed = unwrap_wikilinks(text)
            if (note.parent.name == "notes"
                    and fixed.startswith("---\n")
                    and not re.search(r"^aliases:", fixed, re.MULTILINE)):
                fixed = fixed.replace("---\n", "---\naliases: []\n", 1)
            if fixed != text:
                note.write_text(fixed, encoding="utf-8")
                print(f"fixed: {note.relative_to(ROOT)}")

    failures: list[str] = []
    link_targets: set[str] = set()
    all_links: dict[str, list[str]] = {}

    # Obsidian's click-to-create drops empty notes at the top-level dirs;
    # notes only belong under a system's notes/, _index/, or _sources/.
    for stray in list(SYSTEMS.glob("*.md")) + list(SYSTEM_DIR.glob("*.md")):
        failures.append(f"{stray.relative_to(ROOT)}: stray note outside notes/_index/_sources "
                        f"(Obsidian click-to-create?)")

    for moc in SYSTEM_DIR.glob("_index/*.md"):
        link_targets.add(moc.stem.lower())

    notes = sorted(SYSTEM_DIR.glob("notes/*.md")) + sorted(SYSTEM_DIR.glob("_sources/*.md"))
    for note in notes:
        text = note.read_text(encoding="utf-8")
        rel = note.relative_to(ROOT)
        link_targets.add(note.stem.lower())

        fm = parse_frontmatter(text)
        if fm is None:
            failures.append(f"{rel}: no frontmatter block")
            continue
        if note.parent.name == "notes":
            for key in REQUIRED_KEYS:
                if key not in fm or not fm[key]:
                    failures.append(f"{rel}: missing frontmatter key '{key}'")
        for alias in parse_aliases(fm.get("aliases", "")):
            link_targets.add(alias.lower())

        h1s = [l[2:].strip() for l in text.splitlines() if l.startswith("# ")]
        if not h1s:
            failures.append(f"{rel}: no H1")
        elif note.parent.name == "notes" and h1s[0] != note.stem:
            failures.append(f"{rel}: H1 '{h1s[0]}' != filename '{note.stem}'")

    for note in sorted(SYSTEM_DIR.rglob("*.md")):
        if note.parent.name == "_meta":  # conventions contain template examples
            continue
        rel = str(note.relative_to(ROOT))
        # Obsidian escapes pipes as \| inside tables; normalize before parsing.
        text = note.read_text(encoding="utf-8").replace("\\|", "|")
        for target in WIKILINK_RE.findall(text):
            if "\n" in target:
                failures.append(f"{rel}: line-wrapped wikilink [[{target.splitlines()[0]}...]]")
                continue
            all_links.setdefault(target.strip(), []).append(rel)

    unresolved = {t: srcs for t, srcs in all_links.items() if t.lower() not in link_targets}

    moc_linked: set[str] = set()
    for moc in SYSTEM_DIR.glob("_index/*.md"):
        for target in WIKILINK_RE.findall(moc.read_text(encoding="utf-8").replace("\\|", "|")):
            moc_linked.add(target.strip().lower())
    uncovered = sorted(
        n.stem for n in SYSTEM_DIR.glob("notes/*.md") if n.stem.lower() not in moc_linked
    )

    note_count = len(list(SYSTEM_DIR.glob("notes/*.md")))
    print(f"{note_count} notes, {len(all_links)} distinct wikilink targets, "
          f"{len(unresolved)} unresolved, {len(uncovered)} in no MOC")

    if uncovered:
        print("\nNotes in no MOC (add to a chapter MOC in the postflight pass):")
        for name in uncovered[:20]:
            print(f"  {name}")
        if len(uncovered) > 20:
            print(f"  ... (+{len(uncovered) - 20} more)")

    if unresolved:
        print("\nUnresolved wikilinks (forward links are fine until all chapters land):")
        for target in sorted(unresolved):
            srcs = unresolved[target]
            if args.links:
                print(f"  [[{target}]] <- {', '.join(sorted(set(srcs)))}")
            else:
                print(f"  [[{target}]] ({len(srcs)} reference(s))")

    if failures:
        print("\nHARD FAILURES:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
