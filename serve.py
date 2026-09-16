#!/usr/bin/env python3
"""
Self-hosted web viewer for the systems/ vaults: rendered markdown,
resolved wikilinks (aliases included), tag/source chips, backlinks,
and per-system search. Reads the live files — no build step.

Usage:
    python3 serve.py                 # http://127.0.0.1:8420
    python3 serve.py --port 9000
    python3 serve.py --host 0.0.0.0  # reachable from the LAN

Requires: pip install markdown
"""

import argparse
import html
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

try:
    import markdown
except ImportError:
    sys.exit("The markdown package is not installed. Run: pip install markdown")

ROOT = Path(__file__).resolve().parent
SYSTEMS = ROOT / "systems"
CACHE_TTL = 30  # seconds before the link/alias index is rebuilt
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

LOGO = """<svg width="20" height="20" viewBox="0 0 24 24" fill="none" \
stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">\
<path d="M12 2l8.5 5v10L12 22l-8.5-5V7z"/>\
<path d="M9 8.5l4 3.5-4 3.5M14.5 15.5h3"/></svg>"""

# favicon: same mark minus the cursor line, fixed accent color (favicons
# don't inherit currentColor)
FAVICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
               'stroke="%230b6bcb" stroke-width="2.2" stroke-linejoin="round" '
               'stroke-linecap="round"><path d="M12 2l8.5 5v10L12 22l-8.5-5V7z"/>'
               '<path d="M9 8.5l4 3.5-4 3.5"/></svg>')

JS = """
const linkables = [...document.querySelectorAll('main a.kbd, main a.wl')];
linkables.forEach((a) => a.insertAdjacentHTML('afterend', '<sup class="kbdnum" hidden></sup>'));
let targets = [];
function assignNumbers() {
  const vh = window.innerHeight;
  targets = linkables.filter((a) => {
    const r = a.getBoundingClientRect();
    return r.bottom > 0 && r.top < vh;
  }).slice(0, 10);
  linkables.forEach((a) => { a.nextElementSibling.hidden = true; });
  targets.forEach((a, n) => {
    const badge = a.nextElementSibling;
    badge.textContent = (n + 1) % 10;
    badge.hidden = false;
  });
}
assignNumbers();
let debounce;
for (const ev of ['scroll', 'resize']) {
  window.addEventListener(ev, () => {
    clearTimeout(debounce);
    debounce = setTimeout(assignNumbers, 120);
  }, { passive: true });
}
document.addEventListener('keydown', (e) => {
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea') {
    if (e.key === 'Escape') e.target.blur();
    return;
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'f') {
    const box = document.querySelector('nav input[name=q]');
    if (box) { e.preventDefault(); box.focus(); box.select(); }
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'Backspace') { e.preventDefault(); history.back(); return; }
  if (e.key === 'h' || e.key === 'H') { location.href = '/'; return; }
  const i = '1234567890'.indexOf(e.key);
  if (i >= 0) {
    assignNumbers();  // never act on a stale, mid-scroll assignment
    if (targets[i]) location.href = targets[i].href;
  }
});
"""

