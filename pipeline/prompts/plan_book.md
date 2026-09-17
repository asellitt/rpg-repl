You are drafting the unit map (chapters.json) for ingesting the
{book_title} sourcebook (slug: {book}) into the "{system}" RPG system's
notes. Do not write any notes — your only deliverable is the unit map.

Inputs:

1. Read {outline} — the book's structure: every H1 heading with its pdf
   page, plus the list of pages with no extractable text (full-art pages,
   which usually mark chapter boundaries). Printed page = pdf page -
   {page_offset}.
2. Read {system_dir}/_meta/conventions.md for the note and MOC policies.
3. List {system_dir}/_index/ to see the MOC names that already exist.

Draft the unit map as a JSON array and Write it to {chapters_path}.
Each entry: {{"id": ..., "title": ..., "moc": ..., "pdf_start": ...,
"pdf_end": ...}}.

Unit rules:

- Units are contiguous pdf-page ranges of 8-30 pages. Split large
  chapters at their natural section boundaries (one unit per character
  path, per bestiary alphabet chunk, per A-Z reference block); keep
  small chapters whole.
- id: two digits in book order ("01", "02"), with a letter suffix for
  a split chapter's units ("04a", "04b"). Ignore any heading noise in
  the outline (drop caps, captions, artist names).
- title: "Chapter N: Name — section range" so a human can see what the
  unit covers at a glance.
- moc: the MOC name only (no path, no .md). Rules chapters reuse the
  existing shared MOC name when this system already has one for that
  topic (e.g. every handbook's Combat chapter maps to "Combat"). Lore
  chapters get setting-scoped names; if this book's setting differs
  from an existing same-named lore MOC, qualify it: "The World (X)".
- Skip: covers, table of contents, glossary/index, acknowledgments,
  marketing/product pages, and pure "what is a roleplaying game"
  onboarding. A handbook's core-rules Introduction IS processed (it
  merge-cites the core notes); a world guide's short intro is skipped.
- Use the empty/art pages between chapters as boundary hints; a
  chapter's unit range includes its trailing art page.

After writing the file, print a short summary: units, total pages
covered, pages skipped and why, and any MOC-name decisions that need
human review (collisions, setting qualifications).
