# Vault conventions

This vault is a rules knowledge base for one RPG system, built for agent
search (grep + wikilink traversal). These conventions are binding for every
note, whether written interactively or by the headless pipeline.

## Structure

systems/ is one Obsidian vault. Each RPG system is a top-level directory
inside it, and every note, link, and agent operates within a single
system's directory:

```
systems/          the Obsidian vault (open this in Obsidian)
  <system>/
    _meta/        conventions (this file)
    _sources/     one note per source book
    _index/       one MOC (map of content) per chapter/topic
    notes/        one note per atomic concept — flat, no subfolders
```

Wikilinks are written as bare note names and must only target notes in
the same system.

## One note per concept

A concept gets its own note when a player or GM would look it up by name:
a rule, action, condition, talent, character option, notable item type.
Undifferentiated list content (price tables, mundane gear lists) stays
grouped in one note per table.

## Filenames and titles

- Filename is the concept's canonical name in Title Case: `Plot Die.md`.
  The H1 inside matches the filename exactly.
- The canonical name is the book's own term. Synonyms, plurals, and
  player-speak go in `aliases`, never in the filename.

## Frontmatter (required on every note)

```yaml
---
aliases: [Synonym One, Synonym Two]
tags: [rule/core]
sources: ["<book-slug>: 142-143"]
---
```

- `aliases`: synonyms an agent or player might search for. Be generous.
- `tags`: exactly one primary type tag from the taxonomy below; add more
  only when the concept genuinely spans types.
- `sources`: one string per source book that defines this concept, in
  the form `"<_sources note name>: <printed pages>"`. Printed pages are
  what you cite at the table.

## Multiple source books

Within a system, the rules are consistent across settings — a later
book restates or at most expands a rule, never contradicts it. One note
per concept, always:

- A later book restating an existing concept adds its citation to the
  note's `sources` list and Source section; never a second note.
- A later book adding genuinely new detail to an existing concept merges
  that detail into the existing note, under the same citation
  discipline.
- Content unique to a book gets new notes as normal.

## Setting variants

Adversaries and setting-specific content can share a name across
settings while having different content. When a book defines a concept
whose name already has a note with **different** content:

- The existing note keeps its name unchanged.
- The new concept gets its own note named "Name (Setting)", tagged with
  `setting/<setting>` in addition to its type tag.
- The two notes link to each other with a one-line "differs by setting"
  clause in Related.

Never merge two same-named concepts whose mechanics differ, and never
apply this to core rules — a core rule that reads differently in a new
book is an expansion to merge, not a variant.

## Tag taxonomy

TODO(new system): tailor this list to the system's own structure before
processing the first book. Keep the `rule/`, `lore/`, `adversary`, and
`setting/` namespaces; rename or extend the rest to match the system's
concepts.

- `rule/core` — dice, tests, core resolution, advancement
- `rule/character` — creation, statistics, attributes, skills
- `rule/combat`, `rule/social`, `rule/gm`
- `talent`, `item`, `condition`, `action`, `companion`
- `adversary` — stat blocks for NPCs and creatures the party can face
- `lore/nation`, `lore/faction`, `lore/person`, `lore/place`,
  `lore/history`, `lore/cosmology`, `lore/nature` — setting knowledge
- `setting/<setting>` — on setting-variant notes (see Setting variants)

## Lore notes

Setting content is extracted as notes, tagged in the `lore/` namespace:
one note per nation, faction, notable person, place, era, or cosmological
concept a player or GM would ask about. A lore note carries a concise
factual summary with page citations — not a full transcription of the
chapter's prose. Its body uses `## Details` where a rules note uses
`## Rules`; the rest of the note shape is identical. When a lore concept
has associated mechanics, the mechanics live in their own
`adversary`/rule note and the two link to each other.

## Note body

```markdown
# Concept Name

One-paragraph plain-language summary. This is what an agent skims to
decide relevance — it must stand alone.

## Rules
Faithful restatement of the mechanic. Keep the book's numbers, dice,
and terminology exact. Subheadings as needed.

## Related
- [[Other Note]] — one clause on how it relates

## Source
[[<book-slug>]], p. 142–143
```

The Source section carries one line per book in the `sources` list.

- Restate rules faithfully; do not paraphrase numbers, DCs, dice, costs,
  or ranges. Flavor text is summarized or omitted.
- Wikilink the first mention of any other concept in the body. Linking
  to a note that doesn't exist yet is correct — it resolves when that
  chapter is processed.
- Never line-wrap a wikilink: `[[Name]]` must sit on one line, even if
  the line runs long.
- Every note ends with the Source section citing printed pages.

## MOCs (`_index/`)

One per chapter/topic. Lists every note from that chapter with a
one-line description, grouped by subheading matching the book's
structure.

Rules-chapter MOCs are shared topics across books (every handbook's
Combat chapter feeds the one Combat MOC). Lore MOCs are setting-scoped
and never merge across settings — where two settings' books use the same
chapter name, the MOC name carries the setting, e.g.
"The World (Setting)".
