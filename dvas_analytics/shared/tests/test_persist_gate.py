"""Routine per-track writes (plan B1, §3 rules): first / better / periodic / final crops,
track rows every PERSIST_TRACK_EVERY_S and at track end, attributes on change or periodic.
0 restores one write per frame.

Run from dvas_analytics/:  python -m pytest shared/tests/test_persist_gate.py -q
"""
import numpy as np

from shared.persist_gate import TrackGate, crop_score, speed_bucket


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def gate(**kw):
    c = Clock()
    return TrackGate(clock=c, **{"track_every_s": 2, "crop_every_s": 1, "attr_every_s": 5, "end_s": 3, **kw}), c


def run(g, c, seconds, fps=20, score=100.0, sig="car"):
    rows = crops = attrs = 0
    reasons = []
    for _ in range(int(seconds * fps)):
        st = g.seen("D", 7, ts=c.t, mission_id="M")
        if g.want_row(st):
            st.track_id = "uuid"
            rows += 1
        why = g.want_crop(st, score)
        if why:
            crops += 1
            reasons.append(why)
        if g.want_attrs(st, sig):
            attrs += 1
        c.t += 1 / fps
    return rows, crops, attrs, reasons


def test_three_seconds_of_one_vehicle():
    g, c = gate()
    rows, crops, attrs, reasons = run(g, c, 3)
    assert rows == 2                      # t=0 and t=2 (the final row comes at track end)
    assert crops == 3 and reasons == ["first", "periodic", "periodic"]
    assert attrs == 1


def test_zero_keeps_one_write_per_frame():
    g, c = gate(track_every_s=0, crop_every_s=0, attr_every_s=0)
    rows, crops, attrs, _ = run(g, c, 1)
    assert rows == crops == attrs == 20


def test_better_crop_is_taken_at_once_and_alerts_always():
    g, c = gate()
    st = g.seen("D", 1)
    assert g.want_crop(st, 100) == "first"
    c.t += 0.1
    assert g.want_crop(st, 110) is None             # not clearly better
    assert g.want_crop(st, 200) == "better"
    assert g.want_crop(st, 50, alert=True) == "alert"


def test_attributes_written_on_change():
    g, c = gate()
    st = g.seen("D", 1)
    assert g.want_attrs(st, ("car", "red", 1))
    c.t += 0.5
    assert not g.want_attrs(st, ("car", "red", 1))
    assert g.want_attrs(st, ("car", "red", 2))      # speed bucket changed
    assert speed_bucket(19.9) == 1 and speed_bucket(None) is None


def test_track_end_and_mission_change_hand_back_state():
    g, c = gate()
    st = g.seen("D", 1, mission_id="M1")
    st.pending = b"last"
    c.t += 2.9
    assert g.ended() == []
    c.t += 0.2
    (key, done), = g.ended()
    assert key == ("D", 1) and done.pending == b"last" and len(g) == 0
    old = g.seen("D", 2, mission_id="M1")
    g.seen("D", 2, mission_id="M2")                  # same tracker id, next mission
    assert [s for _, s in g.ended()] == [old] and len(g) == 1
    assert len(g.ended(force=True)) == 1 and len(g) == 0


def test_crop_score_prefers_large_sharp_uncut_crops():
    rng = np.random.default_rng(0)
    frame = np.full((400, 400, 3), 128, np.uint8)
    frame[100:300, 100:300] = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    sharp_inside = crop_score(frame, (100, 100, 300, 300))
    assert sharp_inside > crop_score(frame, (150, 150, 250, 250))          # larger wins
    assert sharp_inside > crop_score(frame, (0, 100, 200, 300))            # cut by the edge
    flat = np.full((400, 400, 3), 128, np.uint8)
    assert sharp_inside > crop_score(flat, (100, 100, 300, 300))           # sharper wins
    assert crop_score(frame, (300, 300, 300, 300)) == 0.0
