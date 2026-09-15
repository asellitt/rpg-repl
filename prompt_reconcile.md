You are reconciling the "{system}" RPG system's vault after ingesting
the {book_title} sourcebook (slug: {book}). Read {report} — the
postflight report — and resolve every finding, working only inside
{vault}/.

Read {vault}/_meta/conventions.md first.

Before anything else, verify this book's note in {vault}/_sources/:
its chapter table must cover the WHOLE book (unit workers sometimes
write "covered so far" stubs that never get extended), the extracted
text pointer must be the book's extracted.txt, and any "defined
elsewhere" expectations must not name a book that is already fully
processed. Fix what's wrong before reasoning from it — the deferral
decisions below depend on this note being truthful. Check the state of
other books at books/<system>/*/work/state.json. Then apply the standard
decision tree to each unresolved wikilink, choosing the LEAST invasive
fix that makes the vault honest:

1. Naming mismatch — the target exists under another name or covers the
   concept (check aliases and similar note names): add the link's name
   as an alias to that note, or retarget the link, whichever reads
   better. A book-section title whose content became several atomic
   notes gets a small hub note named after the section, linking its
   pieces (frontmatter cites the section's printed pages; add the hub to
   the chapter's MOC).
2. Passing mention — a name the books never define and a reader wouldn't
   look up (a city mentioned once, a person name-dropped): unlink it to
   plain text in the referring note.
3. Genuinely defined elsewhere — a concept this system's unprocessed or
   unowned books define: leave the link dangling and record it under
   "## Referenced but defined elsewhere" in the referring book's
   _sources note (one line: link — what it is, where it's expected).
   Remove entries from those lists that now resolve.

For MOC-coverage findings, add the listed notes to the appropriate MOC
section (or the hub note that reaches them, plus the hub to the MOC).

Then symmetrize any new setting forks (conventions.md "Setting
variants"): for each "Name (Setting)" note this book created whose
counterpart is still the bare "Name", rename the counterpart to
"Name (ItsSetting)" with its `setting/` tag, create the bare-name
disambiguation hub linking both variants with both citations, retarget
bare-name links whose referrer's sources are one setting's books to
that setting's variant (shared/merged referrers keep the bare name),
and update MOC entries to name the variant they describe.

Never delete notes, never rename existing notes, never modify
chapters.json or anything outside {vault}/. Keep every edit minimal.

Do not run any commands — the pipeline validates the vault right after
you finish. Your final message must be exactly: one line per file you
changed, in the form `FIXED: <path> — <what>`, and one line per link
deliberately left dangling, in the form
`DEFERRED: [[name]] — <expected source>`.
