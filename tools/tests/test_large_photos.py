"""Very large pictures (100+ megapixels) are reduced while they are read, so a thumbnail or preview of one does not take half a gigabyte or end in an error."""
from PIL import Image

from telegram_archive.core import imaging
from telegram_archive.engines import photo_cache as pc


def jpeg(path, size, exif_orientation=None):
    im = Image.new("RGB", size, (90, 120, 200))
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        im.save(path, quality=60, exif=exif)
    else:
        im.save(path, quality=60)
    return path


def test_a_jpeg_is_decoded_at_a_reduced_size_not_in_full(tmp_path):
    p = jpeg(tmp_path / "a.jpg", (4000, 3000))
    with imaging.open_reduced(p, (640, 640)) as im:
        im.load()
        assert im.size == (1000, 750)                      # 1/4 scale: the smallest that is still at least 640 each way
    with imaging.open_reduced(p, (4000, 3000)) as im:
        im.load()
        assert im.size == (4000, 3000)                     # a use that needs everything gets everything


def test_pillows_limit_is_raised_so_a_150_megapixel_picture_is_not_refused():
    imaging.allow_large_images()
    assert Image.MAX_IMAGE_PIXELS >= 150_000_000 * 2


def test_thumbnail_and_preview_are_the_right_size_whatever_the_original(tmp_path):
    p = jpeg(tmp_path / "b.jpg", (3600, 2400))
    pc.render_jpeg_derivative(p, tmp_path / "t.jpg", pc.THUMBNAIL_BOX, 85)
    pc.render_jpeg_derivative(p, tmp_path / "p.jpg", pc.PREVIEW_BOX, 88)
    assert Image.open(tmp_path / "t.jpg").size == (640, 427)
    assert Image.open(tmp_path / "p.jpg").size == (1440, 960)


def test_a_rotated_picture_keeps_its_orientation_in_the_derivative_and_in_its_size(tmp_path):
    p = jpeg(tmp_path / "c.jpg", (3000, 2000), exif_orientation=6)   # shot sideways: shown 2000 wide and 3000 high
    assert pc._dimensions(p) == (2000, 3000)
    pc.render_jpeg_derivative(p, tmp_path / "t.jpg", pc.THUMBNAIL_BOX, 85)
    assert Image.open(tmp_path / "t.jpg").size == (640, 960)


def test_a_very_large_original_gets_a_larger_finer_preview(tmp_path, monkeypatch):
    p = tmp_path / "d.jpg"
    p.write_bytes(b"x")
    monkeypatch.setattr(pc, "_dimensions", lambda path: (14204, 10652))
    assert pc.preview_box_for(p) == ((2880, 2880), 90)
    monkeypatch.setattr(pc, "_dimensions", lambda path: (4000, 3000))
    assert pc.preview_box_for(p) == (pc.PREVIEW_BOX, pc.PREVIEW_QUALITY)
