"""Our own runner for the content-region detector: how the picture is prepared, how the model's numbers become boxes, and how overlaps are thinned."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from telegram_archive.engines import content_detector as cd  # noqa: E402

SIZE = 320


def output(*candidates, classes=18):
    """A model output (1, 4+classes, n) holding the given (cx, cy, w, h, class, score) candidates and nothing else."""
    out = np.zeros((1, 4 + classes, max(len(candidates), 1)), dtype=np.float32)
    for k, (cx, cy, w, h, cls, score) in enumerate(candidates):
        out[0, :4, k] = (cx, cy, w, h)
        out[0, 4 + cls, k] = score
    return out


def test_the_picture_sits_top_left_on_a_black_square_and_is_scaled_to_the_model():
    im = Image.new("RGB", (200, 100), (255, 0, 0))
    t, original, side = cd.letterbox(im, SIZE)
    assert t.shape == (1, 3, SIZE, SIZE) and original == (200, 100) and side == 200
    assert t.dtype == np.float32 and 0.0 <= float(t.min()) and float(t.max()) <= 1.0
    assert t[0, 0, 10, 10] == 1.0 and t[0, 1, 10, 10] == 0.0                      # red, in RGB order
    assert float(t[0, :, SIZE - 20:, :].max()) == 0.0                               # the bottom is the padding: black
    assert float(t[0, 0, : SIZE // 2 - 20, :].min()) == 1.0                         # the top half is the picture


def test_a_candidate_becomes_a_box_in_the_original_pictures_pixels():
    # 400 x 200 picture, so side 400 and every model pixel is 1.25 picture pixels
    d = cd.decode(output((160, 80, 40, 20, 3, 0.9)), cd.FALLBACK_LABELS, SIZE, (400, 200), 400)
    assert d == [{"class": "FEMALE_BREAST_EXPOSED", "score": pytest.approx(0.9), "box": [175, 87, 50, 25]}]


def test_weak_candidates_are_dropped_and_overlapping_ones_leave_the_best():
    weak = (100, 100, 40, 40, 2, 0.15)                                  # under the candidate floor
    best = (100, 100, 40, 40, 2, 0.8)
    shadow = (102, 101, 40, 40, 7, 0.6)                                 # the same place, another class, lower score
    elsewhere = (250, 250, 30, 30, 7, 0.55)
    d = cd.decode(output(weak, shadow, best, elsewhere), cd.FALLBACK_LABELS, SIZE, (320, 320), 320)
    assert [(x["class"], round(x["score"], 2)) for x in d] == [("BUTTOCKS_EXPOSED", 0.8), ("FEET_EXPOSED", 0.55)]


def test_a_candidate_outside_the_picture_is_ignored_and_a_box_never_leaves_it():
    outside = (300, 100, 20, 20, 1, 0.9)                                # in the padding of a tall picture (picture is 100 wide of 320)
    d = cd.decode(output(outside), cd.FALLBACK_LABELS, SIZE, (100, 320), 320)
    assert d == []
    edge = cd.decode(output((95, 100, 40, 40, 1, 0.9)), cd.FALLBACK_LABELS, SIZE, (100, 320), 320)
    x, y, w, h = edge[0]["box"]
    assert x + w <= 100 and y + h <= 320


def test_nms_thins_by_overlap_and_keeps_the_highest_score_first():
    boxes = [[0, 0, 10, 10], [1, 1, 10, 10], [50, 50, 10, 10]]
    assert cd.non_max_suppression(boxes, [0.5, 0.9, 0.4]) == [1, 2]
    assert cd.non_max_suppression(boxes, [0.5, 0.9, 0.1]) == [1]       # the third is under the score floor


def test_class_names_come_from_the_model_file_with_the_known_eighteen_as_fallback():
    assert cd.labels_from_metadata({"names": "{0: 'A', 1: 'B'}"}) == ["A", "B"]
    assert cd.labels_from_metadata({}) == cd.FALLBACK_LABELS and cd.labels_from_metadata({"names": "not a dict"}) == cd.FALLBACK_LABELS
    assert len(cd.FALLBACK_LABELS) == 18


@pytest.mark.skipif(not os.environ.get("AX_TEST_CONTENT_MODEL"), reason="set AX_TEST_CONTENT_MODEL to the downloaded 320n.onnx")
def test_the_real_model_loads_and_finds_nothing_in_a_plain_picture(tmp_path):
    det = cd.ContentDetector(os.environ["AX_TEST_CONTENT_MODEL"])
    assert det.size == 320 and det.labels == cd.FALLBACK_LABELS             # the file's own names are the NudeNet ones
    p = tmp_path / "plain.png"
    Image.fromarray(np.tile(np.linspace(0, 255, 320, dtype=np.uint8), (240, 1))[..., None].repeat(3, axis=2)).save(p)
    assert det.detect(str(p)) == []
