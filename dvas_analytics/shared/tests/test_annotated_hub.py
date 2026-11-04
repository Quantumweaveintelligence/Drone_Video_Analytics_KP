"""Live annotated video hub (MJPEG): display size, scaling helpers, stream framing.

Run from dvas_analytics/:  python -m pytest shared/tests/test_annotated_hub.py -q
"""
import asyncio

import cv2
import numpy as np

from shared import annotated_hub as ah


def _frame(w, h):
    img = np.zeros((h, w, 3), np.uint8)
    cv2.rectangle(img, (10, 10), (w // 2, h // 2), (0, 255, 0), 3)
    return img


def test_display_jpeg_downscales_4k_to_1920_and_keeps_small_frames():
    jpeg = ah.display_jpeg(_frame(3840, 2160))
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (1080, 1920)
    small = ah.display_jpeg(_frame(1280, 720))
    assert cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (720, 1280)


def test_display_from_jpeg_reads_width_from_header():
    full = cv2.imencode(".jpg", _frame(3840, 2160))[1].tobytes()
    assert ah._jpeg_width(full) == 3840
    out = ah.display_from_jpeg(full)
    assert cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR).shape[1] == 1920
    narrow = cv2.imencode(".jpg", _frame(1280, 720))[1].tobytes()
    assert ah.display_from_jpeg(narrow) is narrow          # untouched, no re-encode


def test_to_display_and_scale_items():
    small, f = ah.to_display(_frame(3840, 2160))
    assert small.shape[1] == 1920 and f == 0.5
    items = [{"bbox": [100, 200, 300, 400], "id": 1}, {"bbox": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}}]
    out = ah.scale_items(items, ("bbox",), f)
    assert out[0]["bbox"] == [50, 100, 150, 200] and out[0]["id"] == 1
    assert out[1]["bbox"] == {"x1": 5, "y1": 10, "x2": 15, "y2": 20}
    assert items[0]["bbox"] == [100, 200, 300, 400]          # originals untouched
    same, f1 = ah.to_display(_frame(1280, 720))
    assert f1 == 1.0 and ah.scale_items(items, ("bbox",), 1.0) == items


def test_mjpeg_sends_each_new_frame_once_with_headers():
    async def go():
        hub = ah.AnnotatedHub("vehicle")
        hub.put("D1", b"\xff\xd8one")
        gen = hub.mjpeg("D1", max_fps=60, max_seconds=5)
        first = await gen.__anext__()
        nxt = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.02)
        assert not nxt.done()                     # no new frame → nothing sent
        hub.put("D1", b"\xff\xd8two")
        second = await asyncio.wait_for(nxt, 1)
        await gen.aclose()
        return first, second
    first, second = asyncio.run(go())
    assert first.startswith(b"--dvasframe\r\nContent-Type: image/jpeg\r\nContent-Length: 5\r\n")
    assert b"X-Frame-Seq: 1" in first and first.endswith(b"\xff\xd8one\r\n")
    assert b"X-Frame-Seq: 2" in second and second.endswith(b"\xff\xd8two\r\n")


def test_hub_resolves_aliases_and_reports_fps():
    hub = ah.AnnotatedHub("person")
    assert hub.resolve(["DRN-001", "DRONE_KL_001"]) is None
    hub.put("DRONE_KL_001", b"x")
    assert hub.resolve(["DRN-001", "DRONE_KL_001"]) == "DRONE_KL_001"
    hub.put("D", None)                            # empty frames are ignored
    assert hub.latest("D") is None
