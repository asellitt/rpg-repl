# rpg-repl

Turns RPG rulebook PDFs into an Obsidian rules vault, and answers
rules questions over that vault with a local LLM.

## Layout

```
transcribe-pdf.py       PDF -> page-tagged plain text
chapters.json           chapter/unit map (pdf page ranges)
prompt_template.md      per-unit prompt for the headless note writer
run_chapters.py         drives `claude -p` over the units, resumable
validate_vault.py       vault checks (--fix unwraps broken wikilinks)
ask.py                  local rules-lawyer agent (Ollama)
input/                  source PDFs
output/                 extracted tagged text
work/                   sliced chunks + run state
logs/                   headless worker transcripts
vault/                  the Obsidian vault (the product)
```

## Building the vault

```bash
python3 transcribe_pdf.py "input/BOOK.pdf" output/book.txt --layout --known-pair 5=1
python3 run_chapters.py            # all pending units (resumable)
python3 run_chapters.py 05 --model opus   # specific units / model
python3 run_chapters.py --status
python3 validate_vault.py --links
```

## Asking rules questions (any machine)

Needs only `vault/` and `ask.py`.

1. Install Ollama (https://ollama.com) and pull a tool-calling model:
   `ollama pull qwen2.5:14b`
2. `pip install ollama`
3. `python3 ask.py "how does raising the stakes work?"`
   or run bare for a REPL; `--model llama3.1:8b` to use a smaller model.

The agent searches note names/aliases/content, reads notes, follows
wikilinks, and cites printed book pages from each note's frontmatter.
