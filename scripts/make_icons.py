"""Render the Lantern mark as PNG app icons (same geometry as Logo.tsx).

Run from the repository root: ``python scripts/make_icons.py``.
Writes frontend/public/icons/*.png and frontend/public/favicon.svg.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "public" / "icons"
BRAND_900 = (0x3E, 0x2A, 0x1E, 255)
BRAND_700 = (0x5A, 0x3E, 0x2C, 255)
BRAND_100 = (0xF1, 0xEA, 0xE3, 255)
GREY_300 = (0xE4, 0xE2, 0xDF, 255)
WHITE = (255, 255, 255, 255)

FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 40" width="32" height="40">
<path d="M11 7.5a5 5 0 0 1 10 0" fill="none" stroke="#3E2A1E" stroke-width="2.4" stroke-linecap="round"/>
<path d="M9.5 11.5 12 7.5h8l2.5 4z" fill="#3E2A1E"/>
<rect x="7" y="11.5" width="18" height="20" rx="3" fill="#3E2A1E"/>
<rect x="10" y="14" width="12" height="15" rx="1.5" fill="#FFFFFF"/>
<rect x="15.4" y="14" width="1.2" height="15" fill="#E4E2DF"/>
<path d="M16 18.2c1.9 2.2 3 3.7 3 5.3a3 3 0 0 1-6 0c0-1.1.4-2 1.2-3 .3.9.8 1.4 1.3 1.6-.2-1.3 0-2.6.5-3.9z" fill="#5A3E2C"/>
<rect x="9" y="31.5" width="14" height="3" rx="1" fill="#3E2A1E"/>
<rect x="11" y="34.5" width="10" height="2.5" rx="1" fill="#3E2A1E"/>
</svg>
"""


def draw_lantern(size: int, *, padding: float, background: tuple | None, scale_glyph: float = 1.0) -> Image.Image:
    """Draw at 8x and downsample for smooth edges."""
    s = size * 8
    img = Image.new("RGBA", (s, s), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # glyph box is 32 x 40 units; fit into the square with padding
    box = s * (1 - 2 * padding) * scale_glyph
    unit = box / 40
    ox = (s - 32 * unit) / 2
    oy = (s - 40 * unit) / 2

    def u(x: float, y: float) -> tuple[float, float]:
        return (ox + x * unit, oy + y * unit)

    # handle (arc)
    bbox = [u(11, 2.5), u(21, 12.5)]
    d.arc(bbox, start=180, end=360, fill=BRAND_900, width=max(1, int(2.4 * unit)))
    # cap
    d.polygon([u(9.5, 11.5), u(12, 7.5), u(20, 7.5), u(22.5, 11.5)], fill=BRAND_900)
    # body
    d.rounded_rectangle([u(7, 11.5), u(25, 31.5)], radius=3 * unit, fill=BRAND_900)
    # glass
    d.rounded_rectangle([u(10, 14), u(22, 29)], radius=1.5 * unit, fill=WHITE)
    d.rectangle([u(15.4, 14), u(16.6, 29)], fill=GREY_300)
    # flame (teardrop approximated with an ellipse and a triangle)
    d.ellipse([u(13, 20.5), u(19, 26.5)], fill=BRAND_700)
    d.polygon([u(16, 18), u(18.6, 23.5), u(13.4, 23.5)], fill=BRAND_700)
    # base
    d.rounded_rectangle([u(9, 31.5), u(23, 34.5)], radius=1 * unit, fill=BRAND_900)
    d.rounded_rectangle([u(11, 34.5), u(21, 37)], radius=1 * unit, fill=BRAND_900)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw_lantern(192, padding=0.12, background=WHITE).save(OUT / "icon-192.png")
    draw_lantern(512, padding=0.12, background=WHITE).save(OUT / "icon-512.png")
    # maskable icons keep the glyph inside the safe zone (inner 80%)
    draw_lantern(512, padding=0.2, background=BRAND_100).save(OUT / "maskable-512.png")
    draw_lantern(180, padding=0.12, background=WHITE).save(OUT / "apple-touch-icon.png")
    (ROOT / "frontend" / "public" / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
