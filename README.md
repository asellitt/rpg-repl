# rpg-repl

Turns RPG rulebook PDFs into per-system Obsidian rules vaults, and
answers rules questions over them with a local LLM.

## Layout

```
transcribe_pdf.py       PDF -> page-tagged plain text
prompt_template.md      per-unit prompt for the headless note writer
run_chapters.py         drives `claude -p` over a book's units, resumable
validate_vault.py       vault checks (--fix unwraps broken wikilinks)
ask.py                  local rules-lawyer agent (Ollama)
systems/                ONE Obsidian vault (the product); each RPG
                        system is a top-level directory inside it
books/<system>/<book>/  per-book pipeline: book.json, chapters.json,
                        extracted.txt, work/ (chunks + state), logs/
```

## Ingesting a book

1. Extract:
   `python3 transcribe_pdf.py BOOK.pdf books/<system>/<slug>/extracted.txt --layout --known-pair 5=1`
2. Create `books/<system>/<slug>/book.json` (`title`, `page_offset`) and
   `chapters.json` (unit map from the book's ToC).
3. Run the units (resumable; state is per book):
   ```bash
   python3 run_chapters.py --system <system> --book <slug>
   python3 run_chapters.py --status
   python3 run_chapters.py 05 --model opus
   ```
4. `python3 validate_vault.py --system <system> --links`

Flags default to the sole system/book when only one exists.

## Asking rules questions (any machine)

Needs only `systems/` and `ask.py`.

1. Install Ollama (https://ollama.com) and pull a tool-calling model:
   `ollama pull qwen2.5:14b`
2. `pip install ollama`
3. `python3 ask.py "how does raising the stakes work?"`

Bare `python3 ask.py` gives a conversational REPL: cyan questions,
numbered note links (`/2` opens one inline, `/open NAME` by name),
`/system NAME` switches RPG system and clears context, `/clear` resets,
`--system` picks the starting system, `--model`/`--ctx` tune the model.
Every answer ends with a Sources footer citing book and printed pages
from note frontmatter.
