# rpg-repl

Turns RPG rulebook PDFs into per-RPG-system Obsidian rules notes, and
answers rules questions over them with a local LLM.

## Layout

```
CLAUDE.md               pipeline operating knowledge (auto-loaded by Claude Code)
transcribe_pdf.py       PDF -> page-tagged plain text (+ book.json via --title)
plan_book.py            drafts a book's chapters.json; scaffolds new systems
process_book.py         drives `claude -p` over a book's units, resumable
validate_book.py        post-book report; --reconcile applies standard fixes
validate_system.py      system checks (--fix unwraps broken wikilinks)
ask.py                  local rules-lawyer agent (Ollama)
serve.py                self-hosted web viewer for the systems
prompt_*.md             the headless workers' task prompts
conventions_template.md seed conventions for a new system
systems/                ONE Obsidian vault (the product); each RPG
                        system is a top-level directory inside it
books/<system>/<book>/  per-book pipeline: book.json, chapters.json,
                        extracted.txt, work/ (chunks + state), logs/
```

## Ingesting a book

```bash
python3 transcribe_pdf.py BOOK.pdf books/<system>/<slug>/extracted.txt \
    --layout --known-pair 5=1 --title "Book Title"
python3 plan_book.py --system <system> --book <slug>   # then REVIEW the draft
python3 process_book.py --system <system> --book <slug>   # resumable; --status
python3 validate_book.py --system <system> --book <slug> --reconcile
```

Flags default to the sole system/book when only one exists (process_book).
A new system name scaffolds its directory from conventions_template.md —
review the tag taxonomy before running units.

## Browsing the systems in a browser

`python3 serve.py` (needs `pip install markdown`) serves the live
system notes at http://127.0.0.1:8420 — rendered markdown, resolved
wikilinks and aliases, tag/source chips, backlinks, per-system search.
`--host 0.0.0.0` makes it reachable on the LAN; `--port` changes the port.

## Asking rules questions (any machine)

Needs only `systems/` and `ask.py`.

1. Install Ollama (https://ollama.com) and pull a tool-calling model:
   `ollama pull qwen2.5:7b`
2. `pip install ollama`
3. `python3 ask.py "how does raising the stakes work?"`

Bare `python3 ask.py` gives a conversational REPL: cyan questions,
numbered note links (`/2` opens one inline, `/open NAME` by name),
`/system NAME` switches RPG system and clears context, `/clear` resets,
`--system` picks the starting system, `--model`/`--ctx` tune the model.
Every answer ends with a Sources footer citing book and printed pages
from note frontmatter.
