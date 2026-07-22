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


def _pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return len(reader.pages)


def plan_pages(catalog: dict, root: Path, toc_pages: int) -> tuple[list[dict], int]:
    """Return TOC entries with 0-based page indices and total page count."""
    # layout: [cover][toc x N][categories/books/pdfs...]
    idx = 1 + toc_pages
    entries: list[dict] = []
    missing: list[str] = []

    for cat in catalog["categories"]:
        cat_entry = {
            "kind": "category",
            "title": cat["category"],
            "page_index": idx,
            "books": [],
        }
        idx += 1  # category separator

        for book in cat["books"]:
            pdfs = book.get("pdfs") or []
            book_entry = {
                "kind": "book",
                "title": book["title"],
                "rank": book.get("rank"),
                "parts": len(pdfs),
                "page_index": idx,
            }
            idx += 1  # book separator
            for pdf in pdfs:
                path = root / pdf["file"]
                if not path.exists():
                    missing.append(pdf["file"])
                    continue
                idx += _pdf_page_count(path)
            cat_entry["books"].append(book_entry)

        entries.append(cat_entry)

    if missing:
        print(f"WARNING: missing while planning: {len(missing)} file(s)", file=sys.stderr)

    total = idx
    return entries, total


def make_toc(entries: list[dict]) -> tuple[PdfReader, list[dict]]:
    """Build TOC pages with dotted leaders and page numbers.

    Returns the TOC reader and link hit-areas:
    ``{toc_page_index, rect, target_page_index}`` (TOC-local page index).
    """
    links: list[dict] = []
    c, buf = _new_page()

    left = 25 * mm
    right = PAGE_W - 25 * mm
    page_col = right
    title_max = right - 18 * mm

    def new_toc_page(first: bool) -> float:
        if not first:
            c.showPage()
            c.setFillColor(BG)
            c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        c.setFillColor(INK)
        if first:
            c.setFont("Helvetica-Bold", 24)
            c.drawString(left, PAGE_H - 30 * mm, "Contents")
            c.setStrokeColor(ACCENT)
            c.setLineWidth(2)
            c.line(left, PAGE_H - 34 * mm, left + 35 * mm, PAGE_H - 34 * mm)
            return PAGE_H - 50 * mm
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(MUTED)
        c.drawString(left, PAGE_H - 22 * mm, "Contents")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(left, PAGE_H - 26 * mm, right, PAGE_H - 26 * mm)
        return PAGE_H - 38 * mm

    def ensure_space(y: float, need: float, toc_page: int) -> tuple[float, int]:
        if y < need:
            toc_page += 1
            y = new_toc_page(first=False)
        return y, toc_page

    def draw_leader(y: float, text_end: float, page_label: str) -> None:
        c.setFont("Helvetica", 10)
        num_w = c.stringWidth(page_label, "Helvetica", 10)
        num_x = page_col - num_w
        line_x0 = text_end + 4
        line_x1 = num_x - 5
        if line_x1 > line_x0 + 8:
            c.setStrokeColor(RULE)
            c.setLineWidth(0.6)
            c.setDash(1, 3)
            c.line(line_x0, y + 3, line_x1, y + 3)
            c.setDash()
        c.setFillColor(INK)
        c.setFont("Helvetica", 10)
        c.drawString(num_x, y, page_label)

    toc_page = 0
    y = new_toc_page(first=True)

    for cat in entries:
        y, toc_page = ensure_space(y, 30 * mm, toc_page)

        # Category heading
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 13)
        cat_label = cat["title"]
        c.drawString(left, y, cat_label)
        cat_end = left + c.stringWidth(cat_label, "Helvetica-Bold", 13)
        page_label = str(cat["page_index"] + 1)
        draw_leader(y, cat_end, page_label)

        # hit area for category
        links.append(
            {
                "toc_page": toc_page,
                "rect": (left, y - 2, right, y + 12),
                "target": cat["page_index"],
            }
        )
        y -= 7.5 * mm

        for book in cat["books"]:
            y, toc_page = ensure_space(y, 22 * mm, toc_page)

            rank = book.get("rank") or ""
            title = book["title"]
            parts = book.get("parts") or 0
            suffix = f"  ({parts} parts)" if parts > 1 else ""
            label = f"({rank}) {title}{suffix}" if rank else f"{title}{suffix}"

            c.setFont("Helvetica", 10)
            # truncate if needed
            while label and c.stringWidth(label, "Helvetica", 10) > title_max - left - 5 * mm:
                label = label[:-2] + "…"

            c.setFillColor(INK)
            c.drawString(left + 5 * mm, y, label)
            text_end = left + 5 * mm + c.stringWidth(label, "Helvetica", 10)
            page_label = str(book["page_index"] + 1)
            draw_leader(y, text_end, page_label)

            links.append(
                {
                    "toc_page": toc_page,
                    "rect": (left, y - 2, right, y + 11),
                    "target": book["page_index"],
                }
            )
            y -= 5.8 * mm

        y -= 3.5 * mm

    c.showPage()
    c.save()
    return _page_reader(buf), links


def add_toc_links(writer: PdfWriter, toc_start: int, links: list[dict]) -> None:
    """Attach GoTo link annotations from TOC hit-areas to target pages."""
    from pypdf.annotations import Link

    for link in links:
        toc_idx = toc_start + link["toc_page"]
        target_idx = link["target"]
        if toc_idx >= len(writer.pages) or target_idx >= len(writer.pages):
            continue
        annot = Link(rect=link["rect"], target_page_index=target_idx)
        writer.add_annotation(page_number=toc_idx, annotation=annot)


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

    # Two-pass plan so TOC can show real continuous page numbers.
    toc_pages = 1
    entries: list[dict] = []
    for _ in range(5):
        entries, _total = plan_pages(catalog, root, toc_pages)
        toc_reader, toc_links = make_toc(entries)
        actual_toc_pages = len(toc_reader.pages)
        if actual_toc_pages == toc_pages:
            break
        toc_pages = actual_toc_pages
    else:
        entries, _total = plan_pages(catalog, root, toc_pages)
        toc_reader, toc_links = make_toc(entries)
        toc_pages = len(toc_reader.pages)

    print(f"TOC: {toc_pages} page(s), {sum(len(c['books']) for c in entries)} books")

    append_reader(writer, make_cover(catalog))
    cover_page = 0
    toc_start = len(writer.pages)
    append_reader(writer, toc_reader)
    assert len(writer.pages) == 1 + toc_pages

    missing: list[str] = []

    for cat, cat_plan in zip(catalog["categories"], entries, strict=True):
        books = cat["books"]
        pdf_count = sum(len(b.get("pdfs") or []) for b in books)
        cat_page = len(writer.pages)
        if cat_page != cat_plan["page_index"]:
            print(
                f"WARNING: category {cat['category']} at {cat_page}, planned {cat_plan['page_index']}",
                file=sys.stderr,
            )
        append_reader(writer, make_category_page(cat["category"], len(books), pdf_count))
        book_outlines: list[tuple[str, int]] = []

        for book, book_plan in zip(books, cat_plan["books"], strict=True):
            pdfs = book.get("pdfs") or []
            book_page = len(writer.pages)
            if book_page != book_plan["page_index"]:
                print(
                    f"WARNING: book {book['title']} at {book_page}, planned {book_plan['page_index']}",
                    file=sys.stderr,
                )
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
    add_toc_links(writer, toc_start, toc_links)

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
