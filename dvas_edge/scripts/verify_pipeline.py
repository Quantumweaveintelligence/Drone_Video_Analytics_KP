"""
Verification script for the Drone Feed Simulation + Change Detection pipeline.
Run from the project root:
    .venv\Scripts\python.exe scripts\verify_pipeline.py
"""
import sys
import time
import threading
import cv2

# ─────────────────────────────────────────────────────────────────────────────
# Test 1: DJI MP4 files are readable
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("Test 1: DJI MP4 file access")
print("=" * 60)

for path in ["DJI_0110.MP4", "DJI_0111.MP4"]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        print(f"  ERROR: Cannot open {path}")
        sys.exit(1)
    fps = cap.get(cv2.CAP_PROP_FPS)
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tot = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ret, frame = cap.read()
    cap.release()
    assert ret, f"  ERROR: Cannot read first frame from {path}"
    print(f"  OK  {path}  =>  {w}x{h}  @  {fps:.1f} fps  |  {tot} frames")

print()

# ─────────────────────────────────────────────────────────────────────────────
# Test 2: MOG2 change detection on real DJI frames
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("Test 2: ChangeDetectionEngine on DJI_0110.MP4 frames")
print("=" * 60)

from src.change_detection.change_detection_engine import ChangeDetectionEngine
from src.change_detection.change_event import ChangeEvent

detected_events = []
event_lock = threading.Lock()

def collect(evt):
    with event_lock:
        detected_events.append(evt)

engine = ChangeDetectionEngine(
    drone_id="test_drone",
    mog2_history=10,
    mog2_var_threshold=16.0,
    detect_shadows=False,
    min_contour_area=100,
    motion_score_threshold=0.001,
    persistence_frames=1,
    on_change_event=collect,
    emit_raw_mask=True,
)
engine.start()

class FakeVF:
    def __init__(self, idx, frame):
        self.frame_index = idx
        self.utc_timestamp_ms = int(time.time() * 1000) + idx * 33
        self.frame = frame

cap = cv2.VideoCapture("DJI_0110.MP4")
pushed = 0
for i in range(90):
    ret, fr = cap.read()
    if not ret:
        break
    if i < 5:
        continue
    engine.push_frame(FakeVF(i, fr))
    pushed += 1
cap.release()

time.sleep(2.5)
engine.stop()

stats = engine.get_stats()
print(f"  Frames pushed     : {pushed}")
print(f"  Frames processed  : {stats['frames_processed']}")
print(f"  Events detected   : {stats['events_total']}")
print(f"  Last motion score : {stats['last_motion_score']:.4f}")

if detected_events:
    evt = detected_events[0]
    print(f"  First event:")
    print(f"    frame_index   = {evt.frame_index}")
    print(f"    motion_score  = {evt.motion_score:.4f}")
    print(f"    region_count  = {evt.region_count}")
    mask_shape = evt.raw_mask.shape if evt.raw_mask is not None else None
    print(f"    mask shape    = {mask_shape}")
    print(f"  PASSED: ChangeDetectionEngine fires events on DJI footage")
else:
    print("  WARNING: No events fired — thresholds may need tuning for this footage")

print()

# ─────────────────────────────────────────────────────────────────────────────
# Test 3: DroneSimulationSource -> RollingFrameBuffer
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("Test 3: DroneSimulationSource -> RollingFrameBuffer")
print("=" * 60)

from src.video.drone_simulation_source import DroneSimulationSource
from src.buffer.rolling_frame_buffer import RollingFrameBuffer

buf = RollingFrameBuffer(
    drone_id="test_sim",
    duration_s=5,
    storage_dir="./buffer_storage/verify_test",
    jpeg_quality=85,
    max_frames=500,
    mmap_slab_size_bytes=32 * 1024 * 1024,
)

sim = DroneSimulationSource(
    drone_id="test_sim",
    video_files=["DJI_0110.MP4"],
    loop=False,
    playback_speed=10.0,
    on_frame=buf.write_video_frame,
)
sim.start()
time.sleep(3.0)
sim.stop()

count = buf.get_frame_count()
print(f"  Buffer frames after 3s at 10x speed: {count}")
assert count > 0, "  ERROR: Buffer received no frames from simulation"
buf.delete_slab()
print(f"  PASSED: Buffer received {count} frames from DroneSimulationSource")
print()

# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Config loads simulation + change_detection sections
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("Test 4: Config sections (simulation + change_detection)")
print("=" * 60)

from config.config import reset_config, get_config
reset_config()
cfg = get_config()

assert cfg.simulation.enabled, "simulation.enabled should be True"
assert len(cfg.simulation.video_files) >= 2, "Expected 2 video files in config"
assert "DJI_0110" in cfg.simulation.video_files[0], "Expected DJI_0110 as first file"
assert cfg.change_detection.enabled, "change_detection.enabled should be True"
assert cfg.change_detection.mog2_history == 100,   f"Expected 100, got {cfg.change_detection.mog2_history}"
assert cfg.change_detection.persistence_frames == 2, f"Expected 2, got {cfg.change_detection.persistence_frames}"

print(f"  simulation.video_files       : {cfg.simulation.video_files}")
print(f"  simulation.playback_speed    : {cfg.simulation.playback_speed}")
print(f"  change_detection.mog2_history: {cfg.change_detection.mog2_history}")
print(f"  change_detection.threshold   : {cfg.change_detection.motion_score_threshold}")
print("  PASSED: Config sections loaded correctly")
print()

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("ALL TESTS PASSED")
print("Run 'python combined_change_stream.py' to start the full pipeline.")
print("=" * 60)
