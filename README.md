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

- **Marker** for every paper by default: a layout model over the PDF's own text
  layer, mapped into a typed IR, so figures keep their captions, tables keep
  their spans and come out as clean values, and equations become their own
  blocks with bounding boxes for exact crops. Installed lazily into an
  isolated venv.
- **arXiv HTML (LaTeXML)** is available via `--extractor auto`, which prefers
  it when arXiv has it. It carries more inline math, but LaTeXML leaks raw
  markup into table cells -- a checkmark arrives as
  `${{\color[rgb]{0.25,0.5117,0.4258}\large\checkmark}}$` -- so tables read
  badly. Marker is the default for that reason.
- **Universal resolvers**: arXiv, DOI (Crossref + OpenAlex + Unpaywall +
  Semantic Scholar), OpenReview, PubMed Central, bioRxiv/medRxiv, ACL, PMLR,
  CVF, JMLR, NeurIPS, plus generic Highwire/JSON-LD landing pages.
- **Coverage gate**: extraction fails when source elements (math, figures,
  tables, bibliography, notes) are not accounted for in the IR, unless
  `--allow-incomplete` is passed. The raw source and PDF are always retained.

## Requirements

paperfetch trades install weight for extraction fidelity. Before installing:

| | |
|---|---|
| **uv** | Required. It is the only supported installer for the Marker environment — [install it](https://docs.astral.sh/uv/getting-started/installation/). |
| **An NVIDIA GPU** | Strongly recommended. Marker runs on CPU but takes ~15s/page instead of ~1s. |
| **~3 GB disk** | Marker's isolated venv plus its model weights. |
| **Python 3.10–3.13** | For the Marker venv specifically. paperfetch itself runs on 3.10+. |

There is no lightweight extraction fallback, by design. A text-extraction
library with no layout model cannot produce sections, captions or tables, so
rather than silently returning a structureless dump, paperfetch fails and says
why.

## Install

```bash
pip install paperfetch-tool
pip install "paperfetch-tool[serve]"   # HTTP API
pip install "paperfetch-tool[mcp]"     # official MCP SDK server
```

Marker is not a Python dependency: paperfetch installs it with `uv` into an
isolated venv under `$PAPERFETCH_CACHE/marker_venv` the first time a PDF needs
it, so the multi-gigabyte torch stack never enters your project environment.
Pass `--no-install-marker` to manage that venv yourself.

The first PDF also downloads Marker's model weights (~2 GB), so expect the
first run to be slow and later runs not to be.

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

Each entry in `crops/manifest.json` records its `precision`. Floats extracted
by Marker carry layout bounding boxes and are cropped `exact`; floats from
arXiv HTML have no page geometry, so they are located by searching the PDF for
their caption and cropped `approximate` -- roughly framed, and possibly
clipping or including a neighbour.

## Commands

| Command | Purpose |
|---|---|
| `fetch` | Resolve, download, extract, and store papers |
| `discover` | Search Semantic Scholar |
| `citations` | Walk the citation graph around papers you already hold |
| `zotero` | Read a Zotero library as a list of papers to fetch |
| `refresh-metadata` | Backfill authors/year/venue into bundles that lack them |
| `list` / `inspect` | Browse the library and bundles |
| `reextract` | Re-run extraction with a different backend |
| `export` | BibTeX export (collision-free keys) |
| `clean` | Prune missing index entries and non-bundle directories |
| `prune` | Delete regenerable heavyweight files (page renders, PDFs) |
| `migrate` | Convert legacy flat libraries to bundles |
| `serve` | FastAPI HTTP API |
| `mcp` | MCP server over stdio |
| `install-mcp` | Register the MCP server with Claude Code, Claude Desktop or opencode |

Useful flags:

```bash
paperfetch fetch --url <url> --extractor marker|arxiv_html|auto
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

### Installing the server

```bash
paperfetch install-mcp --client claude-code --library-dir ./library     # ./.mcp.json
paperfetch install-mcp --client claude-code --scope global              # ~/.claude.json
paperfetch install-mcp --client claude-desktop                          # app-wide config
paperfetch install-mcp --client opencode                                # ./opencode.json
```

Existing servers in the config are preserved. Claude Code and Claude Desktop
use the `mcpServers` schema:

```json
{
  "mcpServers": {
    "paperfetch": {
      "command": "/path/to/paperfetch",
      "args": ["mcp", "--library-dir", "/home/you/papers/library"]
    }
  }
}
```

A skill file is written next to the config for Claude Code
(`.claude/skills/paperfetch/SKILL.md`) and opencode; Claude Desktop loads
skills through its own UI. Restart the client afterwards.

## Building a reading list

The ingestion sources compose: each emits the same manifest CSV that `fetch`
consumes.

```bash
# From a curated markdown list (an "awesome" repo, say)
python scripts/awesome_to_manifest.py awesome.md --section "CVPR 2026" -o papers.csv
paperfetch fetch --manifest papers.csv --library-dir ./library

# From a Zotero collection, read over the local API (no key, nothing leaves the machine)
paperfetch zotero --collection "Anomaly Detection" -o papers.csv

# Then expand outwards: what does this reading list collectively cite?
paperfetch citations --all --direction references --min-seeds 4 -o related.tsv
paperfetch citations --all --min-seeds 8 --format manifest -o foundations.csv
```

`citations` ranks neighbours by how many of your papers reached them, so a work
cited by a third of a reading list rises to the top, and already-held papers
are filtered out.

Set `PAPERFETCH_S2_API_KEY` before bulk runs. Unauthenticated Semantic Scholar
requests share one rate-limited pool and will start failing partway through.

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
uv run ruff check paperfetch_app tests
```

The unit suite covers routing, identity and error handling. It cannot see
whether a table came out with rows in it, so extraction *quality* is measured
against real papers instead:

```bash
uv run python scripts/validate.py            # diff against the recorded baseline
uv run python scripts/validate.py --update   # re-record after an intended change
```

That needs network, a GPU and the Marker venv, so it is a manual/nightly step
rather than part of CI. Every extraction bug found so far passed a fully green
unit run and was visible only here.

## License and responsible use

MIT. paperfetch downloads only open-access sources by default, honors
`robots.txt` for landing pages, and does not circumvent paywalls. If you have
authorized access to a PDF, pass it with `--pdf`. You are responsible for
complying with each publisher's terms of use.
