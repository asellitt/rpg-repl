#!/usr/bin/env python3
"""
Rules-lawyer agent over one RPG system's notes, powered by a local Ollama model with
tool calling.

Usage:
    python3 ask.py "how does raising the stakes work?"
    python3 ask.py                     # interactive REPL
    python3 ask.py --fast "what is a skill test?"   # small model, quicker
    python3 ask.py --model llama3.1:8b "what is a skill test?"

Requires: Ollama running locally (https://ollama.com), a tool-calling
model pulled (default qwen2.5:14b; --fast uses qwen2.5:7b), and the
python client:
    pip install ollama
"""

import argparse
import difflib
import math
import re
import sys
import threading
import time
from pathlib import Path

try:
    import ollama
except ImportError:
    sys.exit("The ollama python client is not installed. Run: pip install ollama")

ROOT = Path(__file__).resolve().parent
SYSTEMS = ROOT / "systems"
SYSTEM_DIR = SYSTEMS / "cosmere"
MAX_TURNS = 12
FAST_MODEL = "qwen2.5:7b"
SMART_MODEL = "qwen2.5:14b"

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

READ_FIRST_NUDGE = (
    "You answered without reading any note in full. Search previews are "
    "not evidence: use search_system to find candidates, then read_note "
    "the relevant hits, and answer only from what the notes say. If no "
    "relevant notes exist, reply only that the system doesn't cover this."
)

FABRICATED_NUDGE = (
    "Your answer referenced notes that do not exist in the system: {names}. "
    "Drop every claim that came from them and keep only what the notes you "
    "actually read support. If nothing remains, reply only that the system "
    "doesn't cover this."
)

CITED_UNREAD_NUDGE = (
    "Your answer cites pages from notes you have not read: {names}. "
    "read_note every note you cite, or drop those citations and the claims "
    "resting on them. If nothing remains, reply only that the system "
    "doesn't cover this."
)


BOOK_NAMES: dict[str, str] = {}


def book_name(slug: str) -> str:
    """Display name for a source slug, from the _sources note's H1."""
    if slug not in BOOK_NAMES:
        BOOK_NAMES[slug] = slug
        source_note = SYSTEM_DIR / "_sources" / f"{slug}.md"
        if source_note.exists():
            for line in source_note.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    BOOK_NAMES[slug] = line[2:].strip()
                    break
    return BOOK_NAMES[slug]


def print_sources(content: str) -> None:
    """Print a footer citing the book and pages of every note the answer
    linked, straight from note frontmatter (immune to model
    hallucination)."""
    cited: list[Path] = []
    for match in re.finditer(r"\[\[([^\]|#]+)", content.replace("\\|", "|")):
        path = NAME_MAP.get(match.group(1).strip().lower())
        if path is not None and path not in cited:
            cited.append(path)
    cites = []
    for path in cited:
        text = path.read_text(encoding="utf-8")
        sources = re.search(r"^sources:\s*\[(.*)\]\s*$", text, re.MULTILINE)
        if not sources:
            continue
        parts = []
        for entry in re.findall(r'"([^"]+)"', sources.group(1)):
            slug, _, pages = entry.partition(":")
            parts.append(f"{book_name(slug.strip())} p. {pages.strip()}")
        if parts:
            cites.append(f"{path.stem} ({'; '.join(parts)})")
    if cites:
        print(f"{DIM}Sources: {'; '.join(cites)}{RESET}")


def fabricated_links(content: str) -> list[str]:
    """Wikilinks in the answer that resolve to no note or alias in the system."""
    fakes: list[str] = []
    for match in re.finditer(r"\[\[([^\]|#]+)", content.replace("\\|", "|")):
        name = match.group(1).strip()
        if NAME_MAP.get(name.lower()) is None and name not in fakes:
            fakes.append(name)
    return fakes


CITE_RE = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*),\s*p(?:\.|age)\s*\d")


def cited_unread(content: str, read_paths: set[Path]) -> list[str]:
    """Notes the answer page-cites inline without ever having read them."""
    unread: list[str] = []
    for match in CITE_RE.finditer(content.replace("\\|", "|")):
        name = match.group(1).strip().strip("[]").strip()
        path = NAME_MAP.get(name.lower())
        if path is not None and path not in read_paths and path.stem not in unread:
            unread.append(path.stem)
    return unread


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
You are a rules expert for the {rpg} tabletop RPG. The system's rules
notes are the only source of truth — never answer from general knowledge.
Notes reference each other with [[wikilinks]]; note frontmatter lists
aliases and source books with printed pages.

