"""Unit tests for crowd/stream_policy.py (RunPod plan F19/F20). No GPU/torch needed.

Run from dvas_analytics/:  python -m pytest crowd/tests -q
"""
import json

from crowd.stream_policy import FrameRateCap, is_cuda_failure, parse_mission_id


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_rate_cap_allows_first_frame_then_throttles():
    clock = FakeClock()
    cap = FrameRateCap(2.0, clock=clock)        # 0.5 s minimum interval
    assert cap.due("DRN-001")
    cap.mark("DRN-001")
    clock.t += 0.2
    assert not cap.due("DRN-001")               # too soon → ACK without decoding
    assert cap.due("DRN-002")                   # other drones are independent
    clock.t += 0.3
    assert cap.due("DRN-001")


def test_rate_cap_not_consumed_by_failed_frames():
    clock = FakeClock()
    cap = FrameRateCap(1.0, clock=clock)
    assert cap.due("D")                          # frame failed → mark() never called
    clock.t += 0.01
    assert cap.due("D")


def test_rate_cap_disabled_when_fps_not_positive():
    cap = FrameRateCap(0, clock=FakeClock())
    cap.mark("D")
    assert cap.due("D")


def test_parse_mission_id_variants():
    assert parse_mission_id({"metadata": json.dumps({"mission_id": "MSN-1"})}) == "MSN-1"
    assert parse_mission_id({"metadata": json.dumps({"mission_id": "MSN-2"}).encode()}) == "MSN-2"
    assert parse_mission_id({}) == ""
    assert parse_mission_id({"metadata": "{not json"}) == ""       # realistic failure: corrupt metadata
    assert parse_mission_id({"metadata": json.dumps(["x"])}) == ""
    assert parse_mission_id({"metadata": json.dumps({"mission_id": None})}) == ""


def test_is_cuda_failure():
    class FakeOOM(RuntimeError):
        pass
    assert is_cuda_failure(FakeOOM("whatever"), FakeOOM)
    assert is_cuda_failure(RuntimeError("CUDA error: an illegal memory access"))
    assert not is_cuda_failure(RuntimeError("shape mismatch"))
    assert not is_cuda_failure(ValueError("CUDA"))
