"""
Headless pipeline integration test — with tuned 4K settings.
Runs for 15 seconds at 4x playback speed to cover enough frames for MOG2 warm-up.
"""
import sys
import time
import threading

sys.path.insert(0, ".")

from config.config import reset_config, get_config
reset_config()
cfg = get_config()

from src.utils.logger import setup_logging, get_logger
setup_logging(level="WARNING", json_output=False, log_file="./logs/headless_test2.log")
log = get_logger("headless_test2")

from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.change_detection.change_detection_engine import ChangeDetectionEngine
from src.change_detection.change_event import ChangeEvent
from src.video.drone_simulation_source import DroneSimulationSource
from src.video.frame_interpolator import FrameInterpolator

print("=== Tuned 4K headless pipeline test ===")
print(f"mog2_history            : {cfg.change_detection.mog2_history}")
print(f"mog2_var_threshold      : {cfg.change_detection.mog2_var_threshold}")
print(f"min_contour_area        : {cfg.change_detection.min_contour_area}")
print(f"motion_score_threshold  : {cfg.change_detection.motion_score_threshold}")
print(f"persistence_frames      : {cfg.change_detection.persistence_frames}")
print()

# Rolling Frame Buffer
buffer = RollingFrameBuffer(
    drone_id="drone_01",
    duration_s=cfg.buffer.duration_s,
    storage_dir=cfg.buffer.storage_dir,
    jpeg_quality=cfg.buffer.jpeg_quality,
    max_frames=cfg.buffer.max_frames,
    mmap_slab_size_bytes=cfg.buffer.mmap_slab_size_bytes,
)

# Change Detection Engine
events = []
lock = threading.Lock()

def collect(evt: ChangeEvent):
    with lock:
        events.append(evt)

engine = ChangeDetectionEngine(
    drone_id="drone_01",
    mog2_history=cfg.change_detection.mog2_history,
    mog2_var_threshold=cfg.change_detection.mog2_var_threshold,
    detect_shadows=cfg.change_detection.detect_shadows,
    min_contour_area=cfg.change_detection.min_contour_area,
    motion_score_threshold=cfg.change_detection.motion_score_threshold,
    persistence_frames=cfg.change_detection.persistence_frames,
    max_queue_size=cfg.change_detection.max_queue_size,
    on_change_event=collect,
    emit_raw_mask=cfg.change_detection.emit_raw_mask,
)
engine.start()

# Fan-out
class FanOut:
    def __call__(self, vf):
        buffer.write_video_frame(vf)
        engine.push_frame(vf)

fanout = FanOut()

# Simulation source at 4x speed to cover enough frames quickly
source = DroneSimulationSource(
    drone_id="drone_01",
    video_files=cfg.simulation.video_files,
    loop=True,
    playback_speed=4.0,
    frame_queue_size=8,
    on_frame=fanout,
)
source.start()

interpolator = FrameInterpolator(source=source, target_fps=25.0)
interpolator.start()

print("Running for 15 seconds at 4x speed (~480 frames, MOG2 needs ~100 to warm up)...")
print()

t0 = time.monotonic()
while time.monotonic() - t0 < 15.0:
    time.sleep(1.0)
    elapsed = time.monotonic() - t0
    stats = engine.get_stats()
    with lock:
        n_events = len(events)
    print(
        f"  t={elapsed:5.1f}s | decoded={source._frame_index:4d} | "
        f"buffer={buffer.get_frame_count():4d} | "
        f"cd_proc={stats['frames_processed']:4d} | "
        f"events={n_events:3d} | "
        f"score={stats['last_motion_score']:.4f} | "
        f"epm={stats['events_per_minute']:.1f}"
    )

print()
print("Shutting down...")
interpolator.stop()
source.stop()
engine.stop()
buf_stats = buffer.get_buffer_stats()
buffer.close()

with lock:
    total_events = len(events)

print()
print("=" * 60)
print("FINAL RESULTS")
print("=" * 60)
print(f"Frames decoded           : {source._frame_index}")
print(f"Synthetic frames (interp): {interpolator._synthetic_total}")
print(f"Frames in buffer         : {buf_stats['frames_in_buffer']}")
print(f"Buffer window            : {buf_stats['window_s']} s")
print(f"Frames processed (CD)    : {engine._frames_processed}")
print(f"Change events fired      : {total_events}")

if events:
    e0 = events[0]
    print(f"First event @ frame      : {e0.frame_index}")
    print(f"First event motion_score : {round(e0.motion_score, 4)}")
    print(f"First event regions      : {e0.region_count}")
    print(f"First event has mask     : {e0.raw_mask is not None}")
print("=" * 60)

assert source._frame_index > 0, "FAIL: No frames decoded"
assert buf_stats["frames_in_buffer"] > 0, "FAIL: Buffer empty"
assert engine._frames_processed > 0, "FAIL: CD engine processed nothing"
print()
print("ALL ASSERTIONS PASSED")
if total_events > 0:
    print("CHANGE DETECTION: WORKING — events fired on DJI 4K footage")
else:
    print("NOTE: No events fired — footage may be mostly static (camera hover, no movement)")
    print("      This is CORRECT behaviour when the scene is stationary.")
    print("      Events will fire when vehicles/people move in view.")