Workflow: your question arrives already searched, with the top notes
already read. search_system again with better terms if they miss;
read_note anything you cite — previews are never enough. When a note
defers the answer to a linked note, read that note.

Answering:
- Lead with the direct answer, then only the supporting mechanics —
  under 80 words unless exact mechanics need more. Quote mechanics
  exactly: numbers, dice, DCs, costs.
- Write each concept that has a note as a wikilink with its exact note
  name: [[Plot Die]], or [[Plot Die|the plot die]] for a different
  surface form. Cite claims as (note name, printed pages from its
  frontmatter), only from notes you have read.
- End with a "Related:" line of useful wikilinks.
- Ignore given notes that are irrelevant to the question: no mentions,
  no "doesn't cover" disclaimers about things nobody asked.
- If the notes cannot answer the question — including when they never
  mention the thing asked about — reply exactly "The system doesn't
  cover this." and nothing else. Never fill gaps from outside knowledge.
- Label anything that is your interpretation rather than rules-as-written.
"""

SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(rpg="cosmere")
RPG_NAME = "cosmere"


def display_name(system: str) -> str:
    meta = SYSTEMS / system / "_meta" / "system.json"
    if meta.exists():
        try:
            import json
            return json.loads(meta.read_text(encoding="utf-8")).get("display_name", system)
        except Exception:
            pass
    return system


def available_systems() -> list[str]:
    if not SYSTEMS.is_dir():
        return []
    return sorted(
        p.name for p in SYSTEMS.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def load_system(name: str) -> bool:
    """Point the agent at systems/<name>; returns False if it doesn't exist."""
    global SYSTEM_DIR, RPG_NAME, SYSTEM_PROMPT, NAME_MAP
    path = SYSTEMS / name
    if not path.is_dir():
        return False
    SYSTEM_DIR = path
    RPG_NAME = display_name(name)
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(rpg=RPG_NAME)
    NAME_MAP = build_name_map()
    _INDEX_CACHE.pop(path, None)
    BOOK_NAMES.clear()
    print(f"({RPG_NAME} [{name}]: {len(NAME_MAP)} note names/aliases indexed)",
          file=sys.stderr)
    return True

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_system",
            "description": "Case-insensitive search over all of the system's notes: note names, aliases, and body text. Returns notes ranked by relevance, each with one matching line as a preview.",
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
    for note in (list(SYSTEM_DIR.glob("notes/*.md")) + list(SYSTEM_DIR.glob("_index/*.md"))
                 + list(SYSTEM_DIR.glob("_sources/*.md"))):
        names[note.stem.lower()] = note
        m = alias_re.search(note.read_text(encoding="utf-8"))
        if m:
            for alias in m.group(1).split(","):
                alias = alias.strip().strip("\"'")
                if alias:
                    names.setdefault(alias.lower(), note)
    return names


STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "is",
    "are", "what", "how", "does", "do", "can", "with", "my", "your",
}


WORD_RE = re.compile(r"[a-z0-9']+")

_INDEX_CACHE: dict[Path, dict] = {}


def _token_matches(word: str, token: str) -> bool:
    """Prefix match, with a naive plural fold ('tests' finds 'test')."""
    if word.startswith(token):
        return True
    return token.endswith("s") and len(token) > 3 and word.startswith(token[:-1])


def _system_index() -> dict:
    """Per-note word counts, lines, and name/alias words, built once per system."""
    index = _INDEX_CACHE.get(SYSTEM_DIR)
    if index is not None:
        return index
    notes: dict[Path, tuple[dict[str, int], list[str]]] = {}
    for note in sorted(SYSTEM_DIR.rglob("*.md")):
        if note.parent.name == "_meta":
            continue
        text = note.read_text(encoding="utf-8")
        counts: dict[str, int] = {}
        for word in WORD_RE.findall(text.lower()):
            counts[word] = counts.get(word, 0) + 1
        notes[note] = (counts, text.splitlines())
    stem_words: dict[Path, set[str]] = {}
    alias_words: dict[Path, set[str]] = {}
    for name, path in NAME_MAP.items():
        words = set(WORD_RE.findall(name))
        target = stem_words if name == path.stem.lower() else alias_words
        target.setdefault(path, set()).update(words)
    vocab: set[str] = set()
    for counts, _ in notes.values():
        vocab.update(counts)
    for words in list(stem_words.values()) + list(alias_words.values()):
        vocab.update(words)
    index = {"notes": notes, "stem": stem_words, "alias": alias_words,
             "vocab": sorted(vocab)}
    _INDEX_CACHE[SYSTEM_DIR] = index
    return index


