"""Opening very large pictures (scans and camera files of 100 megapixels and more) without Pillow refusing them or the machine choking on them.

Pillow's default limit is 89 million pixels (a warning above it, an error above twice that), and decoding a 150-megapixel picture in full takes about half a
gigabyte and many seconds. Everything that only needs a reduced picture (thumbnails, previews, face and look analysis) opens it through `open_reduced`, which for
a JPEG asks the decoder for a 1/2, 1/4 or 1/8 size picture straight away: no full decode at all.
"""
from __future__ import annotations

import warnings

MAX_PIXELS = 700_000_000   # a 26,000 x 26,000 picture; beyond this a file is more likely a trap than a photograph


def allow_large_images() -> None:
    """Raise Pillow's limit once (it is process-wide) and stop it warning about pictures that are merely large."""
    from PIL import Image
    if (Image.MAX_IMAGE_PIXELS or 0) < MAX_PIXELS:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)


def open_reduced(path, box: tuple[int, int]):
    """The picture opened for a use that needs it no larger than about `box`: a JPEG is decoded at the smallest scale that is still at least `box` in both
    directions (usually a fraction of a second, whatever its size), anything else is opened as it is. The caller closes it (use `with`)."""
    from PIL import Image
    allow_large_images()
    image = Image.open(path)
    if (image.format or "").upper() == "JPEG":
        try:
            image.draft("RGB", (max(1, int(box[0])), max(1, int(box[1]))))
        except Exception:      # a decoder that cannot: the picture is simply opened in full
            pass
    return image
