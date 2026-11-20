import sys, tempfile, urllib.request, io
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import photo_look_tags as lt


def test_only_a_clear_winner_becomes_a_tag():
    colour = {t: 0.1 for t, _, _ in lt.LOOKS["Hair colour"]}
    length = {t: 0.1 for t, _, _ in lt.LOOKS["Hair length"]}
    assert lt.choose_tags({"person": 0.9, **colour, **length}, has_face=True) == {}                     # nothing stands out
    colour["hair_red"] = 0.62; colour["hair_blonde"] = 0.2
    length["hair_bob"] = 0.45; length["hair_short"] = 0.30                                              # ahead, but not by enough
    assert set(lt.choose_tags({"person": 0.9, **colour, **length}, has_face=True)) == {"hair_red"}
    length["hair_short"] = 0.2
    assert set(lt.choose_tags({"person": 0.9, **colour, **length}, has_face=True)) == {"hair_red", "hair_bob"}


def test_photos_that_do_not_look_like_a_person_get_nothing_unless_a_face_was_found():
    scores = {"person": 0.2, "hair_red": 0.9, **{t: 0.0 for t, _, _ in lt.LOOKS["Hair length"] if t != "hair_bob"}, "hair_bob": 0.9}
    assert lt.choose_tags(scores) == {}
    assert "hair_red" in lt.choose_tags(scores, has_face=True)


def test_head_crop_leaves_room_for_hair_and_stays_inside_the_picture():
    from PIL import Image
    im = Image.new("RGB", (1000, 800))
    l, t = 100, 50
    crop = lt.head_crop(im, (l, t, 100, 120))
    assert crop.width == 300 and crop.height > 120           # three face-widths wide, taller than the face
    edge = lt.head_crop(im, (0, 0, 100, 100))                 # a face at the corner is clipped, not an error
    assert edge.width >= 8 and edge.height >= 8


def test_look_tags_are_stored_replaced_and_counted_without_touching_other_tags(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for i in (1, 2):
            db.conn.execute("INSERT INTO photo_library_items(id,channel_id,message_id,filename,created_at,updated_at) VALUES(?,?,?,?,?,?)", (i, "-1", i, f"{i}.jpg", "t", "t"))
        db.conn.execute("INSERT INTO photo_content_tags(photo_id,tag,score,regions) VALUES(1,'feet',0.7,1)")
        db.conn.commit()
    lt.store(db, 1, {"hair_red": {"score": 0.7}, "hair_long": {"score": 0.6}})
    lt.store(db, 2, {})
    lt.store(db, 1, {"hair_blonde": {"score": 0.8}})          # re-run replaces the old looks only
    s = lt.summary(db)
    assert s["analysed_photos"] == 2 and [t["tag"] for t in s["tags"]] == ["hair_blonde"]
    assert s["tags"][0]["group"] == "Hair colour"
    assert db.conn.execute("SELECT COUNT(*) FROM photo_content_tags WHERE tag='feet'").fetchone()[0] == 1


def test_a_download_that_fails_its_checksum_is_not_kept(tmp_path, monkeypatch):
    class Fake(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Fake(b"not the real model" * 100))
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        lt.ensure_models(tmp_path)
    assert not list(tmp_path.glob("*.onnx")) and not list(tmp_path.glob("*.part"))


@pytest.mark.skipif(not (lt.available() and (Path(__file__).resolve().parent / "clip").exists()), reason="needs the model files in tools/tests/clip")
def test_real_model_runs():
    from PIL import Image
    from skimage import data
    m = lt.LookModel(Path(__file__).resolve().parent / "clip")
    sc = m.scores(m.embed(Image.fromarray(data.astronaut())))
    assert abs(sum(sc[t] for t, _, _ in lt.LOOKS["Hair length"]) - 1.0) < 1e-4
