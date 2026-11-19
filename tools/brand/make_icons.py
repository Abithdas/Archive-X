"""Builds every icon size from the two master pictures in docs/brand (the mark, and the mark with the name).

    python tools/brand/make_icons.py

Each size is made from the master, never from another size, with Pillow's Lanczos filter (it filters transparent pictures in premultiplied
form, so no dark edge appears). The mark is centred on a square with a margin, because it is not square itself.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "docs" / "brand"
DARK = (35, 40, 51, 255)   # the app's dark background, #232833


def square(mark: Image.Image, size: int, margin: float = 0.06, background=None) -> Image.Image:
    """The mark centred in a size x size picture, `margin` of the side kept free all round."""
    side = max(mark.size)
    inner = max(1, round(size * (1 - 2 * margin)))
    scale = inner / side
    fit = mark.resize((max(1, round(mark.width * scale)), max(1, round(mark.height * scale))), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    out.alpha_composite(fit, ((size - fit.width) // 2, (size - fit.height) // 2))
    return out


def main() -> None:
    mark = Image.open(BRAND / "archive-x-mark.png").convert("RGBA")
    tauri = ROOT / "apps/desktop/src-tauri/icons"
    for name, size in (("32x32.png", 32), ("128x128.png", 128), ("128x128@2x.png", 256), ("icon.png", 512)):
        square(mark, size, 0.04).save(tauri / name, optimize=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [square(mark, s, 0.04) for s in sizes]
    frames[-1].save(tauri / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    for size in (192, 512):                                                             # the web app's icons are opaque: iOS fills transparency with black
        square(mark, size, 0.14, DARK).save(ROOT / f"apps/web/public/icons/icon-{size}.png", optimize=True)
    square(mark, 384, 0.02).save(ROOT / "telegram_archive/web/static/icons/archive-x.png", optimize=True)
    word = Image.open(BRAND / "archive-x-wordmark.png").convert("RGBA")                 # the name is white: it needs a dark ground, so the README banner has one
    banner = Image.new("RGBA", (word.width + 160, word.height + 120), DARK)
    banner.alpha_composite(word, (80, 60))
    mask = Image.new("L", banner.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, banner.width - 1, banner.height - 1), radius=48, fill=255)
    out = Image.new("RGBA", banner.size, (0, 0, 0, 0))
    out.paste(banner, (0, 0), mask)
    out.resize((out.width // 2, out.height // 2), Image.LANCZOS).save(BRAND / "banner.png", optimize=True)


if __name__ == "__main__":
    main()
