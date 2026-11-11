"""Intrusion confirmations in seconds of capture time (v2 plan R6.5).

At 20 fps the state machine must make exactly the decisions the frame-count rule made; at
other rates it keeps the same wall-clock timing; a gap restarts a streak.

Run from dvas_analytics:  python -m pytest intrusion/tests/test_confirm_seconds.py -q
"""
import random

from intrusion.state_machine import IntrusionState as S, TrackIntrusionState


def frame_count_reference(pattern, n_in=3, n_out=3):
    """The pre-R6.5 rule: N consecutive frames."""
    st, cin, cout, out = S.OUTSIDE, 0, 0, []
    for inside in pattern:
        cin, cout = (cin + 1, 0) if inside else (0, cout + 1)
        if st in (S.OUTSIDE, S.EXITED):
            if cin >= 1:
                st = S.ENTERING
        elif st == S.ENTERING:
            st = S.INSIDE if cin >= n_in else S.OUTSIDE if cout >= n_out else st
        elif st in (S.INSIDE, S.DWELLING):
            st = S.EXITING if cout >= n_out else st
        elif st == S.EXITING:
            st = S.EXITED if cout >= n_out else S.INSIDE if cin >= n_in else st
        out.append(st)
    return out


def run(pattern, times, **kw):
    t = TrackIntrusionState(1, min_dwell_time_s=1e9, **kw)    # no DWELLING: compare the confirmations
    return [t.update(p, False, ts, i) for i, (p, ts) in enumerate(zip(pattern, times))]


def test_same_decisions_as_frame_counts_at_20fps_with_jitter():
    rng = random.Random(7)
    for n in (2, 3, 5):
        for _ in range(200):
            pattern = [rng.random() < 0.55 for _ in range(60)]
            times = [1762671000.0 + i * 0.05 + rng.uniform(-0.01, 0.01) for i in range(60)]
            assert run(pattern, times, confirm_inside_frames=n, confirm_outside_frames=n) == \
                frame_count_reference(pattern, n, n)


def test_same_wall_clock_timing_at_10fps():
    # 3 frames at 20 fps = 0.10 s inside; at 10 fps that is reached on the 2nd frame
    t = TrackIntrusionState(1, confirm_inside_frames=3, min_dwell_time_s=1e9)
    assert t.update(True, False, 100.0, 0) == S.ENTERING
    assert t.update(True, False, 100.1, 1) == S.INSIDE


def test_a_gap_restarts_the_streak():
    t = TrackIntrusionState(1, confirm_inside_frames=3, min_dwell_time_s=1e9)
    t.update(True, False, 100.0, 0)
    t.update(True, False, 100.05, 1)
    # 2 s nobody looked (shedding): the streak starts again, no confirmation on this frame
    assert t.update(True, False, 102.05, 2) == S.ENTERING
    assert t.update(True, False, 102.10, 3) == S.ENTERING
    assert t.update(True, False, 102.15, 4) == S.INSIDE


def test_explicit_seconds_win():
    t = TrackIntrusionState(1, confirm_inside_frames=3, confirm_inside_s=1.0, min_dwell_time_s=1e9)
    states = [t.update(True, False, 100.0 + i * 0.05, i) for i in range(22)]
    assert states[19] == S.ENTERING and states[20] == S.INSIDE           # 1.0 s after the first frame
