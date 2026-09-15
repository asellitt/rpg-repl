# rpg-repl pipeline

Turns RPG rulebook PDFs into per-system Obsidian rules vaults
(`systems/`), and answers rules questions over them with a local LLM
(`ask.py` + Ollama). Everything below is the operating knowledge for
running the pipeline; note-level rules live in each system's
`systems/<system>/_meta/conventions.md`, which binds every note.

## Layout and state

```
systems/                 ONE Obsidian vault; one top-level dir per RPG system
  <system>/_meta/        conventions.md — note format, tags, policies
  <system>/_sources/     one note per book (title H1, chapter table,
                         "Referenced but defined elsewhere" list)
  <system>/_index/       MOCs (maps of content), one per chapter/topic
  <system>/notes/        all notes, flat; type carried by tags not folders
books/<system>/<slug>/   per-book pipeline: book.json {title, page_offset},
                         chapters.json (unit map), extracted.txt,
                         work/ (chunks, state.json, outline), logs/
```

Unit state is `books/<system>/<slug>/work/state.json` — the driver skips
`done` units, so runs are resumable. Never edit `prompt_template.md`,
`chapters.json`, or a book's `work/` while that book's run is live (the
template is re-read per unit; state is rewritten wholesale per unit).

## Pipeline per book

1. `python3 transcribe_pdf.py BOOK.pdf books/<system>/<slug>/extracted.txt
   --layout --known-pair PDF=PRINTED --title "Book Title"`
   Review its furniture report (real content vs running heads) and the
   empty-page list (full-art pages are normal).
2. `python3 map_chapters.py --system <system> --book <slug>` — scaffolds
   a new system's vault if needed, builds the heading outline, drafts
   chapters.json via a headless worker. REVIEW the draft (unit sizes,
   skips, MOC names) before running units.
3. `python3 run_chapters.py --system <system> --book <slug>` — headless
   note-writing, one `claude -p` worker per unit (~5-20 min each,
   default --model sonnet). Validator runs per unit and halts the run on
   hard failures; fix and re-run (completed units are skipped).
4. `python3 book_postflight.py --system <system> --book <slug>` — the
   post-book report (dangling links, MOC coverage, merge stats); add
   `--reconcile` to have a headless worker apply the standard fixes.
5. Ship: zip `systems/` + `ask.py` to the REPL machine.

## Policies (rationale lives in conventions.md)

- One note per concept per system. A later book restating a concept ADDS
  a citation (`sources: ["<slug>: <printed pages>"]` entries); new detail
  merges into the existing note; never duplicate.
- Setting variants: same name + different mechanics (mostly adversaries
  across settings) → workers create "Name (Setting)" + `setting/<x>`
  tag and leave the existing note alone; the postflight pass then
  symmetrizes the fork (both notes setting-qualified, the bare name a
  disambiguation hub, links retargeted by referrer setting). Never fork
  core rules — those are consistent across settings by design.
- Lore is first-class: `lore/*` tags, `## Details` body heading, concise
  summaries. Scadrial content that is era-specific gets `era/1`/`era/2`.
- MOCs: rules-chapter MOCs are shared topics across books (every
  handbook's Combat feeds one Combat MOC); lore MOCs are setting-scoped
  ("The World (Scadrial)").
- Citations use printed pages only (pdf page = printed + book.json's
  page_offset).

## Known failure modes and watch-items

- Workers sometimes line-wrap wikilinks; `validate_vault.py --fix`
  repairs this mechanically (the driver runs it per unit).
- `[[Name\|display]]` (escaped pipe in tables) is valid Obsidian; all
  tools normalize it before parsing.
- Extraction artifacts workers must ignore: drop caps as 1-2 letter
  headings, image captions/artist names, garbled table alt-text (worth
  spot-checking any reconstructed dice/metal tables against the PDF).
- After a merge-heavy unit (a handbook Introduction, core-rule chapters
  of a second handbook), spot-check a couple of core notes (Skill Test,
  Plot Die) for sane merged citations rather than churn.
- Dangling links after a book are normal: forward refs to unprocessed
  chapters/books, book-section names that became several atomic notes
  (fix: hub note), or concepts defined in unowned books (document in the
  _sources note's "Referenced but defined elsewhere").
- The `claude -p` workers run with --permission-mode acceptEdits from
  the repo root; their transcripts land in the book's logs/.

## The REPL (ask.py)

Local rules lawyer over one system's notes: Ollama + tool calling
(search/read/index), conversational, `/system` switches system,
`/N` opens linked notes, Sources footer is harness-generated from
frontmatter. Model default qwen2.5:14b; on slow hardware use
llama3.1:8b. Its answers only cite what the vault holds.
