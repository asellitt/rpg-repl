#!/usr/bin/env python3
"""
Rules-lawyer agent over the vault, powered by a local Ollama model with
tool calling.

Usage:
    python3 ask.py "how does raising the stakes work?"
    python3 ask.py                     # interactive REPL
    python3 ask.py --model llama3.1:8b "what is a skill test?"

Requires: Ollama running locally (https://ollama.com), a tool-calling
model pulled (default qwen2.5:14b), and the python client:
    pip install ollama
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import ollama
except ImportError:
    sys.exit("The ollama python client is not installed. Run: pip install ollama")

ROOT = Path(__file__).resolve().parent
VAULT = ROOT / "vault"
MAX_TURNS = 12
MAX_SEARCH_LINES = 25

USE_COLOR = sys.stdout.isatty() and sys.stdin.isatty()
CYAN = "\033[36m" if USE_COLOR else ""
DIM = "\033[2m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


LAST_LINKS: list[str] = []
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def _render_table(block: list[str]) -> list[str]:
    """Align a markdown pipe table into padded columns with box-drawing
    separators. Falls back to the raw lines if the block isn't a table."""
    data: list[list[str]] = []
    has_header = False
    for line in block:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-+:?", c) for c in cells):
            if len(data) == 1:
                has_header = True
            continue
        data.append(cells)
    if not data:
        return block
    ncols = max(len(row) for row in data)
    for row in data:
        row.extend([""] * (ncols - len(row)))
    widths = [max(visible_len(row[i]) for row in data) for i in range(ncols)]

    def fmt(row: list[str], bold: bool) -> str:
        cells = [
            (BOLD if bold else "") + cell + (RESET if bold else "")
            + " " * (widths[i] - visible_len(cell))
            for i, cell in enumerate(row)
        ]
        return f" {DIM}│{RESET} ".join(cells).rstrip()

    out = [fmt(data[0], has_header)]
    if has_header:
        out.append(f"{DIM}" + "─┼─".join("─" * w for w in widths) + RESET)
    out.extend(fmt(row, False) for row in data[1:])
    return out


def _format_tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    block: list[str] = []

    def flush() -> None:
        if len(block) >= 2:
            out.extend(_render_table(block))
        else:
            out.extend(block)
        block.clear()

    for line in lines:
        if line.lstrip().startswith("|"):
            block.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


def render_markdown(text: str) -> str:
    """Light terminal rendering of the answer: wikilinks, bold, headings,
    inline code. No dependency; plain passthrough when colors are off.
    Wikilinks are numbered into LAST_LINKS so /N can open them."""
    if not USE_COLOR:
        return text
    LAST_LINKS.clear()
    text = text.replace("\\|", "|")  # Obsidian's escaped pipe in tables

    def link(match: re.Match) -> str:
        name = match.group(1).strip()
        display = (match.group(2) or name).strip()
        if name not in LAST_LINKS:
            LAST_LINKS.append(name)
        return f"{CYAN}{display}{RESET}{DIM}[{LAST_LINKS.index(name) + 1}]{RESET}"

    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", link, text)
    text = re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)
    text = re.sub(r"`([^`\n]+)`", f"{YELLOW}\\1{RESET}", text)
    text = re.sub(r"^(#{1,4} .*)$", f"{BOLD}\\1{RESET}", text, flags=re.MULTILINE)
    return _format_tables(text)


CITATION_NUDGE = (
    "Your answer omitted citations. Repeat it, writing every mentioned "
    "concept as a [[wikilink]] with its exact note name and citing "
    "(Note Name, p. X) for every rule, using pages from note frontmatter."
)


BOOK_NAMES: dict[str, str] = {}


def book_name(slug: str) -> str:
    """Display name for a source slug, from the _sources note's H1."""
    if slug not in BOOK_NAMES:
        BOOK_NAMES[slug] = slug
        source_note = VAULT / "_sources" / f"{slug}.md"
        if source_note.exists():
            for line in source_note.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    BOOK_NAMES[slug] = line[2:].strip()
                    break
    return BOOK_NAMES[slug]