def search_system(query: str) -> str:
    query_lower = query.lower().strip()
    tokens = [
        t for t in WORD_RE.findall(query_lower) if t not in STOPWORDS
    ] or [query_lower]
    index = _system_index()
    notes = index["notes"]
    n_docs = len(notes) or 1

    # A word no note contains (a typo, a British spelling) would silently
    # drop out and leave the query's generic words to rank alone — correct
    # it to the closest indexed word and say so in the results.
    corrections: list[str] = []
    for i, t in enumerate(tokens):
        if any(_token_matches(w, t) for w in index["vocab"]):
            continue
        close = difflib.get_close_matches(t, index["vocab"], n=1, cutoff=0.8)
        if close:
            corrections.append(f"(no hits for '{t}'; searching '{close[0]}' instead)")
            tokens[i] = close[0]

    # Rarer terms count for more: 'iron' should outweigh 'power'.
    idf = {}
    for t in tokens:
        df = sum(
            1 for counts, _ in notes.values()
            if any(_token_matches(w, t) for w in counts)
        )
        idf[t] = math.log(1 + n_docs / (1 + df))

    scored: list[tuple[float, Path, list[str]]] = []
    for note, (counts, lines) in notes.items():
        stem = index["stem"].get(note, set())
        alias = index["alias"].get(note, set())
        score = 0.0
        matched = 0
        for t in tokens:
            hit = False
            if any(_token_matches(w, t) for w in stem):
                score += 4.0 * idf[t]
                hit = True
            elif any(_token_matches(w, t) for w in alias):
                score += 2.0 * idf[t]
                hit = True
            tf = sum(c for w, c in counts.items() if _token_matches(w, t))
            if tf:
                score += idf[t] * (1.0 + math.log(tf))
                hit = True
            if hit:
                matched += 1
        if not matched:
            continue
        if query_lower in note.stem.lower():
            score += 8.0
        # Notes matching more of the query beat strong single-term hits.
        score *= 0.3 + 0.7 * matched / len(tokens)
        scored.append((score, note, lines))

    if not scored:
        return "\n".join(corrections
                         + [f"No matches for '{query}'. Try a synonym or a broader term."])
    scored.sort(key=lambda entry: (-entry[0], str(entry[1])))

    def best_line(lines: list[str]) -> str:
        best, best_key = "", (0, 0)
        for line in lines:
            if re.match(r"^(---|aliases:|tags:|sources:)", line):
                continue
            words = WORD_RE.findall(line.lower())
            distinct = sum(
                1 for t in tokens if any(_token_matches(w, t) for w in words)
            )
            key = (distinct, -len(line))
            if distinct and key > best_key:
                best_key, best = key, line.strip()
        return best

    hits = list(corrections)
    for _, note, lines in scored[:10]:
        line = best_line(lines)
        hits.append(
            f"NOTE MATCH: {note.relative_to(SYSTEM_DIR)}"
            + (f" | {line[:160]}" if line else "")
        )
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
        for moc in SYSTEM_DIR.glob("_index/*.md"):
            if chapter.strip().lower() in moc.stem.lower():
                return moc.read_text(encoding="utf-8")
        return f"No index note matching '{chapter}'."
    return "\n".join(sorted(p.stem for p in SYSTEM_DIR.glob("_index/*.md")))


def dispatch(name: str, arguments: dict) -> str:
    if name == "search_system":
        return search_system(str(arguments.get("query", "")))
    if name == "read_note":
        return read_note(str(arguments.get("name", "")))
    if name == "list_index":
        return list_index(str(arguments.get("chapter", "")))
    return f"Unknown tool: {name}"


VERBOSE = False


def _resp_stat(response, key):
    try:
        value = response[key]
        return value if value is not None else "?"
    except Exception:
        return "?"


def answer(question: str, model: str, messages: list | None = None, ctx: int = 8192) -> None:
    started = time.monotonic()
    try:
        _answer(question, model, messages, ctx)
    finally:
        print(f"{DIM}  [{time.monotonic() - started:.1f}s]{RESET}", file=sys.stderr)


def _inject_tool_call(messages: list, name: str, arguments: dict, result: str) -> None:
    print(f"{DIM}  [{name}({arguments})]{RESET}", file=sys.stderr)
    messages.append({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]})
    messages.append({"role": "tool", "name": name, "content": result})


