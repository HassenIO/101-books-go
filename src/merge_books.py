#!/usr/bin/env python3
"""Merge all catalog PDFs into one file with category/book separators."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "books.yaml"
OUTPUT_PATH = ROOT / "output" / "101-go-books.pdf"

PAGE_W, PAGE_H = A4
BG = HexColor("#fbf8f4")
INK = HexColor("#333333")
ACCENT = HexColor("#cc6666")
MUTED = HexColor("#777777")
RULE = HexColor("#ddcccc")


def load_catalog(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _new_page() -> tuple[canvas.Canvas, io.BytesIO]:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFillColor(BG)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    return c, buf


def _page_reader(buf: io.BytesIO) -> PdfReader:
    buf.seek(0)
    return PdfReader(buf)


def make_cover(catalog: dict) -> PdfReader:
    c, buf = _new_page()
    stats = catalog.get("stats") or {}
    c.setFillColor(ACCENT)
    c.rect(0, PAGE_H - 8 * mm, PAGE_W, 8 * mm, fill=1, stroke=0)
    c.rect(0, 0, PAGE_W, 8 * mm, fill=1, stroke=0)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 36)
    c.drawCentredString(PAGE_W / 2, PAGE_H * 0.62, "101 Go Books")

    c.setStrokeColor(RULE)
    c.setLineWidth(1.2)
    c.line(PAGE_W * 0.25, PAGE_H * 0.58, PAGE_W * 0.75, PAGE_H * 0.58)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 14)
    c.drawCentredString(
        PAGE_W / 2,
        PAGE_H * 0.52,
        "Go / Weiqi / Baduk problem booklets",
    )

    lines = [
        f"{stats.get('categories', 3)} categories",
        f"{stats.get('books', '?')} books",
        f"{stats.get('pdfs', '?')} PDF volumes",
    ]
    c.setFillColor(INK)
    c.setFont("Helvetica", 13)
    y = PAGE_H * 0.42
    for line in lines:
        c.drawCentredString(PAGE_W / 2, y, line)
        y -= 20

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 10)
    c.drawCentredString(PAGE_W / 2, 28 * mm, catalog.get("source", "https://101books.github.io/"))
    c.showPage()
    c.save()
    return _page_reader(buf)


def make_toc(catalog: dict) -> PdfReader:
    c, buf = _new_page()
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(25 * mm, PAGE_H - 30 * mm, "Contents")

    c.setStrokeColor(ACCENT)
    c.setLineWidth(2)
    c.line(25 * mm, PAGE_H - 34 * mm, 60 * mm, PAGE_H - 34 * mm)

    y = PAGE_H - 50 * mm
    for cat in catalog["categories"]:
        if y < 30 * mm:
            c.showPage()
            c.setFillColor(BG)
            c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
            y = PAGE_H - 30 * mm

        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(25 * mm, y, cat["category"])
        y -= 8 * mm

        c.setFillColor(INK)
        c.setFont("Helvetica", 10)
        for book in cat["books"]:
            if y < 25 * mm:
                c.showPage()
                c.setFillColor(BG)
                c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
                y = PAGE_H - 30 * mm
                c.setFillColor(INK)
                c.setFont("Helvetica", 10)
            rank = book.get("rank") or ""
            title = book["title"]
            n = len(book.get("pdfs") or [])
            parts = f"  ({n} part{'s' if n != 1 else ''})" if n > 1 else ""
            label = f"({rank}) {title}{parts}" if rank else f"{title}{parts}"
            c.drawString(30 * mm, y, label)
            y -= 5.5 * mm

        y -= 4 * mm

    c.showPage()
    c.save()
    return _page_reader(buf)


def make_category_page(name: str, book_count: int, pdf_count: int) -> PdfReader:
    c, buf = _new_page()
    c.setFillColor(ACCENT)
    c.rect(0, PAGE_H / 2 - 28 * mm, PAGE_W, 56 * mm, fill=1, stroke=0)

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 42)
    c.drawCentredString(PAGE_W / 2, PAGE_H / 2 + 2 * mm, name)

    c.setFont("Helvetica", 12)
    c.drawCentredString(
        PAGE_W / 2,
        PAGE_H / 2 - 14 * mm,
        f"{book_count} books  ·  {pdf_count} volumes",
    )

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    c.drawCentredString(PAGE_W / 2, 30 * mm, "Category")
    c.showPage()
    c.save()
    return _page_reader(buf)


def make_book_page(title: str, rank: str | None, parts: int, category: str) -> PdfReader:
    c, buf = _new_page()

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 11)
    c.drawCentredString(PAGE_W / 2, PAGE_H - 30 * mm, category.upper())

    c.setStrokeColor(RULE)
    c.setLineWidth(0.8)
    c.line(40 * mm, PAGE_H - 36 * mm, PAGE_W - 40 * mm, PAGE_H - 36 * mm)

    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 26)
    # wrap long titles
    max_w = PAGE_W - 50 * mm
    words = title.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, "Helvetica-Bold", 26) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        lines = [title]

    y = PAGE_H * 0.55 + (len(lines) - 1) * 8 * mm
    for line in lines:
        c.drawCentredString(PAGE_W / 2, y, line)
        y -= 16 * mm

    if rank:
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 16)
        c.drawCentredString(PAGE_W / 2, y - 4 * mm, f"Rank: {rank}")
        y -= 14 * mm

    if parts > 1:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 12)
        c.drawCentredString(PAGE_W / 2, y - 4 * mm, f"{parts} parts")

    c.showPage()
    c.save()
    return _page_reader(buf)


def append_reader(writer: PdfWriter, reader: PdfReader) -> int:
    """Append all pages; return number of pages added."""
    start = len(writer.pages)
    writer.append(reader)
    return len(writer.pages) - start


def merge(catalog: dict, root: Path, output: Path) -> None:
    writer = PdfWriter()
    outlines: list[tuple[str, int, list[tuple[str, int]]]] = []

    append_reader(writer, make_cover(catalog))
    cover_page = 0
    toc_start = len(writer.pages)
    append_reader(writer, make_toc(catalog))

    missing: list[str] = []

    for cat in catalog["categories"]:
        books = cat["books"]
        pdf_count = sum(len(b.get("pdfs") or []) for b in books)
        cat_page = len(writer.pages)
        append_reader(writer, make_category_page(cat["category"], len(books), pdf_count))
        book_outlines: list[tuple[str, int]] = []

        for book in books:
            pdfs = book.get("pdfs") or []
            book_page = len(writer.pages)
            append_reader(
                writer,
                make_book_page(
                    book["title"],
                    book.get("rank"),
                    len(pdfs),
                    cat["category"],
                ),
            )

            for pdf in pdfs:
                rel = pdf["file"]
                path = root / rel
                if not path.exists():
                    missing.append(rel)
                    print(f"  [MISS] {rel}", file=sys.stderr)
                    continue
                try:
                    reader = PdfReader(str(path))
                    # drop encrypted-empty edge cases
                    if reader.is_encrypted:
                        reader.decrypt("")
                    writer.append(reader)
                    print(f"  [OK] {rel} ({len(reader.pages)} pages)")
                except Exception as e:
                    missing.append(rel)
                    print(f"  [FAIL] {rel}: {e}", file=sys.stderr)

            rank = book.get("rank")
            label = f"({rank}) {book['title']}" if rank else book["title"]
            book_outlines.append((label, book_page))

        outlines.append((cat["category"], cat_page, book_outlines))

    # Bookmarks / outline
    writer.add_outline_item("Cover", cover_page)
    writer.add_outline_item("Contents", toc_start)
    for cat_name, cat_page, book_items in outlines:
        cat_item = writer.add_outline_item(cat_name, cat_page)
        for label, page in book_items:
            writer.add_outline_item(label, page, parent=cat_item)

    writer.add_metadata(
        {
            "/Title": "101 Go Books",
            "/Author": "101books",
            "/Subject": "Merged go/weiqi/baduk problem booklets",
            "/Creator": "101GoBooks merge_books.py",
        }
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as f:
        writer.write(f)

    print(f"Wrote {output} ({len(writer.pages)} pages)")
    if missing:
        print(f"Warning: {len(missing)} file(s) missing/failed", file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    if not YAML_PATH.exists():
        print(f"ERROR: {YAML_PATH} not found. Run download first.", file=sys.stderr)
        return 1
    catalog = load_catalog(YAML_PATH)
    if not catalog.get("categories"):
        print("ERROR: no categories in books.yaml", file=sys.stderr)
        return 1

    print(f"Merging from {YAML_PATH} ...")
    merge(catalog, ROOT, OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