def print_sources(content: str) -> None:
    """Print a footer citing the book and pages of every note the answer
    linked, straight from vault frontmatter (immune to model
    hallucination)."""
    cited: list[Path] = []
    for match in re.finditer(r"\[\[([^\]|#]+)", content.replace("\\|", "|")):
        path = NAME_MAP.get(match.group(1).strip().lower())
        if path is not None and path not in cited:
            cited.append(path)
    cites = []
    for path in cited:
        text = path.read_text(encoding="utf-8")
        pages = re.search(r'^pages:\s*"?([^"\n]+?)"?\s*$', text, re.MULTILINE)
        source = re.search(r"^source:\s*(\S+)\s*$", text, re.MULTILINE)
        if pages:
            book = f"{book_name(source.group(1))} " if source else ""
            cites.append(f"{path.stem} ({book}p. {pages.group(1)})")
    if cites:
        print(f"{DIM}Sources: {'; '.join(cites)}{RESET}")


def open_note(ref: str) -> None:
    """Print a note inline, by /N link number or by name/alias."""
    if ref.isdigit():
        index = int(ref) - 1
        if not 0 <= index < len(LAST_LINKS):
            print(f"No link [{ref}] in the last output.")
            return
        ref = LAST_LINKS[index]
    text = read_note(ref)
    text = re.sub(r"\A---\n.*?\n---\n+", "", text, flags=re.DOTALL)
    print(render_markdown(text))

