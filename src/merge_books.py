#!/usr/bin/env python3
"""Merge all catalog PDFs into one file with category/book separators."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
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


# Source booklet headers (first BT…ET): page number left (even) or right (odd).
_HEADER_NUM_LEFT = re.compile(
    rb"BT\s*"
    rb"(/F\d+)\s+([\d.]+)\s+Tf\s+([\d.]+)\s+([\d.]+)\s+Td\s*\[\(\d+\)\]\s*TJ"
    rb"(/F\d+\s+[\d.]+\s+Tf\s+)([\d.]+)\s+([\d.]+)\s+Td\s*(\[.*?\])\s*TJ\s*ET",
    re.S,
)
_HEADER_NUM_RIGHT = re.compile(
    rb"BT\s*"
    rb"(/F\d+\s+[\d.]+\s+Tf\s+)([\d.]+)\s+([\d.]+)\s+Td\s*(\[.*?\])\s*TJ"
    rb"(/F\d+)\s+([\d.]+)\s+Tf\s+([\d.]+)\s+([\d.]+)\s+Td\s*\[\(\d+\)\]\s*TJ\s*ET",
    re.S,
)

# Layout constants matching the source booklets (A4).
_TITLE_LEFT_X = 59.776
_NUM_LEFT_X = 59.5
_NUM_RIGHT_EDGE = 535.7
_NUM_CHAR_W = 6.1
_TITLE_AFTER_NUM_PAD = 18.0


def _page_content_bytes(page) -> bytes:
    contents = page.get_contents()
    if contents is None:
        return b""
    if isinstance(contents, list):
        return b"\n".join(part.get_data() for part in contents)
    return contents.get_data()


def _set_page_content_bytes(page, data: bytes) -> None:
    stream = ContentStream(stream=None, pdf=None)
    stream.set_data(data)
    page.replace_contents(stream)


def _parse_source_header(data: bytes) -> dict | None:
    """Extract title + number font info from the first header BT…ET block."""
    m = _HEADER_NUM_LEFT.search(data)
    if m:
        num_x, num_y = float(m.group(3)), float(m.group(4))
        rel_x, rel_y = float(m.group(6)), float(m.group(7))
        return {
            "span": m.span(),
            "num_font": m.group(1),
            "num_size": m.group(2),
            "title_tf": m.group(5),
            "title_arr": m.group(8),
            "y": num_y + rel_y,
            "title_x": num_x + rel_x,  # original centered/rightish title
        }

    m = _HEADER_NUM_RIGHT.search(data)
    if m:
        title_x, title_y = float(m.group(2)), float(m.group(3))
        return {
            "span": m.span(),
            "num_font": m.group(5),
            "num_size": m.group(6),
            "title_tf": m.group(1),
            "title_arr": m.group(4),
            "y": title_y,
            "title_x": title_x,  # original left-aligned title
        }
    return None


def _build_source_header(info: dict, page_num: int) -> bytes:
    """Rebuild header: even → number left + title; odd → title left + number right."""
    num_font = info["num_font"]
    num_size = info["num_size"]
    title_tf = info["title_tf"]
    title_arr = info["title_arr"]
    y = info["y"]
    label = str(page_num).encode("ascii")
    num_w = _NUM_CHAR_W * len(label)

    if page_num % 2 == 0:
        # Verso: number on left, title to its right (never under the number).
        title_x = max(info["title_x"], _NUM_LEFT_X + num_w + _TITLE_AFTER_NUM_PAD)
        # If title was left-aligned originally, push it just past the number.
        if info["title_x"] < 100:
            title_x = _NUM_LEFT_X + num_w + _TITLE_AFTER_NUM_PAD
        title_dx = title_x - _NUM_LEFT_X
        return (
            b"BT\n"
            + num_font
            + b" "
            + num_size
            + f" Tf {_NUM_LEFT_X:.3f} {y:.3f} Td ".encode("ascii")
            + b"[("
            + label
            + b")]TJ"
            + title_tf
            + f"{title_dx:.3f} 0 Td ".encode("ascii")
            + title_arr
            + b"TJ\nET"
        )

    # Recto: full title on left, number on right (right-aligned).
    num_x = _NUM_RIGHT_EDGE - num_w
    num_dx = num_x - _TITLE_LEFT_X
    return (
        b"BT\n"
        + title_tf
        + f"{_TITLE_LEFT_X:.3f} {y:.3f} Td ".encode("ascii")
        + title_arr
        + b"TJ"
        + num_font
        + b" "
        + num_size
        + f" Tf {num_dx:.3f} 0 Td ".encode("ascii")
        + b"[("
        + label
        + b")]TJ\nET"
    )


def renumber_source_header(page, page_num: int) -> bool:
    """Replace per-volume header number with continuous page_num; keep full title."""
    data = _page_content_bytes(page)
    if not data:
        return False
    info = _parse_source_header(data)
    if info is None:
        return False
    start, end = info["span"]
    new_data = data[:start] + _build_source_header(info, page_num) + data[end:]
    _set_page_content_bytes(page, new_data)
    return True


def make_page_number_stamp(
    page_num: int,
    width: float,
    height: float,
    *,
    side: str,
) -> PdfReader:
    """Overlay a continuous page number in a header corner (`left` or `right`)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFillColor(HexColor("#000000"))
    c.setFont("Helvetica", 11)
    label = str(page_num)
    y = height - 57.6
    if side == "left":
        c.drawString(59.5, y, label)
    else:
        c.drawRightString(width - 59.5, y, label)
    c.save()
    return _page_reader(buf)


def stamp_continuous_page_numbers(writer: PdfWriter, source_pages: set[int]) -> None:
    """Renumber the whole volume 1..N with verso/recto header placement."""
    total = len(writer.pages)
    print(f"Stamping continuous page numbers (1–{total}) ...")
    rewritten = 0
    needs_overlay: set[int] = set(range(total))

    for i in sorted(source_pages):
        page_num = i + 1
        if renumber_source_header(writer.pages[i], page_num):
            rewritten += 1
            needs_overlay.discard(i)

    print(f"  rewrote headers on {rewritten} source pages")

    # Cover / TOC / separators / booklet title pages: Helvetica overlay only.
    for i in sorted(needs_overlay):
        page_num = i + 1
        page = writer.pages[i]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        side = "left" if page_num % 2 == 0 else "right"
        stamp = make_page_number_stamp(page_num, width, height, side=side)
        page.merge_page(stamp.pages[0])


def merge(catalog: dict, root: Path, output: Path) -> None:
    writer = PdfWriter()
    outlines: list[tuple[str, int, list[tuple[str, int]]]] = []
    source_pages: set[int] = set()

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
                    if reader.is_encrypted:
                        reader.decrypt("")
                    start = len(writer.pages)
                    writer.append(reader)
                    source_pages.update(range(start, len(writer.pages)))
                    print(f"  [OK] {rel} ({len(reader.pages)} pages)")
                except Exception as e:
                    missing.append(rel)
                    print(f"  [FAIL] {rel}: {e}", file=sys.stderr)

            rank = book.get("rank")
            label = f"({rank}) {book['title']}" if rank else book["title"]
            book_outlines.append((label, book_page))

        outlines.append((cat["category"], cat_page, book_outlines))

    stamp_continuous_page_numbers(writer, source_pages)

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
