You are populating an Obsidian rules knowledge base for the Cosmere RPG
(Stormlight Handbook). Work only inside the vault/ directory of the
current project.

Your unit of work: **{title}** (printed pages {printed_start}-{printed_end}).

Steps, in order:

1. Read vault/_meta/conventions.md and follow it exactly — frontmatter,
   filenames, tag taxonomy, note body shape, granularity policy.
2. Read {chunk} — the extracted text for this unit. Every page begins
   with `<!-- pdf_page: N -->` and `<!-- printed_page: N -->` markers;
   use them to fill the pages/pdf_pages frontmatter of every note you
   write (pdf page = printed page + 4).
3. Before naming or linking anything, list the existing notes in
   vault/rules/ and check their aliases (grep for `aliases:`). Link to
   existing notes under their existing names. Never create a duplicate
   note for a concept that already has one.
4. Write one note per concept from this text into vault/rules/, per the
   conventions. Restate rules faithfully — keep every number, die, DC,
   cost, range, and duration exact. Wikilink the first mention of other
   concepts; forward links to concepts from chapters not yet processed
   are correct even though the target doesn't exist yet.
5. Create or update the chapter MOC at "{moc}": list every note from
   this chapter processed so far (keep entries added by earlier units of
   the same chapter), grouped by the book's own section structure, one
   line each. Record deliberately skipped lore/flavor sections at the
   bottom under "Not extracted as rules".

Extraction artifacts — ignore them, never transcribe them:

- Drop caps split into fake headings: a heading of 1-2 letters followed
  by a line continuing the word.
- Image captions and alt-text: artist names in caps, caption sentences
  rendered as headings, garbled diagram descriptions. Reconstruct any
  rules content a diagram's alt text carries (e.g. dice conversion
  tables) into a proper table.
- Table-of-contents dot leaders and stray page numbers.

Do not modify notes belonging to other chapters. Do not write anything
outside vault/.

When done, print exactly one line per file you created or updated, in
the form: `NOTE: <path>`.
