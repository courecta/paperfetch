# paperfetch

Lossless academic paper ingestion for research agents.

paperfetch turns papers into self-contained **bundles** that preserve what the
paper actually says: the original HTML/PDF source, a structured document IR,
markdown, every figure image, canonical table HTML plus span-expanded CSV, and
per-float PDF crops as visual ground truth. Bundles are indexed in SQLite with
FTS5 and served to LLM agents over MCP or HTTP with context-safe, paginated
tools.

## Why

Most paper-to-markdown tools silently drop tables, equations, and figures.
paperfetch treats those as first-class data and keeps the raw source around, so
an agent can always fall back to the original rendering.

- **arXiv HTML (LaTeXML)** parsed into a typed IR: sections, paragraphs,
  inline/display LaTeX, tables with rowspan/colspan, figures with all panels,
  footnotes, citations, bibliography, algorithms/listings.
- **Marker** (high quality, isolated venv, lazily installed) with **PyMuPDF**
  as a fast CPU fallback for any PDF.
- **Universal resolvers**: arXiv, DOI (Crossref + OpenAlex + Unpaywall +
  Semantic Scholar), OpenReview, PubMed Central, bioRxiv/medRxiv, ACL, PMLR,
  CVF, JMLR, NeurIPS, plus generic Highwire/JSON-LD landing pages.
- **Coverage gate**: extraction fails when source elements (math, figures,
  tables, bibliography, notes) are not accounted for in the IR, unless
  `--allow-incomplete` is passed. The raw source and PDF are always retained.

## Install

```bash
pip install paperfetch-tool
pip install "paperfetch-tool[serve]"   # HTTP API
pip install "paperfetch-tool[mcp]"     # official MCP SDK server
pip install "paperfetch-tool[pdf]"     # PyMuPDF fallback extraction
```

Marker is not a Python dependency: paperfetch installs it lazily into an
isolated venv under `$PAPERFETCH_CACHE/marker_venv` the first time a PDF needs
it (or use `--no-install-marker` / `--prefer-pymupdf`).

## Quick start

```bash
paperfetch fetch --url https://arxiv.org/abs/2501.00001
paperfetch fetch --manifest papers.csv --out-dir ./project
paperfetch discover "diffusion models" --limit 10
paperfetch list
paperfetch inspect --key <key>
paperfetch export --all -o references.bib
```

Fetch into a project so an agent (for example opencode) can read the files and
view figures directly:

```bash
paperfetch fetch --url <url> --out-dir ./papers --project-files both
# papers/<slug>.md, papers/<slug>.pdf, papers/<slug>-figures/*.png
```

## Bundle layout

```
library/<key>/
├── meta.json               # normalized metadata, provenance, coverage
├── source.html             # raw LaTeXML HTML (when available)
├── paper.pdf               # original PDF
├── document.json           # structured IR (sections/floats/equations)
├── paper.md                # GFM render, local figure paths
├── paper.txt               # plain text
├── refs.bib  references.json
├── figures/  figures.json  # every figure image, captioned
├── tables/   tables.json   # canonical HTML + span-expanded CSV
├── crops/    pages/        # PDF visual ground truth crops and page renders
├── checksums.json          # sha256 for every file
└── extraction_report.json  # coverage, assets, resolution, notes
```

`library/index.json` is kept as a portable metadata export; the queryable index
lives in `library.sqlite3` (WAL + FTS5).

## Commands

| Command | Purpose |
|---|---|
| `fetch` | Resolve, download, extract, and store papers |
| `discover` | Search Semantic Scholar |
| `list` / `inspect` | Browse the library and bundles |
| `reextract` | Re-run extraction with a different backend |
| `export` | BibTeX export (collision-free keys) |
| `clean` | Prune missing entries and orphans |
| `migrate` | Convert legacy flat libraries to bundles |
| `serve` | FastAPI HTTP API |
| `mcp` | MCP server over stdio |
| `install-mcp` | Register paperfetch with opencode |

