#!/usr/bin/env python3
"""Download all PDFs from https://101books.github.io/ into docs/ and write books.yaml."""

from __future__ import annotations

import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE_URL = "https://101books.github.io/"
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
YAML_PATH = ROOT / "books.yaml"
MAX_WORKERS = 8


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "101GoBooks-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def parse_structure(html: str) -> list[dict]:
    """Parse categories, books, ranks, and PDF parts from the index page."""
    categories: list[dict] = []
    # Page uses: <p>Tesuji:</p><ul>...</ul>
    section_re = re.compile(
        r"<p>\s*(Tesuji|Tsumego|Endgame)\s*:?\s*</p>\s*<ul>(.*?)</ul>",
        re.S | re.I,
    )
    for m in section_re.finditer(html):
        cat_name = m.group(1).strip().capitalize()
        books = _parse_list_items(m.group(2))
        if books:
            categories.append({"category": cat_name, "books": books})
    return categories


def _parse_list_items(html_fragment: str) -> list[dict]:
    items = re.findall(r"<li[^>]*>(.*?)</li>", html_fragment, flags=re.S | re.I)
    books: list[dict] = []
    for item in items:
        rank_m = re.search(r"\((\d+[kd]|[1-9]d)\)", item, re.I)
        rank = rank_m.group(1).lower() if rank_m else None

        links = re.findall(
            r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(.*?)</a>',
            item,
            flags=re.S | re.I,
        )
        if not links:
            continue

        before = item.split("<a", 1)[0]
        title = re.sub(r"<[^>]+>", "", before)
        title = re.sub(r"\([^)]*\)", "", title)
        title = title.replace(":", "").strip()
        title = re.sub(r"\s+", " ", title)

        if not title and links:
            title = re.sub(r"<[^>]+>", "", links[0][1]).strip()

        pdfs = []
        for href, label in links:
            label_clean = re.sub(r"<[^>]+>", "", label).strip()
            filename = href.split("/")[-1]
            url = href if href.startswith("http") else BASE_URL.rstrip("/") + "/" + href.lstrip("/")
            part = None
            if len(links) > 1:
                part = label_clean
            pdfs.append({"part": part, "file": filename, "url": url})

        books.append({"title": title, "rank": rank, "pdfs": pdfs})
    return books


def collect_urls(categories: list[dict]) -> list[tuple[str, Path]]:
    jobs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for cat in categories:
        for book in cat["books"]:
            for pdf in book["pdfs"]:
                url = pdf["url"]
                if url not in seen:
                    seen.add(url)
                    jobs.append((url, DOCS_DIR / pdf["file"]))
    return jobs


def download_one(url: str, dest: Path) -> tuple[str, bool, str]:
    if dest.exists() and dest.stat().st_size > 0:
        return url, True, "skipped (exists)"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "101GoBooks-downloader/1.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return url, True, f"ok ({len(data)} bytes)"
    except Exception as e:
        return url, False, str(e)


def _yaml_escape(s: str) -> str:
    if s == "" or any(c in s for c in ":#{}[]&*!|>'\"%@`") or s.strip() != s or s.lower() in (
        "true",
        "false",
        "null",
        "yes",
        "no",
    ):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def write_yaml(structure: dict, path: Path) -> None:
    lines: list[str] = []
    lines.append(f"source: {structure['source']}")
    lines.append(f"description: {_yaml_escape(structure['description'])}")
    stats = structure["stats"]
    lines.append("stats:")
    for k, v in stats.items():
        lines.append(f"  {k}: {v}")
    lines.append("categories:")
    for cat in structure["categories"]:
        lines.append(f"  - category: {cat['category']}")
        lines.append("    books:")
        for book in cat["books"]:
            lines.append(f"      - title: {_yaml_escape(book['title'])}")
            lines.append(f"        rank: {book['rank']}")
            lines.append("        pdfs:")
            for p in book["pdfs"]:
                if p.get("part") is not None:
                    lines.append(f"          - part: {_yaml_escape(str(p['part']))}")
                    lines.append(f"            file: {p['file']}")
                    lines.append(f"            url: {p['url']}")
                else:
                    lines.append(f"          - file: {p['file']}")
                    lines.append(f"            url: {p['url']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def to_structure(categories: list[dict], ok: int, fail: int, pdf_count: int) -> dict:
    out_cats = []
    for cat in categories:
        books = []
        for book in cat["books"]:
            pdfs = []
            for p in book["pdfs"]:
                entry: dict = {
                    "file": f"docs/{p['file']}",
                    "url": p["url"],
                }
                if p.get("part") is not None:
                    entry = {"part": p["part"], **entry}
                pdfs.append(entry)
            books.append({"title": book["title"], "rank": book["rank"], "pdfs": pdfs})
        out_cats.append({"category": cat["category"], "books": books})
    return {
        "source": BASE_URL,
        "description": "Go/weiqi/baduk problem booklets from 101books",
        "stats": {
            "categories": len(categories),
            "books": sum(len(c["books"]) for c in categories),
            "pdfs": pdf_count,
            "downloaded_ok": ok,
            "downloaded_failed": fail,
        },
        "categories": out_cats,
    }


def main() -> int:
    print(f"Fetching {BASE_URL} ...")
    html = fetch_html(BASE_URL)
    categories = parse_structure(html)
    if not categories:
        print("ERROR: could not parse any categories from the page", file=sys.stderr)
        return 1

    total_books = sum(len(c["books"]) for c in categories)
    jobs = collect_urls(categories)
    print(f"Found {len(categories)} categories, {total_books} books, {len(jobs)} PDFs")

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(download_one, url, dest): url for url, dest in jobs}
        for fut in as_completed(futures):
            url, success, msg = fut.result()
            name = url.rsplit("/", 1)[-1]
            status = "OK" if success else "FAIL"
            print(f"  [{status}] {name}: {msg}")
            if success:
                ok += 1
            else:
                fail += 1

    structure = to_structure(categories, ok, fail, len(jobs))
    write_yaml(structure, YAML_PATH)
    print(f"Wrote {YAML_PATH}")
    print(f"Done: {ok} ok, {fail} failed")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