SYSTEM_PROMPT_TEMPLATE = """\
You are a rules expert for the {rpg} tabletop RPG, answering from an
Obsidian vault of rules notes. The vault is the only source of truth —
never answer from general knowledge.

Vault layout:
- rules/: one note per rule/concept. Frontmatter carries aliases,
  tags, and the printed book pages the note cites.
- _index/: one map-of-content note per book chapter, listing that
  chapter's notes.
- Notes reference each other with [[wikilinks]].

Workflow for every question:
1. search_vault for the key terms (note names and aliases are indexed;
   try synonyms if the first search misses).
2. read_note the most relevant hits in full.
3. Follow [[wikilinks]] to related notes when the answer spans concepts.

Answering rules:
- Quote mechanics exactly — numbers, dice, DCs, costs. Never approximate.
- Cite every claim: note name plus the printed pages from its
  frontmatter, e.g. (Raise the Stakes, p. 8-9).
- Whenever your answer mentions a concept that has a vault note, write
  it as a wikilink with the exact note name: [[Plot Die]], or
  [[Raise the Stakes|raising the stakes]] when the sentence needs a
  different surface form. The reader's terminal turns these into
  numbered references they can open.
- End every answer with a "Related:" line listing the wikilinks of the
  notes you used or that the reader would sensibly open next.
- If the vault doesn't cover the question, say exactly that — never
  guess or fill gaps from outside knowledge.
- Distinguish rules-as-written (quoted) from your interpretation, and
  label the interpretation as such.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(rpg="cosmere")
RPG_NAME = "rules"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "Case-insensitive search over all vault notes: note names, aliases, and body text. Returns matching lines as 'path: line'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "word or phrase to search for"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "Read one note in full, by note name or alias (case-insensitive), e.g. 'Plot Die' or 'Stakes'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "note name or alias"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_index",
            "description": "List the chapter index notes (maps of content), or read one by chapter name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter": {"type": "string", "description": "optional chapter name to read in full"},
                },
                "required": [],
            },
        },
    },
]


def build_name_map() -> dict[str, Path]:
    """Map lowercase note stems and aliases to note paths."""
    names: dict[str, Path] = {}
    alias_re = re.compile(r"^aliases:\s*\[(.*)\]", re.MULTILINE)
    for note in list(VAULT.glob("rules/*.md")) + list(VAULT.glob("_sources/*.md")):
        names[note.stem.lower()] = note
        m = alias_re.search(note.read_text(encoding="utf-8"))
        if m:
            for alias in m.group(1).split(","):
                alias = alias.strip().strip("\"'")
                if alias:
                    names.setdefault(alias.lower(), note)
    return names


def search_vault(query: str) -> str:
    query_lower = query.lower()
    hits: list[str] = []
    name_matches: list[Path] = []
    for name, path in sorted(NAME_MAP.items(), key=lambda kv: str(kv[1])):
        if query_lower in name and path not in name_matches:
            name_matches.append(path)
            hits.append(f"NOTE MATCH: {path.relative_to(VAULT)}")

    truncated = False
    for note in sorted(VAULT.rglob("*.md")):
        if note.parent.name == "_meta":
            continue
        for line in note.read_text(encoding="utf-8").splitlines():
            if query_lower in line.lower():
                if len(hits) < MAX_SEARCH_LINES:
                    hits.append(f"{note.relative_to(VAULT)}: {line.strip()}")
                else:
                    truncated = True
    if truncated:
        hits.append("... (more hits truncated; refine the query)")
    if not hits:
        return f"No matches for '{query}'. Try a synonym or a broader term."
    return "\n".join(hits)


def read_note(name: str) -> str:
    path = NAME_MAP.get(name.strip().lower())
    if path is None:
        close = [n for n in NAME_MAP if name.strip().lower() in n][:10]
        suggestion = f" Close names: {', '.join(sorted(set(close)))}" if close else ""
        return f"No note named '{name}'.{suggestion}"
    return path.read_text(encoding="utf-8")


def list_index(chapter: str = "") -> str:
    if chapter:
        for moc in VAULT.glob("_index/*.md"):
            if chapter.strip().lower() in moc.stem.lower():
                return moc.read_text(encoding="utf-8")
        return f"No index note matching '{chapter}'."
    return "\n".join(sorted(p.stem for p in VAULT.glob("_index/*.md")))


def dispatch(name: str, arguments: dict) -> str:
    if name == "search_vault":
        return search_vault(str(arguments.get("query", "")))
    if name == "read_note":
        return read_note(str(arguments.get("name", "")))
    if name == "list_index":
        return list_index(str(arguments.get("chapter", "")))
    return f"Unknown tool: {name}"


def answer(question: str, model: str, messages: list | None = None, ctx: int = 8192) -> None:
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.append({"role": "user", "content": question})
    turn_start = len(messages)
    nudged = False
    for _ in range(MAX_TURNS):
        response = ollama.chat(
            model=model,
            messages=messages,
            tools=TOOLS,
            options={"num_ctx": ctx},
            keep_alive="30m",
        )
        message = response["message"]
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            content = message.get("content", "").strip()
            uncited = "[[" not in content and not re.search(r"\bp(?:\.|age)\s*\d", content)
            if uncited and not nudged:
                nudged = True
                messages.append({"role": "user", "content": CITATION_NUDGE})
                continue
            print(render_markdown(content))
            print_sources(content)
            # Keep only [user question, final answer] in history: the tool
            # dumps are the bulk of the context and follow-ups re-search.
            del messages[turn_start:-1]
            return
        for call in tool_calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            result = dispatch(fn["name"], args)
            print(f"{DIM}  [{fn['name']}({args})]{RESET}", file=sys.stderr)
            messages.append({"role": "tool", "name": fn["name"], "content": result})
    print("Stopped: too many tool-call rounds without a final answer.")


def main() -> None:
    global NAME_MAP, VAULT, RPG_NAME, SYSTEM_PROMPT
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", help="the rules question (omit for a REPL)")
    parser.add_argument("--model", default="qwen2.5:14b")
    parser.add_argument("--ctx", type=int, default=8192,
                        help="context window tokens (lower = faster/less RAM)")
    parser.add_argument("--vault", type=Path, default=VAULT,
                        help="vault directory (one per RPG system)")
    args = parser.parse_args()

    VAULT = args.vault.resolve()
    if not VAULT.is_dir():
        sys.exit(f"No vault at {VAULT}")
    rpg_file = VAULT / "_meta" / "rpg.txt"
    RPG_NAME = rpg_file.read_text(encoding="utf-8").strip() if rpg_file.exists() else "rules"
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(rpg=RPG_NAME)
    NAME_MAP = build_name_map()
    print(f"({RPG_NAME}: {len(NAME_MAP)} note names/aliases indexed)", file=sys.stderr)

    if args.question:
        answer(" ".join(args.question), args.model, ctx=args.ctx)
        return
    print("Conversational REPL: follow-ups keep context. "
          "/2 or /open NAME shows a linked note, /clear resets, q quits.", file=sys.stderr)
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]
    while True:
        try:
            question = input(f"\n{CYAN}{RPG_NAME}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(RESET)
            return
        finally:
            print(RESET, end="", flush=True)
        if question in ("q", "quit", "exit", ""):
            return
        if question == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("(context cleared)", file=sys.stderr)
            continue
        if question.startswith("/open "):
            open_note(question[6:].strip())
            continue
        if re.fullmatch(r"/\d+", question):
            open_note(question[1:])
            continue
        answer(question, args.model, messages, ctx=args.ctx)


NAME_MAP: dict[str, Path] = {}

if __name__ == "__main__":
    main()
