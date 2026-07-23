#!/usr/bin/env python3
"""Merge all catalog PDFs into one file with category/book separators."""

from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from pypdf import PageObject, PdfReader, PdfWriter, Transformation
from pypdf.generic import ContentStream
from reportlab.lib import pagesizes as rl_pagesizes
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
YAML_PATH = ROOT / "books.yaml"
OUTPUT_DIR = ROOT / "output"

BG = HexColor("#fbf8f4")
INK = HexColor("#333333")
ACCENT = HexColor("#cc6666")
MUTED = HexColor("#777777")
RULE = HexColor("#ddcccc")

# Source booklets are authored at A4; used to scale header overlays proportionally.
_SOURCE_A4_W, _SOURCE_A4_H = A4

_PAGE_SIZE_MAP: dict[str, tuple[float, float]] = {
    "a3": rl_pagesizes.A3,
    "a4": rl_pagesizes.A4,
    "a5": rl_pagesizes.A5,
    "a6": rl_pagesizes.A6,
    "b5": rl_pagesizes.B5,
    "letter": rl_pagesizes.LETTER,
    "legal": rl_pagesizes.LEGAL,
    "tabloid": rl_pagesizes.ELEVENSEVENTEEN,
    "11x17": rl_pagesizes.ELEVENSEVENTEEN,
    "halfletter": rl_pagesizes.HALF_LETTER,
}


@dataclass(frozen=True)
class PageGeom:
    """Target print page geometry for one output volume."""

    w: float
    h: float
    name: str = "A4"

    @property
    def size(self) -> tuple[float, float]:
        return (self.w, self.h)


def resolve_page_size(spec: object | None) -> PageGeom:
    """Parse outputs.size (default A4). Accepts A4/A5/Letter/… or [w, h] points."""
    if spec is None or spec == "":
        return PageGeom(_SOURCE_A4_W, _SOURCE_A4_H, "A4")
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        return PageGeom(float(spec[0]), float(spec[1]), "custom")
    key = str(spec).strip().lower().replace(" ", "").replace("-", "")
    if key in _PAGE_SIZE_MAP:
        w, h = _PAGE_SIZE_MAP[key]
        return PageGeom(w, h, str(spec).strip().upper())
    raise ValueError(
        f"Unknown page size {spec!r}. "
        f"Try one of: {', '.join(sorted({k.upper() for k in _PAGE_SIZE_MAP}))}"
    )


def load_catalog(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _new_page(geom: PageGeom) -> tuple[canvas.Canvas, io.BytesIO]:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=geom.size)
    c.setFillColor(BG)
    c.rect(0, 0, geom.w, geom.h, fill=1, stroke=0)
    return c, buf


def _page_reader(buf: io.BytesIO) -> PdfReader:
    buf.seek(0)
    return PdfReader(buf)


def fit_page_to_geom(src_page, geom: PageGeom) -> PageObject:
    """Scale and center a source page onto the target print size."""
    sw = float(src_page.mediabox.width)
    sh = float(src_page.mediabox.height)
    if sw <= 0 or sh <= 0:
        return src_page
    scale = min(geom.w / sw, geom.h / sh)
    tx = (geom.w - sw * scale) / 2
    ty = (geom.h - sh * scale) / 2
    dest = PageObject.create_blank_page(width=geom.w, height=geom.h)
    dest.merge_transformed_page(
        src_page,
        Transformation().scale(scale).translate(tx, ty),
    )
    return dest


def slugify(title: str) -> str:
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return s or "output"


def catalog_stats(categories: list[dict]) -> dict:
    books = sum(len(c.get("books") or []) for c in categories)
    pdfs = sum(len(b.get("pdfs") or []) for c in categories for b in c.get("books") or [])
    problems = 0
    ranks: list[str | None] = []
    for cat in categories:
        for book in cat.get("books") or []:
            if book.get("problems") is not None:
                problems += int(book["problems"])
            else:
                problems += sum(
                    int(p["problems"])
                    for p in (book.get("pdfs") or [])
                    if p.get("problems") is not None
                )
            for p in book.get("pdfs") or []:
                ranks.append(p.get("rank"))
    return {
        "categories": len(categories),
        "books": books,
        "pdfs": pdfs,
        "problems": problems,
        "ranks": ranks,
    }


def index_books_by_title(categories: list[dict]) -> dict[str, tuple[str, dict]]:
    """Map book title → (category_name, book_dict)."""
    index: dict[str, tuple[str, dict]] = {}
    for cat in categories:
        for book in cat.get("books") or []:
            title = book["title"]
            if title in index:
                raise ValueError(f"Duplicate book title in catalog: {title!r}")
            index[title] = (cat["category"], book)
    return index


