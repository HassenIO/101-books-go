# AGENTS.md

Guidance for AI coding agents working on this repository.

## Project

**101 Go Books** downloads free Go/Weiqi/Baduk problem PDFs from https://101books.github.io/ and merges them into one volume with category separators, continuous page numbers, and a clickable TOC.

| Path | Role |
|------|------|
| `src/download_books.py` | Scrape index HTML, download PDFs → `docs/`, write `books.yaml` |
| `src/merge_books.py` | Read `books.yaml`, build one PDF per `outputs` entry → `output/` |
| `books.yaml` | Catalog + `outputs` volume definitions |
| `docs/` | Downloaded PDFs (gitignored; keep `docs/.keep`) |
| `output/` | Merged PDFs (gitignored; keep `output/.keep`) |
| `Makefile` | `make download`, `make merge` |
| `pyproject.toml` / `uv.lock` | Python 3.12+, deps: pypdf, pyyaml, reportlab |

## Setup & commands

```bash
uv sync                          # create .venv and install deps
make download                    # fetch PDFs + regenerate books.yaml
make merge                       # build output/101-go-books.pdf
.venv/bin/python src/<script>.py # run a script directly
```

Prefer `.venv/bin/python` (Makefile does this when `.venv` exists). Do not commit `docs/*.pdf`, `output/*.pdf`, or `.venv/`.

## Architecture notes

### Download (`download_books.py`)

- Fetches the live HTML index; parses `<p>Category:</p><ul>…</ul>` sections (Tesuji / Tsumego / Endgame).
- Concurrent downloads; skips existing non-empty files in `docs/`.
- Writes YAML with a small hand-rolled emitter (no required PyYAML at write time, but PyYAML is a project dep for merge).
- Paths are project-root relative (`ROOT = parent of src/`).

### Merge (`merge_books.py`)

Pipeline order matters:

1. **Plan pages** (two-pass): count PDF pages + separators so TOC page numbers are correct once TOC page count stabilizes.
2. **Cover** → **TOC** (leaders + page numbers + link hit-areas) → **category/book separators** → **source PDFs**.
3. **Continuous renumbering**: rewrite source booklet header content streams (verso number left / recto title left + number right); Helvetica overlay for cover/TOC/separators/title pages.
4. **TOC GoTo annotations** via `pypdf.annotations.Link`.
5. **PDF outline bookmarks** for cover, contents, categories, books.

Do not cover header titles with white rectangles to hide old page numbers — that truncates titles. Rewrite the first header `BT…ET` block instead.

### Catalog contract (`books.yaml`)

```yaml
categories:
  - category: Tesuji|Tsumego|Endgame
    books:
      - title: string
        pdfs:
          - part: "1"      # only when multi-part
            rank: 11k|1d   # from PDF cover Difficulty; per part
            file: docs/….pdf
            url: https://…

outputs:
  - title: string           # → output/<slug>.pdf
    books: [title, …]       # optional; omit/empty = all books
```

- Ranks are **per PDF** (cover page), not the site list rank (wrong for multi-part).
- TOC book lines show a Go-ordered range weak→strong (`12 kyu – 8 kyu`); parts show their own rank.
- `outputs[].books` titles must match `categories[*].books[*].title` exactly.
- Download **preserves** existing `outputs` when rewriting `books.yaml`.
- Merge assumes `file` paths are relative to the repo root and exist after download.

## Conventions

- Python 3.12+, no unnecessary comments.
- Keep scripts runnable as `python src/….py` with `ROOT` = repo root.
- Stdlib + declared deps only (pypdf, pyyaml, reportlab).
- Match existing naming: `make download` / `make merge`, outputs under `output/<slug>.pdf`.
- Do not commit generated binaries (PDFs under `docs/` / `output/`).
- Do not invent new top-level commands without updating the Makefile and README.

## Verification

After changing download or merge logic:

```bash
make download          # if catalog/HTML parsing changed
make merge             # always after merge_books.py changes
```

Spot-check the merged PDF:

- TOC page numbers match category/book separator pages (1-based continuous numbers).
- Odd pages: full title on the left, page number on the right.
- Even pages: page number on the left, title not overlapping the number.
- TOC entries are clickable; bookmarks outline is present.

Optional visual check with PyMuPDF (dev-only, not a project dep):

```bash
uv pip install pymupdf
.venv/bin/python -c "import fitz; d=fitz.open('output/101-go-books.pdf'); print(len(d), d[1].get_text()[:400])"
```

## What not to do

- Do not hardcode the full book list; scrape the live site or read `books.yaml`.
- Do not reintroduce header whiteout overlays that clip titles.
- Do not force-push, amend others’ commits, or commit secrets.
- Do not add heavy deps unless needed; prefer small scripts over frameworks.
- Source PDFs are third-party content from 101books — do not claim ownership or strip attribution from cover/TOC source links.

## Quick change map

| Goal | Touch |
|------|--------|
| Site HTML structure changed | `src/download_books.py` parsers |
| Catalog fields / YAML shape | `download_books.py` + `merge_books.py` consumers |
| Cover / TOC / separators look | `make_*` helpers in `merge_books.py` |
| Page numbering / headers | `renumber_source_header`, `stamp_continuous_page_numbers` |
| TOC links | `make_toc` link list + `add_toc_links` |
| Multiple output volumes | `outputs` in `books.yaml`; `resolve_output_catalog` / `merge_all` |
| New make target | `Makefile` + `README.md` |