CSS = """
:root { color-scheme: light dark;
  --bg: #faf9f6; --fg: #1f2328; --muted: #6a737d; --accent: #0b6bcb;
  --chip: #e8eef7; --card: #ffffff; --border: #d8dde3; --code: #f0f2f5; }
@media (prefers-color-scheme: dark) { :root {
  --bg: #14171a; --fg: #dfe4ea; --muted: #8b949e; --accent: #6cb2ff;
  --chip: #22304a; --card: #1b2026; --border: #333a42; --code: #22272e; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 48rem; margin: 0 auto; padding: 1rem 1.2rem 4rem; }
nav.top { border-bottom: 1px solid var(--border); padding: .6rem 1.2rem;
  display: flex; gap: 1rem; align-items: baseline; flex-wrap: wrap; }
nav.top a { color: var(--accent); text-decoration: none; font-weight: 600; }
a.brand { display: inline-flex; align-items: center; gap: .45rem; }
a.brand svg { flex: none; }
nav.top form { margin-left: auto; }
nav.top input { background: var(--card); color: var(--fg);
  border: 1px solid var(--border); border-radius: 6px; padding: .3rem .6rem; }
h1, h2, h3 { line-height: 1.25; }
h1 { margin-top: 1rem; }
h2 { border-bottom: 1px solid var(--border); padding-bottom: .2rem; margin-top: 2rem; }
a { color: var(--accent); }
a.unresolved { color: var(--muted); text-decoration: underline dashed; }
.meta { display: flex; gap: .4rem; flex-wrap: wrap; margin: .4rem 0 1.2rem; }
.chip { background: var(--chip); border-radius: 999px; padding: .1rem .7rem;
  font-size: .8rem; }
.chip.t-rule { background: rgba(11,107,203,.16); }
.chip.t-lore { background: rgba(30,150,80,.16); }
.chip.t-adv { background: rgba(200,60,60,.16); }
.chip.t-scope { background: rgba(140,80,200,.16); }
.chip.src { background: transparent; border: 1px solid var(--border);
  color: var(--muted); }
table { border-collapse: collapse; display: block; overflow-x: auto;
  margin: 1rem 0; }
th, td { border: 1px solid var(--border); padding: .35rem .7rem;
  text-align: left; }
th { background: var(--code); }
tbody tr:nth-child(even) { background: color-mix(in srgb, var(--code) 45%, transparent); }
tbody tr:hover { background: var(--chip); }
code, pre { background: var(--code); border-radius: 6px; }
code { padding: .1rem .35rem; }
pre { padding: .8rem; overflow-x: auto; }
ul.cards { list-style: none; padding: 0; display: grid; gap: .5rem;
  grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); }
ul.cards li { background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: .6rem .9rem; transition: border-color .15s; }
ul.cards li:hover { border-color: var(--accent); }
ul.cards a { text-decoration: none; font-weight: 600; }
mark { background: rgba(230,180,0,.35); color: inherit; border-radius: 3px;
  padding: 0 .1rem; }
sup.kbdnum { color: var(--muted); font-size: .68em; margin-left: .18em;
  user-select: none; }
.tierlabel { color: var(--muted); font-size: .8rem; text-transform: uppercase;
  letter-spacing: .06em; margin: 1.4rem 0 .4rem; }
.backlinks { margin-top: 3rem; border-top: 1px solid var(--border);
  padding-top: .8rem; }
.backlinks h3, .muted { color: var(--muted); }
.hit { margin: .8rem 0; }
.hit .line { color: var(--muted); font-size: .9rem; }
"""


class SystemIndex:
    """Cached per-system view: name/alias resolution, texts, backlinks."""

    def __init__(self, system: str):
        self.system = system
        self.built = 0.0

    def refresh(self) -> None:
        if time.time() - self.built < CACHE_TTL:
            return
        vault = SYSTEMS / self.system
        self.names: dict[str, Path] = {}
        self.texts: dict[Path, str] = {}
        self.backlinks: dict[str, set[str]] = {}
        self.book_titles: dict[str, str] = {}

        for folder in ("notes", "_index", "_sources"):
            for path in sorted((vault / folder).glob("*.md")):
                text = path.read_text(encoding="utf-8")
                self.texts[path] = text
                self.names.setdefault(path.stem.lower(), path)
                m = re.search(r"^aliases:\s*\[(.*)\]", text, re.MULTILINE)
                if m and folder != "_index":
                    for alias in m.group(1).split(","):
                        alias = alias.strip().strip("\"'")
                        if alias:
                            self.names.setdefault(alias.lower(), path)
                if folder == "_sources":
                    h1 = re.search(r"^# (.+)$", text, re.MULTILINE)
                    self.book_titles[path.stem] = h1.group(1).strip() if h1 else path.stem

        for path, text in self.texts.items():
            for m in WIKILINK_RE.finditer(text.replace("\\|", "|")):
                target = self.names.get(m.group(1).strip().lower())
                if target is not None and target != path:
                    self.backlinks.setdefault(str(target), set()).add(path.stem)
        self.built = time.time()

    def resolve(self, name: str) -> Path | None:
        return self.names.get(name.strip().lower())