def resolve_output_catalog(full_catalog: dict, output_spec: dict) -> dict:
    """Build a catalog view for one output entry.

    - No ``books`` key (or empty): include every book.
    - ``books``: list of titles resolved against ``categories``.
    """
    categories = full_catalog.get("categories") or []
    titles = output_spec.get("books")
    if not titles:
        filtered = categories
    else:
        index = index_books_by_title(categories)
        missing = [t for t in titles if t not in index]
        if missing:
            raise ValueError(
                "Unknown book title(s) in outputs: "
                + ", ".join(repr(t) for t in missing)
            )
        # Keep category order from catalog; within each category, keep
        # the order titles were listed in the output spec.
        order = {t: i for i, t in enumerate(titles)}
        by_cat: dict[str, list[dict]] = {}
        cat_order: list[str] = []
        for title in titles:
            cat_name, book = index[title]
            if cat_name not in by_cat:
                by_cat[cat_name] = []
                cat_order.append(cat_name)
            by_cat[cat_name].append(book)
        # Re-order categories as they appear in the full catalog
        catalog_cat_order = [c["category"] for c in categories]
        cat_order = [c for c in catalog_cat_order if c in by_cat]
        filtered = []
        for cat_name in cat_order:
            books = sorted(by_cat[cat_name], key=lambda b: order[b["title"]])
            filtered.append({"category": cat_name, "books": books})

    stats = catalog_stats(filtered)
    return {
        "source": full_catalog.get("source", "https://101books.github.io/"),
        "description": full_catalog.get("description", ""),
        "stats": stats,
        "categories": filtered,
        "title": output_spec["title"],
        "cover": output_spec.get("cover") or {},
        "size": output_spec.get("size") or "A4",
        "geom": resolve_page_size(output_spec.get("size")),
    }


def resolve_outputs(full_catalog: dict) -> list[dict]:
    """Return output specs; default to a single all-books volume."""
    outputs = full_catalog.get("outputs")
    if not outputs:
        return [{"title": "All 101 books"}]
    for i, spec in enumerate(outputs):
        if not isinstance(spec, dict) or not spec.get("title"):
            raise ValueError(f"outputs[{i}] must be a mapping with a title")
    return outputs


def _parse_hex_color(value: str | None, default: HexColor | None = None) -> HexColor | None:
    if not value or not isinstance(value, str):
        return default
    s = value.strip()
    if not s.startswith("#"):
        s = "#" + s
    try:
        return HexColor(s)
    except Exception:
        return default


def make_cover(catalog: dict, geom: PageGeom) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=geom.size)
    stats = catalog.get("stats") or {}
    title = catalog.get("title") or "101 Go Books"
    cover_cfg = catalog.get("cover") or {}
    bg = _parse_hex_color(cover_cfg.get("background"), BG)
    fg = _parse_hex_color(cover_cfg.get("foreground"), INK)
    page_w, page_h = geom.w, geom.h
    scale = min(page_w / _SOURCE_A4_W, page_h / _SOURCE_A4_H)

    c.setFillColor(bg)
    c.rect(0, 0, page_w, page_h, fill=1, stroke=0)

    c.setFillColor(fg)
    # Serif title (Times-Bold is built into PDF viewers).
    font_name = "Times-Bold"
    font_size = (32 if len(title) > 28 else 36) * scale
    c.setFont(font_name, font_size)
    max_w = page_w - 40 * mm * scale
    words = title.split()
    lines_t: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, font_name, font_size) <= max_w:
            cur = trial
        else:
            if cur:
                lines_t.append(cur)
            cur = w
    if cur:
        lines_t.append(cur)
    if not lines_t:
        lines_t = [title]

    title_line_gap = font_size * 1.15
    title_block_h = len(lines_t) * title_line_gap
    # Title centered in the upper half of the page.
    title_top = page_h * 0.72 + title_block_h / 2 - font_size * 0.2
    y_title = title_top
    for line in lines_t:
        c.drawCentredString(page_w / 2, y_title, line)
        y_title -= title_line_gap
    title_bottom = y_title + title_line_gap * 0.35

    problems = stats.get("problems")
    if problems is None:
        problems = 0
    rank_range = format_rank_range(stats.get("ranks") or [])

    detail_lines: list[str] = []
    if problems:
        detail_lines.append(f"{problems:,} GO PROBLEMS".replace(",", " "))
    if rank_range:
        detail_lines.append(f"FOR {rank_range.upper()} STUDENTS")

    detail_gap = 22 * scale
    detail_block_h = max(len(detail_lines), 1) * detail_gap
    # Stats lower in the bottom half.
    detail_top = page_h * 0.34 + detail_block_h / 2
    # Separation line midway between title block and stats block.
    rule_y = (title_bottom + detail_top) / 2

    c.setStrokeColor(fg)
    c.setLineWidth(1.2 * scale)
    c.line(page_w * 0.25, rule_y, page_w * 0.75, rule_y)

    c.setFillColor(fg)
    c.setFont("Helvetica", 12 * scale)
    y = detail_top
    for line in detail_lines:
        c.drawCentredString(page_w / 2, y, line)
        y -= detail_gap

    c.showPage()
    c.save()
    return _page_reader(buf)