def _answer(question: str, model: str, messages: list | None = None, ctx: int = 8192) -> None:
    if messages is None:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        SESSION_READS.clear()
    messages.append({"role": "user", "content": question})
    turn_start = len(messages)
    nudged = False
    read_nudged = False
    fake_nudged = False
    cite_nudged = False
    read_any = False
    read_paths = SESSION_READS
    # Every question's first round is a search, so run it harness-side
    # instead of paying a model call to ask for it — and hand over the top
    # hits already read, since reading them is the model's next move anyway.
    # A terse follow-up ("no i mean surges") is a useless query on its own,
    # so fold the previous question back in for the search.
    query = question
    if len(question) < 48:
        prev = next((m["content"] for m in reversed(messages[:turn_start - 1])
                     if isinstance(m, dict) and m.get("role") == "user"), "")
        if prev:
            query = f"{prev} {question}"
    results = search_system(query)
    _inject_tool_call(messages, "search_system", {"query": query}, results)
    # Inject only real notes: _index MOCs are link farms that flood the
    # model with talent names it then riffs on instead of reading.
    top_hits = [l for l in results.splitlines()
                if l.startswith("NOTE MATCH: notes/")
                or l.startswith("NOTE MATCH: _sources/")][:2]
    for hit in top_hits:
        path = SYSTEM_DIR / hit[len("NOTE MATCH: "):].split(" | ")[0]
        try:
            note_text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Trim the injected copy: frontmatter feeds the harness, not the
        # model, and a size cap keeps prefill cheap — read_note still
        # returns the full note when the model wants the rest.
        note_text = re.sub(r"\A---\n.*?\n---\n+", "", note_text, flags=re.DOTALL)
        if len(note_text) > 4000:
            # Cut at a section boundary: a mid-sentence cut reads like a
            # malformed note and invites invented section names.
            cut = note_text.rfind("\n#", 0, 4000)
            note_text = (note_text[:cut if cut > 1000 else 4000]
                         + "\n[truncated — read_note this note for the rest]")
        _inject_tool_call(messages, "read_note", {"name": path.stem}, note_text)
        read_any = True
        read_paths.add(path)
    for _ in range(MAX_TURNS):
        call_started = time.monotonic()
        try:
            response = ollama.chat(
                model=model,
                messages=messages,
                tools=TOOLS,
                options={"num_ctx": ctx, "temperature": 0, "num_predict": 800,
                         "repeat_penalty": 1.15},
                keep_alive="30m",
            )
        except Exception as exc:  # noqa: BLE001 - a dead model call must not kill the REPL
            print(f"Model call failed: {exc}")
            del messages[turn_start:]
            return
        message = response["message"]
        messages.append(message)
        tool_calls = message.get("tool_calls") or []
        if VERBOSE:
            print(
                f"{DIM}  [model call: {time.monotonic() - call_started:.1f}s, "
                f"prompt {_resp_stat(response, 'prompt_eval_count')} tok, "
                f"output {_resp_stat(response, 'eval_count')} tok, "
                f"{len(tool_calls)} tool call(s)]{RESET}",
                file=sys.stderr,
            )
            interim = (message.get("content") or "").strip()
            if interim and tool_calls:
                print(f"{DIM}  [model says: {interim[:200]}]{RESET}", file=sys.stderr)
        if not tool_calls:
            content = message.get("content", "").strip()
            no_coverage = re.fullmatch(
                r"the system does(?:n't| not) cover th\w+\.?", content.strip(),
                re.IGNORECASE) is not None
            if not read_any and not no_coverage:
                if read_nudged:
                    print("Answer withheld: the model wouldn't read any note in full. "
                          "Try rephrasing the question.")
                    del messages[turn_start:]
                    return
                read_nudged = True
                print(f"{DIM}  [answered without reading a note; nudging for a retry]{RESET}",
                      file=sys.stderr)
                messages.append({"role": "user", "content": READ_FIRST_NUDGE})
                continue
            fakes = fabricated_links(content)
            if fakes and not no_coverage:
                if fake_nudged:
                    print("Answer withheld: it cited notes that don't exist "
                          f"({', '.join(fakes)}). Try rephrasing the question.")
                    del messages[turn_start:]
                    return
                fake_nudged = True
                print(f"{DIM}  [answer cited nonexistent notes: "
                      f"{', '.join(fakes)}; nudging for a retry]{RESET}",
                      file=sys.stderr)
                messages.append(
                    {"role": "user",
                     "content": FABRICATED_NUDGE.format(names=", ".join(fakes))})
                continue
            unread = cited_unread(content, read_paths)
            if unread and not no_coverage:
                if cite_nudged:
                    print("Answer withheld: it cites pages from notes it never read "
                          f"({', '.join(unread)}). Try rephrasing the question.")
                    del messages[turn_start:]
                    return
                cite_nudged = True
                print(f"{DIM}  [answer cited unread notes: "
                      f"{', '.join(unread)}; nudging for a retry]{RESET}",
                      file=sys.stderr)
                messages.append(
                    {"role": "user",
                     "content": CITED_UNREAD_NUDGE.format(names=", ".join(unread))})
                continue
            uncited = "[[" not in content and not re.search(r"\bp(?:\.|age)\s*\d", content)
            if uncited and not nudged and not no_coverage:
                nudged = True
                print(f"{DIM}  [answer lacked citations; nudging for a retry]{RESET}",
                      file=sys.stderr)
                messages.append({"role": "user", "content": CITATION_NUDGE})
                continue
            # The small model likes to append an empty coverage disclaimer
            # after a full answer; strip that dangling line.
            if len(content) > 80:
                content = re.sub(
                    r"\n\(?the system does(?:n't| not) cover th\w+[.:]?\)?\s*\Z",
                    "", content, flags=re.IGNORECASE).rstrip()
            print(render_markdown(content))
            print_sources(content)
            # Keep only [user question, final answer] in history: the tool
            # dumps are the bulk of the context and follow-ups re-search.
            del messages[turn_start:-1]
            return
        for call in tool_calls:
            fn = call["function"]
            args = fn.get("arguments") or {}
            if fn["name"] == "read_note":
                read_any = True
                path = NAME_MAP.get(str(args.get("name", "")).strip().lower())
                if path is not None:
                    read_paths.add(path)
            result = dispatch(fn["name"], args)
            print(f"{DIM}  [{fn['name']}({args})]{RESET}", file=sys.stderr)
            if VERBOSE:
                preview = "\n".join(f"    | {l}" for l in result.splitlines()[:6])
                more = len(result.splitlines()) - 6
                if more > 0:
                    preview += f"\n    | ... (+{more} lines)"
                print(f"{DIM}{preview}{RESET}", file=sys.stderr)
            messages.append({"role": "tool", "name": fn["name"], "content": result})
    print("Stopped: too many tool-call rounds without a final answer.")