INDEXES: dict[str, SystemIndex] = {}


def display_name(system: str) -> str:
    meta = SYSTEMS / system / "_meta" / "system.json"
    if meta.exists():
        try:
            return json.loads(meta.read_text(encoding="utf-8")).get("display_name", system)
        except Exception:
            pass
    return system


def system_names() -> list[str]:
    return sorted(p.name for p in SYSTEMS.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def index_for(system: str) -> SystemIndex:
    if system not in INDEXES:
        INDEXES[system] = SystemIndex(system)
    INDEXES[system].refresh()
    return INDEXES[system]


def note_url(system: str, stem: str) -> str:
    return f"/{quote(system)}/note/{quote(stem)}"


def render_markdown_body(text: str, system: str, idx: SystemIndex) -> str:
    text = text.replace("\\|", "|")

    def link(m: re.Match) -> str:
        name = m.group(1).strip()
        display = html.escape((m.group(2) or name).strip())
        target = idx.resolve(name)
        if target is None:
            return f'<a class="unresolved" title="no note yet">{display}</a>'
        return f'<a class="wl" href="{note_url(system, target.stem)}">{display}</a>'

    text = WIKILINK_RE.sub(link, text)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    return md.convert(text)


def page(title: str, body: str, system: str | None = None) -> str:
    crumbs = f'<a class="brand" href="/">{LOGO}rpg-repl</a>'
    search = ""
    if system:
        label = html.escape(display_name(system))
        crumbs += f' / <a href="/{quote(system)}/">{label}</a>'
        search = (f'<form action="/{quote(system)}/search">'
                  f'<input name="q" placeholder="search {label}…"></form>')
    favicon = quote(FAVICON_SVG, safe="/ :=\"'.,-")
    return (f"<!doctype html><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title>"
            f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{favicon}">'
            f"<style>{CSS}</style>"
            f"<nav class='top'>{crumbs}{search}</nav><main>{body}</main>"
            f"<script>{JS}</script>")


def frontmatter_chips(text: str, idx: SystemIndex) -> tuple[str, str]:
    """Return (chips html, body-without-frontmatter)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    fm, body = m.group(1), text[m.end():]
    chips = []
    tags = re.search(r"^tags:\s*\[(.*)\]", fm, re.MULTILINE)
    if tags:
        for t in tags.group(1).split(","):
            t = t.strip()
            if not t:
                continue
            cls = ("t-rule" if t.startswith("rule/")
                   else "t-lore" if t.startswith("lore/")
                   else "t-adv" if t == "adversary"
                   else "t-scope" if t.startswith(("setting/", "era/", "floor/"))
                   else "")
            chips.append(f'<span class="chip {cls}">{html.escape(t)}</span>')
    sources = re.search(r"^sources:\s*\[(.*)\]", fm, re.MULTILINE)
    if sources:
        for entry in re.findall(r'"([^"]+)"', sources.group(1)):
            slug, _, pages = entry.partition(":")
            book = idx.book_titles.get(slug.strip(), slug.strip())
            chips.append(f'<span class="chip src">{html.escape(book)} '
                         f'p. {html.escape(pages.strip())}</span>')
    return f'<div class="meta">{"".join(chips)}</div>' if chips else "", body


def home_page() -> str:
    items = []
    for name in system_names():
        idx = index_for(name)
        count = sum(1 for p in idx.texts if p.parent.name == "notes")
        items.append(f'<li><a class="kbd" href="/{quote(name)}/">'
                     f'{html.escape(display_name(name))}</a>'
                     f'<div class="muted">{name} · {count} notes</div></li>')
    return page("rpg-repl", "<h1>Systems</h1><ul class='cards'>" + "".join(items) + "</ul>")


def system_page(system: str) -> str:
    idx = index_for(system)
    mocs = sorted(p.stem for p in idx.texts if p.parent.name == "_index")
    sources = sorted(p.stem for p in idx.texts if p.parent.name == "_sources")
    count = sum(1 for p in idx.texts if p.parent.name == "notes")
    body = [f"<h1>{html.escape(display_name(system))}</h1>",
            f'<p class="muted">{count} notes · {len(sources)} source book(s)</p>',
            "<h2>Chapters</h2><ul class='cards'>"]
    body += [f'<li><a href="{note_url(system, m)}">{html.escape(m)}</a></li>' for m in mocs]
    body.append("</ul><h2>Source books</h2><ul class='cards'>")
    body += [f'<li><a href="{note_url(system, s)}">'
             f'{html.escape(idx.book_titles.get(s, s))}</a></li>' for s in sources]
    body.append("</ul>")
    return page(system, "".join(body), system)


def note_page(system: str, name: str) -> str | None:
    idx = index_for(system)
    path = idx.resolve(name)
    if path is None:
        return None
    text = idx.texts.get(path) or path.read_text(encoding="utf-8")
    chips, body_md = frontmatter_chips(text, idx)
    body = chips + render_markdown_body(body_md, system, idx)
    back = sorted(idx.backlinks.get(str(path), set()))
    if back:
        links = " · ".join(f'<a href="{note_url(system, b)}">{html.escape(b)}</a>'
                           for b in back)
        body += f'<div class="backlinks"><h3>Linked from</h3>{links}</div>'
    return page(path.stem, body, system)


def highlight(line: str, q: str) -> str:
    """HTML-escape a line and wrap query matches in <mark>."""
    out, low, i = [], line.lower(), 0
    while True:
        j = low.find(q, i)
        if j < 0:
            out.append(html.escape(line[i:]))
            break
        out.append(html.escape(line[i:j]))
        out.append(f"<mark>{html.escape(line[j:j + len(q)])}</mark>")
        i = j + len(q)
    return "".join(out)


def search_page(system: str, query: str) -> str:
    idx = index_for(system)
    q = query.lower().strip()
    # tier 0: exact title/alias match; 1: partial title/alias; 2: body only
    tiers: dict[int, list[str]] = {0: [], 1: [], 2: []}
    total = 0
    if q:
        aliases: dict[Path, list[str]] = {}
        for name, path in idx.names.items():
            aliases.setdefault(path, []).append(name)
        scored = []
        for path, text in idx.texts.items():
            if path.parent.name == "_meta":
                continue
            names = aliases.get(path, [path.stem.lower()])
            lines = [l for l in text.splitlines()
                     if q in l.lower() and not l.startswith("---")]
            if any(n == q for n in names):
                tier = 0
            elif any(q in n for n in names):
                tier = 1
            elif lines:
                tier = 2
            else:
                continue
            scored.append((tier, -len(lines), path.stem.lower(), path, lines))
        scored.sort()
        for tier, _, _, path, lines in scored[:60]:
            total += 1
            sample = f'<div class="line">{highlight(lines[0][:160], q)}</div>' if lines else ""
            tiers[tier].append(
                f'<div class="hit"><a class="kbd" href="{note_url(system, path.stem)}">'
                f'{highlight(path.stem, q)}</a>{sample}</div>')

    labels = {0: "Exact title", 1: "Title matches", 2: "Content matches"}
    sections = "".join(
        f'<div class="tierlabel">{labels[t]}</div>' + "".join(tiers[t])
        for t in (0, 1, 2) if tiers[t])
    body = (f"<h1>Search: {html.escape(query)}</h1>"
            f'<p class="muted">{total} result(s)</p>' + sections)
    return page(f"search: {query}", body, system)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        try:
            if not parts:
                return self.ok(home_page())
            system = parts[0]
            if system not in system_names():
                return self.notfound()
            if len(parts) == 1:
                return self.ok(system_page(system))
            if parts[1] == "search":
                query = parse_qs(url.query).get("q", [""])[0]
                return self.ok(search_page(system, query))
            if parts[1] == "note" and len(parts) == 3:
                rendered = note_page(system, parts[2])
                return self.ok(rendered) if rendered else self.notfound()
            return self.notfound()
        except Exception as error:  # keep the server alive on a bad page
            self.send_error(500, str(error))

    def ok(self, body: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def notfound(self):
        self.send_error(404, "no such page")

    def log_message(self, fmt, *args):
        pass  # quiet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving {len(system_names())} system(s) on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
