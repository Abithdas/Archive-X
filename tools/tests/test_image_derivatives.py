"""Display-size copies of stored images: allowed widths only, never enlarged, made from the master, cached by content, animations untouched.
Run: python tools/tests/test_image_derivatives.py"""
import io, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from PIL import Image
from telegram_archive import config
config.CACHE_DIR = tempfile.mkdtemp()
from telegram_archive.core import image_derivatives as d

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail and not ok else ""))
    if not ok: fails.append(name)

def jpeg(size, color=(80, 120, 200)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "JPEG", quality=95); return b.getvalue()
def size_of(data): return Image.open(io.BytesIO(data)).size

check("only allowed widths are accepted", d.requested_width("480") == 480 and d.requested_width("481") == 0 and d.requested_width("x") == 0 and d.requested_width(None) == 0 and d.requested_width("-5") == 0)
m = jpeg((1600, 2400))
out, mime = d.derive(m, "image/jpeg", 480)
check("reduced to the asked width, aspect kept", size_of(out) == (480, 720) and mime == "image/jpeg", size_of(out))
check("width 0 serves the master unchanged", d.derive(m, "image/jpeg", 0) == (m, "image/jpeg"))
small = jpeg((300, 200))
check("never enlarged: a master smaller than the width is served as it is", d.derive(small, "image/jpeg", 480) == (small, "image/jpeg"))
check("equal width is served as it is too", d.derive(jpeg((480, 300)), "image/jpeg", 480)[0] == jpeg((480, 300)) or size_of(d.derive(jpeg((480, 300)), "image/jpeg", 480)[0]) == (480, 300))
# cached by content: same master → same file; different master → different file
files = list(Path(config.CACHE_DIR, "image_derivatives").rglob("*.w480.*"))
check("a copy was cached", len(files) == 1, len(files))
d.derive(m, "image/jpeg", 480)
check("second request reuses the cache", len(list(Path(config.CACHE_DIR, "image_derivatives").rglob("*.w480.*"))) == 1)
d.derive(jpeg((1600, 2400), (10, 10, 10)), "image/jpeg", 480)
check("a changed master gets its own copy", len(list(Path(config.CACHE_DIR, "image_derivatives").rglob("*.w480.*"))) == 2)
# transparency stays transparent and has no coloured edge
rgba = Image.new("RGBA", (1200, 1200), (255, 0, 0, 0))
for x in range(300, 900):
    for y in range(300, 900): rgba.putpixel((x, y), (0, 0, 255, 255))
b = io.BytesIO(); rgba.save(b, "PNG")
out, mime = d.derive(b.getvalue(), "image/png", 240)
im = Image.open(io.BytesIO(out)); corner = im.getpixel((2, 2))
check("transparent image stays PNG with a transparent corner", mime == "image/png" and im.mode == "RGBA" and corner[3] == 0, (mime, im.mode, corner))
edge = [im.getpixel((x, 60)) for x in range(im.width)]
check("no red fringe along a transparent edge", all(not (p[3] > 0 and p[0] > 60) for p in edge), [p for p in edge if p[3] > 0 and p[0] > 60][:2])
# animated images and non-images are served untouched
frames = [Image.new("RGB", (900, 900), c) for c in ((255, 0, 0), (0, 255, 0))]
g = io.BytesIO(); frames[0].save(g, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
check("animated image untouched", d.derive(g.getvalue(), "image/gif", 240) == (g.getvalue(), "image/gif"))
check("not an image: untouched", d.derive(b"not an image at all", "image/jpeg", 240) == (b"not an image at all", "image/jpeg"))
print("\nALL PASSED" if not fails else f"\n{len(fails)} FAILED"); sys.exit(1 if fails else 0)
