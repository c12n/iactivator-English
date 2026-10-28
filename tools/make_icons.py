"""Render assets/logo.svg's design to icon files: icon.png, icon.icns (macOS), icon.ico (Windows).

Run: .venv/bin/python tools/make_icons.py   (iconutil is needed for .icns, i.e. macOS)
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BG, EDGE, INK, CABLE = "#F6F6F3", "#E2E2DC", "#2E3035", "#9AA3AD"


def draw(size: int, margin: float = 0.0) -> Image.Image:
    """The 84-unit logo drawn at `size` px; `margin` is the empty border as a share of size."""
    ss = 4  # supersample for smooth edges
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = big * margin
    k = (big - 2 * off) / 84

    def p(x, y):
        return (off + x * k, off + y * k)

    def rect(x, y, w, h, r, **kw):
        d.rounded_rectangle([p(x, y), p(x + w, y + h)], radius=r * k, **kw)

    rect(0.5, 0.5, 83, 83, 20, fill=BG, outline=EDGE, width=max(1, round(k)))
    rect(27, 10, 30, 50, 8, fill=INK)
    rect(31, 15, 22, 38, 4, fill=BG)
    d.polygon([p(38, 30), p(42, 24), p(42, 32), p(46, 32), p(42, 40), p(42, 34)], fill=INK)
    w = 4 * k
    d.line([p(42, 60), p(42, 74)], fill=CABLE, width=round(w))
    for y in (60, 74):
        cx, cy = p(42, y)
        d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2], fill=CABLE)
    return img.resize((size, size), Image.LANCZOS)


def main():
    draw(512).save(ASSETS / "icon.png")
    # Windows: full-bleed, several sizes in one .ico
    draw(256).save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # macOS: Apple's grid leaves ~10% transparent margin around the icon body
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            for s in (16, 32, 128, 256, 512):
                draw(s, 0.1).save(iconset / f"icon_{s}x{s}.png")
                draw(s * 2, 0.1).save(iconset / f"icon_{s}x{s}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")], check=True)


if __name__ == "__main__":
    main()
