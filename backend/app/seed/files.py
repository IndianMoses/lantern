"""Generate small synthetic demonstration files (PDF wordings, invoices,
police receipts, photographs) so uploads, downloads and extraction can be
exercised without any real documents."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

_CJK_FONT = "STSong-Light"
_registered = False


def _ensure_fonts() -> None:
    global _registered
    if not _registered:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT))
        except Exception:  # pragma: no cover
            pass
        _registered = True


def _font_for(text: str) -> str:
    return _CJK_FONT if any("一" <= ch <= "鿿" for ch in text) else "Helvetica"


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, width: float) -> list[str]:
    words = text.split(" ") if font == "Helvetica" else list(text)
    joiner = " " if font == "Helvetica" else ""
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + joiner + w) if cur else w
        if c.stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_pdf(path: Path, title: str, paragraphs: list[str], *, header: str | None = None, watermark: str = "DEMONSTRATION DOCUMENT — FICTIONAL") -> Path:
    """Write a simple multi page PDF. Paragraphs starting with '# ' become headings."""
    _ensure_fonts()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    margin = 2 * cm
    y = height - margin

    def new_page() -> None:
        nonlocal y
        c.showPage()
        y = height - margin
        draw_watermark()

    def draw_watermark() -> None:
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillGray(0.55)
        c.drawString(margin, 1.2 * cm, watermark)
        c.restoreState()

    draw_watermark()
    if header:
        c.setFont(_font_for(header), 9)
        c.setFillGray(0.4)
        c.drawString(margin, y, header)
        c.setFillGray(0)
        y -= 0.8 * cm
    tf = _font_for(title)
    c.setFont(tf, 15)
    for line in _wrap(c, title, tf, 15, width - 2 * margin):
        c.drawString(margin, y, line)
        y -= 0.75 * cm
    y -= 0.3 * cm
    for para in paragraphs:
        is_heading = para.startswith("# ")
        text = para[2:] if is_heading else para
        font = _font_for(text)
        size = 11 if is_heading else 9.5
        leading = 0.55 * cm if is_heading else 0.45 * cm
        if is_heading:
            y -= 0.25 * cm
        for line in _wrap(c, text, font, size, width - 2 * margin):
            if y < margin + 1.5 * cm:
                new_page()
            c.setFont(font, size)
            c.drawString(margin, y, line)
            y -= leading
        y -= 0.2 * cm
    c.save()
    return path


def make_png(path: Path, *, width: int = 320, height: int = 200, colour=(188, 206, 222), label_band=(90, 120, 150)) -> Path:
    """Write a valid PNG (flat colour with a darker band) without PIL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for yy in range(height):
        band = 60 <= yy <= 90
        rgb = label_band if band else colour
        row = bytes([0]) + bytes(rgb) * width
        rows.append(row)
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
    return path


def policy_wording_paragraphs(template: dict) -> list[str]:
    paras = [f"Provider: {template['provider']}  |  Product: {template['product']}  |  Type: {template['insurance_type']}"]
    current_section = None
    for cl in template["clauses"]:
        if cl["section"] != current_section:
            current_section = cl["section"]
            paras.append(f"# {current_section}")
        paras.append(f"{cl['ref']}  {cl['title']}. {cl['text']}")
    return paras
