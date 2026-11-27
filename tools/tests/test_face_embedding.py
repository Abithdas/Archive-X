"""A face is embedded from the aligned crop and its mirror image, and averaged: the same person groups more reliably, and the vectors stay compatible with the stored ones."""
import numpy as np

from telegram_archive.engines import photo_face_clustering as pfc


class Fake:
    """A recognizer whose answer depends on whether the crop was mirrored (the top-left pixel tells): it has a favourite side."""
    def feature(self, image):
        return np.array([1.0, 0.0, 0.0], dtype=np.float32) if image[0, 0, 0] == 255 else np.array([0.0, 1.0, 0.0], dtype=np.float32)


def crop():
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    img[:, 0, :] = 255          # the left column is bright; mirrored, the right one is
    return img


def test_the_embedding_is_the_normalised_mean_of_the_face_and_its_mirror_image():
    v = pfc.embed_aligned(Fake(), crop())
    assert v is not None and v.shape == (3,)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5
    assert np.allclose(v, [2 ** -0.5, 2 ** -0.5, 0.0], atol=1e-5)


def test_a_model_that_gives_nothing_gives_no_embedding_and_a_failing_mirror_falls_back_to_the_plain_one():
    class Empty:
        def feature(self, image):
            return np.zeros(3, dtype=np.float32)
    assert pfc.embed_aligned(Empty(), crop()) is None

    class MirrorFails:
        def __init__(self): self.n = 0
        def feature(self, image):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("boom")
            return np.array([3.0, 4.0], dtype=np.float32)
    v = pfc.embed_aligned(MirrorFails(), crop())
    assert np.allclose(v, [0.6, 0.8], atol=1e-5)
