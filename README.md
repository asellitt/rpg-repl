# rpg-repl

Turns RPG rulebook PDFs into per-RPG-system Obsidian rules notes, and
answers rules questions over them with a local LLM.

## Layout

```
CLAUDE.md                 pipeline operating knowledge (auto-loaded by Claude Code)
ask.py                    local rules-lawyer agent (Ollama); ships with systems/
rules                     one-command REPL: starts a tuned Ollama if needed, runs ask.py
systems/                  ONE Obsidian vault (the product); each RPG
                          system is a top-level directory inside it
books/<system>/<book>/    per-book pipeline: book.json, chapters.json,
                          extracted.txt, work/ (chunks + state), logs/
pipeline/
  transcribe_pdf.py       PDF -> page-tagged plain text (+ book.json via --title)
  plan_book.py            drafts a book's chapters.json; scaffolds new systems
  process_book.py         drives `claude -p` over a book's units, resumable
  validate_book.py        post-book report; --reconcile applies standard fixes
  validate_system.py      system checks (--fix unwraps broken wikilinks)
  conventions_template.md seed conventions for a new system
  prompts/                the headless workers' task prompts, one per stage
viewer/
  serve.py                self-hosted web viewer for the systems
  Dockerfile, compose.yaml, logo.svg
```

All pipeline commands run from the repo root.

## Ingesting a book

```bash
python3 pipeline/transcribe_pdf.py BOOK.pdf books/<system>/<slug>/extracted.txt \
    --layout --known-pair 5=1 --title "Book Title"
python3 pipeline/plan_book.py --system <system> --book <slug>   # then REVIEW the draft
python3 pipeline/process_book.py --system <system> --book <slug>   # resumable; --status
python3 pipeline/validate_book.py --system <system> --book <slug> --reconcile
```

Flags default to the sole system/book when only one exists (process_book).
A new system name scaffolds its directory from conventions_template.md —
review the tag taxonomy before running units.

## Browsing the systems in a browser

`python3 viewer/serve.py` (needs `pip install markdown`) serves the live
system notes at http://127.0.0.1:8420 — rendered markdown, resolved
wikilinks and aliases, tag/source chips, backlinks, per-system search.
`--host 0.0.0.0` makes it reachable on the LAN; `--port` changes the port.

## Asking rules questions (any machine)

Needs only `systems/`, `ask.py`, and `ask`.

1. Install Ollama (https://ollama.com) and pull a tool-calling model:
   `ollama pull qwen2.5:14b` (and `qwen2.5:7b` if you want `--fast`)
2. `pip install ollama`
3. `./ask "how does raising the stakes work?"`

`./ask` starts an Ollama server with the fast settings
(`OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`) when none is
running, stops it again when you quit, and passes every argument to
ask.py — running `python3 ask.py` directly against your own server
works the same.

Bare `python3 ask.py` gives a conversational REPL: cyan questions,
numbered note links (`/2` opens one inline, `/open NAME` by name),
`/system NAME` switches RPG system and clears context, `/clear` resets,
`--system` picks the starting system, `--fast`/`--dumb` swaps to the
small model, `--slow`/`--smart` the big one (the default), and
`--model`/`--ctx` tune it by hand.
Every answer ends with a Sources footer citing book and printed pages
from note frontmatter.
s
