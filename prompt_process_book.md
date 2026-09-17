You are populating an Obsidian rules knowledge base for the {book_title}
sourcebook of the "{system}" RPG system. Work only inside the {system_dir}/
directory of the current project.

Your unit of work: **{title}** (printed pages {printed_start}-{printed_end}).

Steps, in order:

1. Read {system_dir}/_meta/conventions.md and follow it exactly — frontmatter,
   filenames, tag taxonomy, note body shape, granularity policy.
2. Read {chunk} — the extracted text for this unit. Every page begins
   with `<!-- pdf_page: N -->` and `<!-- printed_page: N -->` markers;
   use the printed page numbers for the sources frontmatter of every
   note you write. This book's sources entry is:
       sources: ["{book}: <printed pages>"]
3. Before naming or linking anything, list the existing notes in
   {system_dir}/notes/ and check their aliases (grep for `aliases:`). Link to
   existing notes under their existing names.
4. When a concept from this text already has a note (from any book of
   this system): never create a duplicate. If this book restates the
   same rule, add this book's citation to the existing note's sources
   list (and its Source section). If this book adds genuinely new
   detail, merge it into the existing note under the same citation
   discipline. Concepts unique to this book get new notes as normal.
5. Write one note per new concept into {system_dir}/notes/, per the
   conventions. Restate rules faithfully — keep every number, die, DC,
   cost, range, and duration exact. Wikilink the first mention of other
   concepts; forward links to concepts not yet processed are correct
   even though the target doesn't exist yet.
6. Create or update the chapter MOC at "{moc}": list every note from
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

Do not modify notes belonging to other chapters except as step 4
directs. Do not write anything outside {system_dir}/.

When done, print exactly one line per file you created or updated, in
the form: `NOTE: <path>`.
