import sys, tempfile
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import photo_content_tags as ct


def test_detections_become_plain_tags_and_covered_or_faces_are_ignored():
    dets = [
        {"class": "FEMALE_BREAST_EXPOSED", "score": 0.8, "box": [1, 2, 3, 4]}, {"class": "FEMALE_BREAST_EXPOSED", "score": 0.6, "box": [5, 6, 7, 8]},
        {"class": "BUTTOCKS_EXPOSED", "score": 0.49}, {"class": "BUTTOCKS_COVERED", "score": 0.99}, {"class": "FACE_FEMALE", "score": 0.99},
        {"class": "FEET_EXPOSED", "score": 0.7},
    ]
    assert ct.tags_from_detections(dets) == {"breasts": {"score": 0.8, "regions": 2}, "feet": {"score": 0.7, "regions": 1}}


def test_tags_are_stored_replaced_and_counted(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for i in (1, 2, 3):
            db.conn.execute("INSERT INTO photo_library_items(id,channel_id,message_id,filename,created_at,updated_at) VALUES(?,?,?,?,?,?)", (i, "-1", i, f"{i}.jpg", "t", "t"))
        db.conn.commit()
    ct.store_tags(db, 1, {"breasts": {"score": 0.9, "regions": 2}})
    ct.store_tags(db, 2, {"breasts": {"score": 0.7, "regions": 1}, "feet": {"score": 0.6, "regions": 1}})
    ct.store_tags(db, 3, {})                      # analysed, nothing visible
    ct.store_tags(db, 1, {"belly": {"score": 0.8, "regions": 1}})   # a re-run replaces the old tags
    s = ct.summary(db)
    assert s["total_photos"] == 3 and s["analysed_photos"] == 3
    assert {t["tag"]: t["count"] for t in s["tags"]} == {"breasts": 1, "belly": 1, "feet": 1}
    assert {t["tag"]: t["label"] for t in s["tags"]}["feet"] == "Feet"


@pytest.mark.skipif(not ct.available(), reason="nudenet not installed")
def test_real_detector_runs_and_finds_no_exposed_regions_in_an_ordinary_portrait():
    from nudenet import NudeDetector
    from PIL import Image
    pytest.importorskip("skimage")   # a test-only package; the check is skipped where it is not installed
    from skimage import data
    p = Path(tempfile.mkdtemp()) / "a.jpg"
    Image.fromarray(data.astronaut()).save(p)
    dets = NudeDetector().detect(str(p))
    assert any(d["class"] == "FACE_FEMALE" for d in dets)        # the detector really ran
    assert ct.tags_from_detections(dets) == {}                    # a clothed portrait gets no content tags