def main() -> None:
    global NAME_MAP, SYSTEM_DIR, RPG_NAME, SYSTEM_PROMPT
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", help="the rules question (omit for a REPL)")
    model_group = parser.add_mutually_exclusive_group()
    model_group.add_argument("--model", default=SMART_MODEL)
    model_group.add_argument("--dumb", "--fast", dest="fast", action="store_true",
                             help=f"use the small model ({FAST_MODEL})")
    model_group.add_argument("--slow", "--smart", dest="smart", action="store_true",
                             help=f"use the big model ({SMART_MODEL}) — the default")
    parser.add_argument("--ctx", type=int, default=8192,
                        help="context window tokens (lower = faster/less RAM)")
    parser.add_argument("--system", default="cosmere",
                        help="RPG system directory under systems/")
    parser.add_argument("--verbose", action="store_true",
                        help="show model timings/token counts, tool result previews")
    args = parser.parse_args()
    if args.smart:
        args.model = SMART_MODEL
    elif args.fast:
        args.model = FAST_MODEL

    global VERBOSE
    VERBOSE = args.verbose

    if not load_system(args.system):
        sys.exit(f"No system '{args.system}' under {SYSTEMS} (available: {available_systems()})")

    if args.question:
        answer(" ".join(args.question), args.model, ctx=args.ctx)
        return
    # Load the model while the user types their first question; an empty
    # prompt makes Ollama load and hold the model without generating.
    threading.Thread(
        target=lambda: ollama.generate(model=args.model, prompt="", keep_alive="30m"),
        daemon=True,
    ).start()
    print("Conversational REPL: follow-ups keep context. "
          "/2 or /open NAME shows a linked note, /system NAME switches "
          "system, /clear resets, q quits.", file=sys.stderr)
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
            SESSION_READS.clear()
            print("(context cleared)", file=sys.stderr)
            continue
        if question.startswith("/system"):
            name = question[7:].strip()
            listing = ", ".join(f"{s} ({display_name(s)})" for s in available_systems())
            if not name:
                print(f"Current system: {RPG_NAME}. Available: {listing}")
            elif load_system(name):
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                SESSION_READS.clear()
                print("(context cleared)", file=sys.stderr)
            else:
                print(f"No system '{name}'. Available: {listing}")
            continue
        if question.startswith("/open "):
            open_note(question[6:].strip())
            continue
        if re.fullmatch(r"/\d+", question):
            open_note(question[1:])
            continue
        answer(question, args.model, messages, ctx=args.ctx)


NAME_MAP: dict[str, Path] = {}
SESSION_READS: set[Path] = set()

if __name__ == "__main__":
    main()
