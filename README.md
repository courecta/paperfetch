# paperfetch

Academic paper ingestion with structured HTML extraction, metadata enrichment, and BibTeX export.

## Features

- **arXiv HTML extraction** — Parses arXiv's native HTML (faster and more accurate than PDF OCR for math and tables)
- **Metadata enrichment** — Fetches authors, abstract, year, categories, and BibTeX from arXiv API
- **BibTeX export** — Export your entire library or selected papers to `.bib`
- **Paper discovery** — Search Semantic Scholar from the command line
- **Identity deduplication** — SHA-256 fingerprinting prevents duplicate downloads
- **Atomic storage** — Lock-safe index updates with crash-resistant writes
- **Parallel processing** — ThreadPoolExecutor for batch ingestion
- **HTTP API + MCP server** — Agentic integration via FastAPI and Model Context Protocol

## Install

```bash
pip install paperfetch-tool
```

For the HTTP API server:
```bash
pip install paperfetch-tool[serve]
```

## Quick start

```bash
# Fetch a paper (auto-detects arXiv and uses HTML extraction)
paperfetch fetch --url https://arxiv.org/abs/2501.00001

# Fetch from a CSV manifest
paperfetch fetch --manifest papers.csv --out-dir ./project_papers

# Search for papers
paperfetch discover "diffusion models" --limit 10

# Export your library to BibTeX
paperfetch export --all -o references.bib

# Start the API server
paperfetch serve --port 8765
```

## Commands

- `fetch` — Download and extract papers from URLs or manifests
- `discover` — Search Semantic Scholar for papers
- `export` — Export to BibTeX
- `list` — List library entries
- `inspect` — Inspect a single entry
- `reextract` — Re-run extraction
- `clean` — Remove stale entries
- `serve` — Start HTTP API server
- `mcp` — Start MCP server over stdio

## Configuration

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `PAPERFETCH_LIBRARY` | Library storage directory | `~/papers/library` |
| `PAPERFETCH_CACHE` | Cache directory (marker venv, etc.) | `~/.cache/paperfetch` |

### Supported manifest formats

- `.csv` — `url,title,slug` columns
- `.tsv` — tab-separated
- `.json` / `.jsonl` — array of objects
- `.txt` — one URL per line

## Cross-platform notes

- **Linux/macOS**: Fully supported
- **Windows**: Supported via `pathlib`; marker venv uses `Scripts/` directory
- **arXiv papers**: Fast HTML extraction, no heavy dependencies
- **Non-arXiv PDFs**: Requires `marker-pdf` (auto-installed in isolated venv, ~2GB)

## Cache & storage layout

```
~/papers/library/          # $PAPERFETCH_LIBRARY
├── index.json             # Master index of all papers
├── pdfs/                  # Cached PDFs
├── md/                    # Extracted markdown
├── meta/                  # Per-paper metadata JSON
└── locks/                 # Process locks

~/.cache/paperfetch/       # $PAPERFETCH_CACHE
└── marker_venv/           # Isolated marker environment
```

## License

MIT