def _wrap_lines(
    c: canvas.Canvas,
    text: str,
    font: str,
    size: float,
    max_w: float,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


_CJK_FONT_NAME: str | None = None
_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)


def _ensure_cjk_font() -> str | None:
    """Register a system CJK font for original-language subtitles."""
    global _CJK_FONT_NAME
    if _CJK_FONT_NAME is not None:
        return _CJK_FONT_NAME or None
    candidates = [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in candidates:
        if not Path(path).exists():
            continue
        for idx in range(0, 6):
            try:
                name = "CoverCJK"
                pdfmetrics.registerFont(TTFont(name, path, subfontIndex=idx))
                _CJK_FONT_NAME = name
                return name
            except Exception:
                continue
    _CJK_FONT_NAME = ""
    return None


def extract_cover_subtitle(cover_text: str) -> str | None:
    """Original-language line(s) between the English title and Problems."""
    lines = [ln.strip() for ln in cover_text.splitlines() if ln.strip()]
    subs: list[str] = []
    for ln in lines:
        if re.match(r"Problems\s*:", ln, flags=re.I):
            break
        if _CJK_RE.search(ln):
            # Hangul sometimes extracts as spaced jamo — drop gaps.
            if re.search(r"[\u1100-\u11ff\u3130-\u318f]", ln):
                ln = re.sub(r"\s+", "", ln)
            subs.append(ln)
    if not subs:
        return None
    return " ".join(subs)


def make_part_title_page(
    book_title: str,
    part_label: str | None,
    rank: str | None,
    problems: int | None,
    source_url: str | None,
    *,
    background: HexColor,
    foreground: HexColor,
    subtitle: str | None = None,
    geom: PageGeom,
) -> PdfReader:
    """First page of each part: colored top half + white bottom half."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=geom.size)
    page_w, page_h = geom.w, geom.h
    scale = min(page_w / _SOURCE_A4_W, page_h / _SOURCE_A4_H)
    mid = page_h / 2

    # Top half — cover colors
    c.setFillColor(background)
    c.rect(0, mid, page_w, mid, fill=1, stroke=0)

    title_font = "Times-Bold"
    title_size = (28 if len(book_title) > 32 else 32) * scale
    max_w = page_w - 40 * mm * scale
    title_lines = _wrap_lines(c, book_title, title_font, title_size, max_w)

    part_no = None
    if part_label is not None and str(part_label).strip() != "":
        part_no = str(part_label).strip()
        if part_no.lower().startswith("part"):
            part_no = part_no[4:].strip()
        if part_no:
            part_no = f"Part {part_no}"

    cjk_font = _ensure_cjk_font() if subtitle else None
    sub_size = 32 * scale

    line_gap = title_size * 1.15
    block_h = len(title_lines) * line_gap
    if part_no:
        block_h += 18 * scale
    # Title/part stay centered as a block; CJK sits lower, separate from that block.
    y = mid + (mid + block_h) / 2 - title_size
    c.setFillColor(foreground)
    c.setFont(title_font, title_size)
    for line in title_lines:
        c.drawCentredString(page_w / 2, y, line)
        y -= line_gap
    if part_no:
        y -= 4 * scale
        c.setFont("Helvetica", 14 * scale)
        c.drawCentredString(page_w / 2, y, part_no)
        y -= 20 * scale
    if subtitle and cjk_font:
        # Below title/part, with comfortable gap (not flush to the midline).
        y -= 36 * scale
        c.setFont(cjk_font, sub_size)
        size = sub_size
        while size >= 14 * scale and c.stringWidth(subtitle, cjk_font, size) > max_w:
            size -= 1
            c.setFont(cjk_font, size)
        c.drawCentredString(page_w / 2, y, subtitle)

    # Bottom half — white, black type
    c.setFillColor(HexColor("#ffffff"))
    c.rect(0, 0, page_w, mid, fill=1, stroke=0)

    bottom_lines: list[tuple[str, str, float]] = []  # text, font, size
    rank_text = format_rank(rank)
    if problems is not None and rank_text:
        bottom_lines.append(
            (f"{problems} problems for {rank_text} students", "Helvetica", 13 * scale)
        )
    elif problems is not None:
        bottom_lines.append((f"{problems} problems", "Helvetica", 13 * scale))
    elif rank_text:
        bottom_lines.append((f"For {rank_text} students", "Helvetica", 13 * scale))
    bottom_lines.append(("All problems are black to play", "Helvetica", 12 * scale))

    qr_size = 21 * mm * scale if source_url else 0
    row_gap = 22 * scale
    text_h = len(bottom_lines) * row_gap
    # Keep text block centered; place QR lower with extra offset.
    y = (mid + text_h) / 2 - 4 * scale
    c.setFillColor(INK)
    for text, font, size in bottom_lines:
        c.setFont(font, size)
        c.drawCentredString(page_w / 2, y, text)
        y -= row_gap

    if source_url:
        qr_bottom = 36 * mm * scale
        _draw_qr_code(c, source_url, page_w / 2, qr_bottom, qr_size)

    c.showPage()
    c.save()
    return _page_reader(buf)


def _draw_qr_code(
    c: canvas.Canvas,
    data: str,
    center_x: float,
    bottom_y: float,
    size: float,
) -> None:
    """Draw a vector QR code centered at ``center_x``, sitting on ``bottom_y``."""
    import qrcode

    qr = qrcode.QRCode(border=1, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    if n == 0:
        return
    cell = size / n
    x0 = center_x - size / 2
    c.setFillColor(HexColor("#000000"))
    for row_i, row in enumerate(matrix):
        for col_i, dark in enumerate(row):
            if not dark:
                continue
            # matrix row 0 is top of QR
            y = bottom_y + size - (row_i + 1) * cell
            c.rect(x0 + col_i * cell, y, cell + 0.05, cell + 0.05, fill=1, stroke=0)


def _pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return len(reader.pages)


def make_blank_page(geom: PageGeom) -> PdfReader:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=geom.size)
    c.setFillColor(HexColor("#ffffff"))
    c.rect(0, 0, geom.w, geom.h, fill=1, stroke=0)
    c.showPage()
    c.save()
    return _page_reader(buf)


def _iter_existing_parts(
    catalog: dict, root: Path
) -> tuple[list[tuple[dict, dict, dict, Path]], list[str]]:
    """Ordered (cat, book, pdf, path) for PDFs that exist on disk."""
    parts: list[tuple[dict, dict, dict, Path]] = []
    missing: list[str] = []
    for cat in catalog["categories"]:
        for book in cat["books"]:
            for pdf in book.get("pdfs") or []:
                path = root / pdf["file"]
                if not path.exists():
                    missing.append(pdf["file"])
                    continue
                parts.append((cat, book, pdf, path))
    return parts, missing


def plan_pages(catalog: dict, root: Path, toc_pages: int) -> tuple[list[dict], int]:
    """Return TOC entries with 0-based page indices and total page count."""
    # layout: [cover][toc x N][parts…] with a blank page after each part except last
    idx = 1 + toc_pages
    all_parts, missing = _iter_existing_parts(catalog, root)
    n_parts = len(all_parts)

    # page_index per (category, book title, part file) as we walk
    part_pages: dict[tuple[str, str, str], int] = {}
    for i, (cat, book, pdf, path) in enumerate(all_parts):
        part_pages[(cat["category"], book["title"], pdf["file"])] = idx
        idx += _pdf_page_count(path)
        if i < n_parts - 1:
            idx += 1  # blank page before next part

    entries: list[dict] = []
    for cat in catalog["categories"]:
        cat_entry = {
            "kind": "category",
            "title": cat["category"],
            "page_index": 1 + toc_pages,
            "books": [],
        }
        for book in cat["books"]:
            book_entry = {
                "kind": "book",
                "title": book["title"],
                "page_index": 1 + toc_pages,
                "parts": [],
            }
            for pdf in book.get("pdfs") or []:
                key = (cat["category"], book["title"], pdf["file"])
                if key not in part_pages:
                    continue
                book_entry["parts"].append(
                    {
                        "label": pdf.get("part"),
                        "rank": pdf.get("rank"),
                        "page_index": part_pages[key],
                    }
                )
            if book_entry["parts"]:
                book_entry["page_index"] = book_entry["parts"][0]["page_index"]
            cat_entry["books"].append(book_entry)
        for book_entry in cat_entry["books"]:
            if book_entry["parts"]:
                cat_entry["page_index"] = book_entry["parts"][0]["page_index"]
                break
        entries.append(cat_entry)

    if missing:
        print(f"WARNING: missing while planning: {len(missing)} file(s)", file=sys.stderr)

    return entries, idx


def format_rank(rank: str | None) -> str | None:
    """Turn catalog ranks like ``11k`` / ``1d`` into ``11 kyu`` / ``1 dan``."""
    if not rank:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([kd])\s*", str(rank), flags=re.I)
    if not m:
        return str(rank).strip() or None
    n, unit = m.group(1), m.group(2).lower()
    return f"{n} {'kyu' if unit == 'k' else 'dan'}"


def rank_strength(rank: str | None) -> int | None:
    """Comparable strength: higher is stronger (30k … 1k … 1d … 9d)."""
    if not rank:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([kd])\s*", str(rank), flags=re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    # kyu: weaker as number grows → negative. dan: positive.
    return -n if unit == "k" else n


def format_rank_range(ranks: list[str | None]) -> str | None:
    """Weakest–strongest range in Go order, e.g. ``12 kyu – 8 kyu``."""
    keyed: list[tuple[int, str]] = []
    for r in ranks:
        s = rank_strength(r)
        if s is not None and r is not None:
            keyed.append((s, str(r)))
    if not keyed:
        return None
    keyed.sort(key=lambda t: t[0])
    lo, hi = keyed[0][1], keyed[-1][1]
    if lo == hi:
        return format_rank(lo)
    return f"{format_rank(lo)} – {format_rank(hi)}"


def make_toc(
    entries: list[dict], toc_pages: int, geom: PageGeom
) -> tuple[PdfReader, list[dict]]:
    """Build TOC pages with dotted leaders and page numbers.

    Content page numbers start at 1 after cover + TOC (``toc_pages`` TOC sheets).
    Returns the TOC reader and link hit-areas:
    ``{toc_page_index, rect, target_page_index}`` (TOC-local page index).
    """
    links: list[dict] = []
    c, buf = _new_page(geom)
    page_w, page_h = geom.w, geom.h
    scale = min(page_w / _SOURCE_A4_W, page_h / _SOURCE_A4_H)

    left = 25 * mm * scale
    right = page_w - 25 * mm * scale
    page_col = right
    title_max = right - 18 * mm * scale
    # absolute index i → printed page i - toc_pages (content starts at 1)
    def printed_page(abs_index: int) -> int:
        return abs_index - toc_pages

    def new_toc_page(first: bool) -> float:
        if not first:
            c.showPage()
            c.setFillColor(BG)
            c.rect(0, 0, page_w, page_h, fill=1, stroke=0)
        c.setFillColor(INK)
        if first:
            c.setFont("Helvetica-Bold", 24 * scale)
            c.drawString(left, page_h - 30 * mm * scale, "Contents")
            c.setStrokeColor(ACCENT)
            c.setLineWidth(2 * scale)
            c.line(left, page_h - 34 * mm * scale, left + 35 * mm * scale, page_h - 34 * mm * scale)
            return page_h - 50 * mm * scale
        c.setFont("Helvetica-Bold", 12 * scale)
        c.setFillColor(MUTED)
        c.drawString(left, page_h - 22 * mm * scale, "Contents")
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6 * scale)
        c.line(left, page_h - 26 * mm * scale, right, page_h - 26 * mm * scale)
        return page_h - 38 * mm * scale

    def ensure_space(y: float, need: float, toc_page: int) -> tuple[float, int]:
        if y < need:
            toc_page += 1
            y = new_toc_page(first=False)
        return y, toc_page

    def draw_leader(y: float, text_end: float, page_label: str) -> None:
        c.setFont("Helvetica", 10 * scale)
        num_w = c.stringWidth(page_label, "Helvetica", 10 * scale)
        num_x = page_col - num_w
        line_x0 = text_end + 4 * scale
        line_x1 = num_x - 5 * scale
        if line_x1 > line_x0 + 8 * scale:
            c.setStrokeColor(RULE)
            c.setLineWidth(0.6 * scale)
            c.setDash(1 * scale, 3 * scale)
            c.line(line_x0, y + 3 * scale, line_x1, y + 3 * scale)
            c.setDash()
        c.setFillColor(INK)
        c.setFont("Helvetica", 10 * scale)
        c.drawString(num_x, y, page_label)

    def draw_entry(
        y: float,
        toc_page: int,
        main_text: str,
        rank_text: str | None,
        target: int,
    ) -> tuple[float, int]:
        """One TOC row: main title (+ optional gray rank) … page number."""
        y, toc_page = ensure_space(y, 20 * mm * scale, toc_page)
        main_font, main_size = "Helvetica", 10 * scale
        rank_font, rank_size = "Helvetica", 8 * scale
        c.setFont(main_font, main_size)
        max_main = title_max - left - 5 * mm * scale
        if rank_text:
            max_main -= c.stringWidth(f"  {rank_text}", rank_font, rank_size) + 4 * scale
        label = main_text
        while label and c.stringWidth(label, main_font, main_size) > max_main:
            label = label[:-2] + "…"

        c.setFillColor(INK)
        x = left
        c.drawString(x, y, label)
        text_end = x + c.stringWidth(label, main_font, main_size)
        if rank_text:
            gap = 5 * scale
            c.setFillColor(MUTED)
            c.setFont(rank_font, rank_size)
            # Align rank near the main baseline.
            c.drawString(text_end + gap, y + 0.5 * scale, rank_text)
            text_end = text_end + gap + c.stringWidth(rank_text, rank_font, rank_size)

        page_label = str(printed_page(target))
        draw_leader(y, text_end, page_label)
        links.append(
            {
                "toc_page": toc_page,
                "rect": (left, y - 2 * scale, right, y + 11 * scale),
                "target": target,
            }
        )
        return y - 5.8 * mm * scale, toc_page

    toc_page = 0
    y = new_toc_page(first=True)

    for cat in entries:
        for book in cat["books"]:
            parts = book.get("parts") or []
            title = book["title"]
            multi = len(parts) > 1

            if multi:
                for part in parts:
                    raw = part.get("label")
                    if raw is None or str(raw).strip() == "":
                        part_no = ""
                    else:
                        part_no = str(raw).strip()
                        if part_no.lower().startswith("part"):
                            part_no = part_no[4:].strip()
                    main = f"{title}: {part_no}" if part_no else f"{title}:"
                    rank_text = format_rank(part.get("rank"))
                    y, toc_page = draw_entry(
                        y, toc_page, main, rank_text, part["page_index"]
                    )
            elif parts:
                part = parts[0]
                rank_text = format_rank(part.get("rank"))
                y, toc_page = draw_entry(
                    y, toc_page, title, rank_text, part["page_index"]
                )
            else:
                y, toc_page = draw_entry(
                    y, toc_page, title, None, book["page_index"]
                )

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


def append_reader(writer: PdfWriter, reader: PdfReader) -> int:
    """Append all pages; return number of pages added."""
    start = len(writer.pages)
    writer.append(reader)
    return len(writer.pages) - start


# Source booklet headers (first BT…ET): page number left (even) or right (odd).
# Allow optional whitespace inside arrays — pypdf may insert spaces when rewriting.
_HEADER_NUM_LEFT = re.compile(
    rb"BT\s*"
    rb"(/F\d+)\s+([\d.]+)\s+Tf\s+([\d.]+)\s+([\d.]+)\s+Td\s*\[\s*\(\d+\)\s*\]\s*TJ"
    rb"(/F\d+\s+[\d.]+\s+Tf\s+)([\d.]+)\s+([\d.]+)\s+Td\s*(\[.*?\])\s*TJ\s*ET",
    re.S,
)
_HEADER_NUM_RIGHT = re.compile(
    rb"BT\s*"
    rb"(/F\d+\s+[\d.]+\s+Tf\s+)([\d.]+)\s+([\d.]+)\s+Td\s*(\[.*?\])\s*TJ"
    rb"(/F\d+)\s+([\d.]+)\s+Tf\s+([\d.]+)\s+([\d.]+)\s+Td\s*\[\s*\(\d+\)\s*\]\s*TJ\s*ET",
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
    color: HexColor | None = None,
) -> PdfReader:
    """Overlay a continuous page number in a header corner (`left` or `right`)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    sx = width / _SOURCE_A4_W
    sy = height / _SOURCE_A4_H
    c.setFillColor(color if color is not None else HexColor("#000000"))
    c.setFont("Helvetica", 11 * min(sx, sy))
    label = str(page_num)
    y = height - 57.6 * sy
    if side == "left":
        c.drawString(59.5 * sx, y, label)
    else:
        c.drawRightString(width - 59.5 * sx, y, label)
    c.save()
    return _page_reader(buf)


def make_book_tab_stamp(
    width: float,
    height: float,
    *,
    side: str,
    y_bottom: float,
    tab_h: float,
    tab_w: float,
    color: HexColor,
) -> PdfReader:
    """Colored edge tab (thumb index) on the outer margin."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.setFillColor(color)
    x = width - tab_w if side == "right" else 0
    c.rect(x, y_bottom, tab_w, tab_h, fill=1, stroke=0)
    c.save()
    return _page_reader(buf)


def stamp_book_tabs(
    writer: PdfWriter,
    book_ranges: list[tuple[int, int]],
    *,
    front_matter: int,
    color: HexColor,
) -> None:
    """Stamp staggered edge tabs; one vertical slot per book section."""
    if not book_ranges:
        return
    n = len(book_ranges)
    print(f"Stamping book tabs ({n} sections) ...")
    # Sample page size from first content page.
    sample = writer.pages[front_matter] if front_matter < len(writer.pages) else writer.pages[0]
    width = float(sample.mediabox.width)
    height = float(sample.mediabox.height)

    scale = min(width / _SOURCE_A4_W, height / _SOURCE_A4_H)
    margin_top = 36 * mm * scale
    margin_bottom = 36 * mm * scale
    usable = height - margin_top - margin_bottom
    slot = usable / n
    tab_h = min(30 * mm * scale, max(14 * mm * scale, slot * 0.78))
    tab_w = 4.5 * mm * scale

    for bi, (start, end) in enumerate(book_ranges):
        # Top → bottom stacking.
        y_top = height - margin_top - bi * slot
        y_bottom = y_top - tab_h
        # Keep tab inside usable band if slot is large.
        if y_bottom < margin_bottom:
            y_bottom = margin_bottom
            y_top = y_bottom + tab_h
        for i in range(start, end):
            if i < front_matter or i >= len(writer.pages):
                continue
            page_num = i - front_matter + 1  # printed content number
            # Outer edge: recto (odd) right, verso (even) left.
            side = "right" if page_num % 2 == 1 else "left"
            page = writer.pages[i]
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            stamp = make_book_tab_stamp(
                w, h, side=side, y_bottom=y_bottom, tab_h=tab_h, tab_w=tab_w, color=color
            )
            page.merge_page(stamp.pages[0])


def stamp_continuous_page_numbers(
    writer: PdfWriter,
    source_pages: set[int],
    *,
    front_matter: int,
    title_pages: set[int] | None = None,
    title_number_color: HexColor | None = None,
    already_renumbered: set[int] | None = None,
) -> None:
    """Renumber content pages 1..N; leave cover/TOC unnumbered."""
    total = len(writer.pages)
    content_count = max(0, total - front_matter)
    print(f"Stamping continuous page numbers (1–{content_count}) ...")
    rewritten = 0
    title_pages = title_pages or set()
    already_renumbered = already_renumbered or set()
    # Only content pages may receive numbers.
    needs_overlay: set[int] = set(range(front_matter, total)) - already_renumbered

    for i in sorted(source_pages):
        if i < front_matter or i in title_pages or i in already_renumbered:
            continue
        page_num = i - front_matter + 1
        if renumber_source_header(writer.pages[i], page_num):
            rewritten += 1
            needs_overlay.discard(i)

    print(f"  rewrote headers on {rewritten + len(already_renumbered)} source pages")

    # Title pages + any leftover pages without a rewritable header.
    # Never overlay pages already renumbered in-stream (avoids double numbers).
    for i in sorted(needs_overlay):
        page_num = i - front_matter + 1
        page = writer.pages[i]
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        side = "left" if page_num % 2 == 0 else "right"
        color = title_number_color if i in title_pages else None
        stamp = make_page_number_stamp(
            page_num, width, height, side=side, color=color
        )
        page.merge_page(stamp.pages[0])


def merge_one(catalog: dict, root: Path, output: Path) -> list[str]:
    """Merge one catalog view to ``output``. Returns missing file paths."""
    writer = PdfWriter()
    outlines: list[tuple[str, int, list[tuple[str, int]]]] = []
    source_pages: set[int] = set()
    title_pages: set[int] = set()
    already_renumbered: set[int] = set()
    volume_title = catalog.get("title") or "101 Go Books"
    cover_cfg = catalog.get("cover") or {}
    part_bg = _parse_hex_color(cover_cfg.get("background"), ACCENT) or ACCENT
    part_fg = _parse_hex_color(cover_cfg.get("foreground"), HexColor("#ffffff")) or HexColor(
        "#ffffff"
    )
    geom: PageGeom = catalog.get("geom") or resolve_page_size(catalog.get("size"))
    print(f"  Page size: {geom.name} ({geom.w:.1f}×{geom.h:.1f} pt)")

    # Two-pass plan so TOC can show real continuous page numbers.
    toc_pages = 1
    entries: list[dict] = []
    for _ in range(5):
        entries, _total = plan_pages(catalog, root, toc_pages)
        toc_reader, toc_links = make_toc(entries, toc_pages, geom)
        actual_toc_pages = len(toc_reader.pages)
        if actual_toc_pages == toc_pages:
            break
        toc_pages = actual_toc_pages
    else:
        entries, _total = plan_pages(catalog, root, toc_pages)
        toc_reader, toc_links = make_toc(entries, toc_pages, geom)
        toc_pages = len(toc_reader.pages)

    n_books = sum(len(c["books"]) for c in entries)
    print(f"  TOC: {toc_pages} page(s), {n_books} books")

    append_reader(writer, make_cover(catalog, geom))
    cover_page = 0
    toc_start = len(writer.pages)
    append_reader(writer, toc_reader)
    front_matter = 1 + toc_pages
    assert len(writer.pages) == front_matter

    missing: list[str] = []
    all_parts, missing_plan = _iter_existing_parts(catalog, root)
    missing.extend(missing_plan)
    n_parts = len(all_parts)

    # Outline bookkeeping keyed like the plan
    outline_books: dict[tuple[str, str], tuple[str, int]] = {}
    outline_cats: dict[str, list[tuple[str, int]]] = {
        cat["category"]: [] for cat in catalog["categories"]
    }
    cat_first_page: dict[str, int] = {}
    # Book sections for edge tabs: (start_page, end_page exclusive)
    book_ranges: list[tuple[int, int]] = []
    range_key: tuple[str, str] | None = None
    range_start = 0

    def _close_book_range(end: int) -> None:
        nonlocal range_key, range_start
        if range_key is not None and end > range_start:
            book_ranges.append((range_start, end))
        range_key = None

    for i, (cat, book, pdf, path) in enumerate(all_parts):
        pdfs = book.get("pdfs") or []
        multi = len(pdfs) > 1
        rel = pdf["file"]
        key = (cat["category"], book["title"])
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                reader.decrypt("")
            start = len(writer.pages)
            if key != range_key:
                _close_book_range(start)
                range_key = key
                range_start = start
            part_url = pdf.get("url")
            subtitle = None
            try:
                cover_text = reader.pages[0].extract_text() or ""
                subtitle = extract_cover_subtitle(cover_text)
            except Exception:
                pass
            title_reader = make_part_title_page(
                book["title"],
                pdf.get("part") if multi else None,
                pdf.get("rank"),
                pdf.get("problems"),
                part_url,
                background=part_bg,
                foreground=part_fg,
                subtitle=subtitle,
                geom=geom,
            )
            writer.add_page(title_reader.pages[0])
            title_pages.add(start)
            for pi in range(1, len(reader.pages)):
                # Renumber on the original A4 stream, then scale to print size.
                # Doing this before fit avoids double numbers (overlay + original).
                src_page = reader.pages[pi]
                abs_idx = len(writer.pages)
                page_num = abs_idx - front_matter + 1
                if renumber_source_header(src_page, page_num):
                    already_renumbered.add(abs_idx)
                writer.add_page(fit_page_to_geom(src_page, geom))
            source_pages.update(range(start, len(writer.pages)))
            print(f"  [OK] {rel} ({len(reader.pages)} pages)")

            if key not in outline_books:
                rank_display = format_rank_range(
                    [p.get("rank") for p in pdfs]
                )
                label = (
                    f"{book['title']} ({rank_display})"
                    if rank_display
                    else book["title"]
                )
                outline_books[key] = (label, start)
                outline_cats[cat["category"]].append((label, start))
                cat_first_page.setdefault(cat["category"], start)
        except Exception as e:
            missing.append(rel)
            print(f"  [FAIL] {rel}: {e}", file=sys.stderr)
            continue

        if i < n_parts - 1:
            writer.add_page(make_blank_page(geom).pages[0])

    _close_book_range(len(writer.pages))

    for cat in catalog["categories"]:
        name = cat["category"]
        if name in cat_first_page:
            outlines.append(
                (name, cat_first_page[name], outline_cats.get(name, []))
            )

    stamp_continuous_page_numbers(
        writer,
        source_pages,
        front_matter=front_matter,
        title_pages=title_pages,
        title_number_color=part_fg,
        already_renumbered=already_renumbered,
    )
    stamp_book_tabs(
        writer,
        book_ranges,
        front_matter=front_matter,
        color=part_bg,
    )
    add_toc_links(writer, toc_start, toc_links)

    writer.add_outline_item("Cover", cover_page)
    writer.add_outline_item("Contents", toc_start)
    for cat_name, cat_page, book_items in outlines:
        cat_item = writer.add_outline_item(cat_name, cat_page)
        for label, page in book_items:
            writer.add_outline_item(label, page, parent=cat_item)

    writer.add_metadata(
        {
            "/Title": volume_title,
            "/Author": "101books",
            "/Subject": "Merged go/weiqi/baduk problem booklets",
            "/Creator": "101GoBooks merge_books.py",
        }
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as f:
        writer.write(f)

    print(f"  Wrote {output} ({len(writer.pages)} pages)")
    return missing


def merge_all(full_catalog: dict, root: Path, output_dir: Path) -> int:
    """Build every volume listed under ``outputs`` (or a default all-books PDF)."""
    try:
        output_specs = resolve_outputs(full_catalog)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    any_missing = False
    for spec in output_specs:
        title = spec["title"]
        print(f"\n=== {title} ===")
        try:
            view = resolve_output_catalog(full_catalog, spec)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        out_path = output_dir / f"{slugify(title)}.pdf"
        missing = merge_one(view, root, out_path)
        if missing:
            any_missing = True

    print(f"\nDone: {len(output_specs)} volume(s) in {output_dir}")
    return 2 if any_missing else 0


def main() -> int:
    if not YAML_PATH.exists():
        print(f"ERROR: {YAML_PATH} not found. Run download first.", file=sys.stderr)
        return 1
    catalog = load_catalog(YAML_PATH)
    if not catalog.get("categories"):
        print("ERROR: no categories in books.yaml", file=sys.stderr)
        return 1

    print(f"Merging from {YAML_PATH} ...")
    return merge_all(catalog, ROOT, OUTPUT_DIR)


if __name__ == "__main__":
    raise SystemExit(main())
