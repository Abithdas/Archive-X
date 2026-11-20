"""Photo thumbnails/previews: never enlarged, width-driven box, no fringe on transparent edges, original untouched.
Run: python tools/tests/test_photo_derivatives.py"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PIL import Image
from telegram_archive.engines.photo_cache import THUMBNAIL_BOX, PREVIEW_BOX, render_jpeg_derivative

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail and not ok else ""))
    if not ok: fails.append(name)

tmp = Path(tempfile.mkdtemp())
# 1. a tall photo is limited by height, a wide one by width, a small one is never enlarged
for name, size, expect in (("tall", (3000, 6000), (480, 960)), ("wide", (6000, 3000), (640, 320)), ("small", (200, 100), (200, 100))):
    src = tmp / f"{name}.png"; Image.new("RGB", size, (90, 120, 160)).save(src)
    out = tmp / f"{name}.jpg"; render_jpeg_derivative(src, out, THUMBNAIL_BOX, 84)
    with Image.open(out) as im: check(f"thumbnail {name} size", im.size == expect, f"{im.size} != {expect}")
    check(f"{name} original untouched", Image.open(src).size == size)
# 2. portrait thumbnails are wider than the old 480x360 box allowed (240 px)
src = tmp / "portrait.png"; Image.new("RGB", (2000, 3000), (200, 50, 50)).save(src)
out = tmp / "portrait.jpg"; render_jpeg_derivative(src, out, THUMBNAIL_BOX, 84)
with Image.open(out) as im: check("portrait thumbnail wider than the old 240 px", im.width >= 600, im.size)
# 3. a transparent image whose invisible pixels are bright red must not leave a red fringe on a dark background
src = tmp / "alpha.png"
im = Image.new("RGBA", (1600, 1600), (255, 0, 0, 0))            # invisible red everywhere
for x in range(400, 1200):
    for y in range(400, 1200): im.putpixel((x, y), (20, 20, 20, 255))   # opaque dark square
im.save(src)
out = tmp / "alpha.jpg"; render_jpeg_derivative(src, out, (400, 400), 90, background=(28, 32, 40))
with Image.open(out) as r:
    r = r.convert("RGB"); w, h = r.size
    # every pixel on the edge ring of the square should be a blend of dark-square and the dark background, never reddish
    worst = max(r.getpixel((x, y))[0] - r.getpixel((x, y))[2] for x in range(w) for y in range(h))
    check("no red fringe from transparent pixels", worst < 40, f"red excess {worst}")
print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED"); sys.exit(1 if fails else 0)