Useful flags:

```bash
paperfetch fetch --url <url> --extractor auto|arxiv_html|marker|pymupdf
paperfetch fetch --url <url> --pdf ./authorized-copy.pdf   # skip downloading
paperfetch fetch --url <url> --min-coverage 0.95 --allow-incomplete
paperfetch fetch --url <url> --no-figures --no-pdf-visual
paperfetch fetch --url <url> --resolve-only                # print resolution plan
```

`--resolve-only` prints which candidates would be tried (open-access PDF,
publisher HTML, etc.) without downloading anything.

## MCP tools

`paperfetch mcp` runs the official MCP Python SDK server (falling back to a
built-in stdio implementation when the SDK is not installed). Tools:

- `paperfetch_search` — Semantic Scholar search
- `paperfetch_fetch` — resolve + ingest URLs
- `paperfetch_outline` — section outline with ids and character counts
- `paperfetch_read` — windowed read (`section`, `offset`, `next_offset`, `max_chars`)
- `paperfetch_figure` — figure image content block(s) + caption
- `paperfetch_table` — canonical HTML (or CSV) + caption (+ crop image)
- `paperfetch_grep` — FTS5 full-text search with snippets
- `paperfetch_annotate` — save quotes/notes on a bundle
- `paperfetch_export` — BibTeX export (collision-free keys)

### opencode

```bash
paperfetch install-mcp --scope project     # writes ./opencode.json + .opencode/skills/paperfetch/SKILL.md
# or
paperfetch install-mcp --scope global
```

The command merges an entry into `opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "paperfetch": {
      "type": "local",
      "command": ["paperfetch", "mcp", "--library-dir", "/home/you/papers/library"],
      "enabled": true
    }
  }
}
```

Restart opencode after installing so the MCP server is loaded.

## HTTP API

`paperfetch serve` exposes the same capabilities:

```
GET  /api/v1/papers                POST /api/v1/fetch
GET  /api/v1/papers/{key}          POST /api/v1/resolve
GET  /api/v1/papers/{key}/markdown POST /api/v1/discover
GET  /api/v1/papers/{key}/outline  GET  /api/v1/export/bibtex
GET  /api/v1/papers/{key}/read     GET  /api/v1/search
GET  /api/v1/papers/{key}/figures/{name}
GET  /api/v1/papers/{key}/tables/{ref}
GET  /api/v1/papers/{key}/annotations
GET  /api/v1/library/stats
```

## Configuration

| Variable | Description | Default |
|---|---|---|
| `PAPERFETCH_LIBRARY` | Library directory | `~/papers/library` |
| `PAPERFETCH_CACHE` | Cache (marker venv, etc.) | `~/.cache/paperfetch` |
| `PAPERFETCH_MAILTO` | Contact address for polite API pools (Unpaywall, OpenAlex) and `User-Agent` | unset |
| `PAPERFETCH_S2_API_KEY` / `S2_API_KEY` | Semantic Scholar API key | unset |
| `PAPERFETCH_RPS` | Global per-host request rate limit | `2` |
| `PAPERFETCH_ALLOW_PRIVATE_URLS` | Allow fetching private/local addresses | unset |

Politeness is built in: per-host token buckets, `Retry-After` handling,
retries only on 429/5xx/timeouts, ETag-free but conditional-safe atomic
downloads, robots checks for landing pages, and PDF magic-byte validation.

## Development

```bash
uv sync --group dev
uv run pytest -m "not network"     # fast, hermetic
uv run pytest -m network           # live arXiv/S2 smoke tests
uv run ruff check paperfetch_app tests
```

## License and responsible use

MIT. paperfetch downloads only open-access sources by default, honors
`robots.txt` for landing pages, and does not circumvent paywalls. If you have
authorized access to a PDF, pass it with `--pdf`. You are responsible for
complying with each publisher's terms of use.
