"""Shared decoded-frame ring (v2 plan R11.3): what a reader gets is exactly what was written,
and anything doubtful — a slot overwritten or being written, another frame, another JPEG
decoder build, a full /dev/shm — gives None (the service then decodes the JPEG as before).

  python -m pytest shared/tests/test_frame_ring.py -q
"""
import numpy as np
import pytest

from shared import frame_ring as fr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "SHM_RESERVE", 0)
    return str(tmp_path)


def frame(seed, h=36, w=64):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def fields(w, slot, gen, seq):
    return {"ring": w.name, "ring_slot": str(slot), "ring_gen": str(gen), "frame_seq": str(seq)}


def test_a_written_frame_reads_back_identically(root):
    w = fr.RingWriter("sid1", 64, 36, 4, root=root)
    r = fr.RingReader(root)
    for seq in range(1, 9):                                     # wraps the 4 slots twice
        img = frame(seq)
        gen = w.write((seq - 1) % 4, seq, img)
        got = r.read(fields(w, (seq - 1) % 4, gen, seq))
        assert got is not None and np.array_equal(got, img) and got.flags.writeable
    assert r.hits == 8


def test_an_overwritten_slot_is_refused(root):
    w = fr.RingWriter("sid1", 64, 36, 2, root=root)
    r = fr.RingReader(root)
    g1 = w.write(0, 1, frame(1))
    w.write(0, 3, frame(3))                                     # the ring went round
    assert r.read(fields(w, 0, g1, 1)) is None


def test_a_slot_being_written_or_of_another_frame_is_refused(root):
    w = fr.RingWriter("sid1", 64, 36, 2, root=root)
    r = fr.RingReader(root)
    g = w.write(1, 2, frame(2))
    assert r.read(fields(w, 1, g, 5)) is None                   # frame_seq does not match
    assert r.read(fields(w, 1, g + 1, 2)) is None               # odd gen: never complete
    assert r.read(fields(w, 7, g, 2)) is None                   # no such slot
    assert r.read({**fields(w, 1, g, 2), "ring": "../etc.ring"}) is None


def test_a_frame_of_another_size_is_not_written(root):
    w = fr.RingWriter("sid1", 64, 36, 2, root=root)
    assert w.write(0, 1, frame(1, h=40)) is None


def test_another_jpeg_decoder_build_is_not_trusted(root, monkeypatch):
    w = fr.RingWriter("sid1", 64, 36, 2, root=root)
    g = w.write(0, 1, frame(1))
    monkeypatch.setattr(fr, "_build", b"9.9.9|JPEG: other")
    assert fr.RingReader(root).read(fields(w, 0, g, 1)) is None


def test_no_room_refuses_the_ring(root, monkeypatch):
    monkeypatch.setattr(fr, "SHM_RESERVE", 1 << 60)
    with pytest.raises(fr.RingFull):
        fr.RingWriter("sid1", 64, 36, 2, root=root)


def test_names_are_never_reused_and_clear_removes_them(root):
    a = fr.RingWriter("sid1", 64, 36, 2, root=root)
    b = fr.RingWriter("sid1", 64, 36, 2, root=root)
    assert a.name != b.name
    a.close(unlink=False)
    assert fr.clear(root) == 2


def test_the_sampler_takes_the_ring_frame_and_falls_back_to_the_jpeg(root, monkeypatch):
    import cv2
    from shared import frame_sampler as fs
    img = frame(4)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    decoded = cv2.imdecode(jpg, cv2.IMREAD_COLOR)                # what the services decoded before
    w = fr.RingWriter("sid1", 64, 36, 2, root=root)
    gen = w.write(0, 7, decoded)
    reader = fr.RingReader(root)
    monkeypatch.setattr(fs, "RING", reader)
    f = {"frame": jpg.tobytes(), "drone_id": "D", **fields(w, 0, gen, 7)}
    assert np.array_equal(fs.decode_frame(dict(f)), decoded) and reader.hits == 1
    stale = {**f, "ring_gen": str(gen + 2)}                     # slot reused since: decode the JPEG
    assert np.array_equal(fs.decode_frame(stale), decoded) and reader.misses == 1
    monkeypatch.setattr(fs, "RING", None)                       # DVAS_FRAME_RING=0
    assert np.array_equal(fs.decode_frame(dict(f)), decoded) and reader.hits == 1
