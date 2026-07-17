"""
src/metrics/prometheus_metrics.py
===================================
Prometheus metric definitions — optional dependency.

If prometheus-client is not installed the module still imports cleanly;
all metric objects become no-op stubs so the rest of the codebase works
unchanged in environments without network access to PyPI.
"""

from __future__ import annotations

_server_started = False
_prometheus_available = False

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server as _start
    _prometheus_available = True
except ImportError:
    pass


# ── No-op stubs used when prometheus-client is absent ───────────────────────

class _NoOpMetric:
    """Silent no-op replacement for any Prometheus metric."""
    def labels(self, **kwargs) -> "_NoOpMetric":
        return self
    def set(self, value) -> None: pass
    def inc(self, amount=1) -> None: pass
    def observe(self, value) -> None: pass


def _make_gauge(name, doc, labelnames=()):
    if _prometheus_available:
        return Gauge(name, doc, labelnames=list(labelnames))
    return _NoOpMetric()

def _make_counter(name, doc, labelnames=()):
    if _prometheus_available:
        return Counter(name, doc, labelnames=list(labelnames))
    return _NoOpMetric()

def _make_histogram(name, doc, labelnames=(), buckets=None):
    if _prometheus_available:
        kw = {"labelnames": list(labelnames)}
        if buckets:
            kw["buckets"] = buckets
        return Histogram(name, doc, **kw)
    return _NoOpMetric()


def start_metrics_server(port: int = 8000) -> None:
    global _server_started
    if _prometheus_available and not _server_started:
        _start(port)
        _server_started = True
    elif not _prometheus_available:
        import logging
        logging.getLogger(__name__).warning(
            "prometheus-client not installed — metrics server not started. "
            "Run: pip install prometheus-client"
        )


# ── Drone source metrics ─────────────────────────────────────────────────────

DRONE_FPS            = _make_gauge("drone_fps_current", "Current decoded frame rate", ["drone_id"])
DRONE_FRAME_WIDTH    = _make_gauge("drone_frame_width_pixels", "Frame width", ["drone_id"])
DRONE_FRAME_HEIGHT   = _make_gauge("drone_frame_height_pixels", "Frame height", ["drone_id"])
DRONE_RECONNECT_TOTAL= _make_counter("drone_reconnect_total", "Reconnection attempts", ["drone_id"])
DRONE_FRAMES_TOTAL   = _make_counter("drone_frames_total", "Frames decoded", ["drone_id"])
DRONE_STREAM_UP      = _make_gauge("drone_stream_up", "1=connected 0=down", ["drone_id"])
DRONE_DECODE_LATENCY = _make_histogram(
    "drone_decode_latency_ms", "Decode latency ms", ["drone_id"],
    buckets=[5, 10, 20, 33, 50, 100, 200, 500, 1000],
)

# ── Rolling buffer metrics ───────────────────────────────────────────────────

BUFFER_USED_FRAMES    = _make_gauge("buffer_used_frames", "Frames in buffer", ["drone_id"])
BUFFER_CAPACITY_FRAMES= _make_gauge("buffer_capacity_frames", "Buffer capacity", ["drone_id"])
BUFFER_DROPS_TOTAL    = _make_counter("buffer_drops_total", "Dropped frames", ["drone_id"])
BUFFER_WRITE_LATENCY  = _make_histogram(
    "buffer_write_latency_ms", "Write latency ms", ["drone_id"],
    buckets=[0.5, 1, 2, 5, 10, 20, 50, 100],
)

# ── Change detection metrics (Task 1.3) ──────────────────────────────────────

CHANGE_EVENTS_TOTAL = _make_counter(
    "change_events_total",
    "Total ChangeEvents emitted by the change detection engine",
    ["drone_id"],
)

CHANGE_EVENTS_PER_MINUTE = _make_gauge(
    "change_events_per_minute",
    "Rolling events-per-minute rate over the last 60 seconds",
    ["drone_id"],
)

CHANGE_MOTION_SCORE = _make_gauge(
    "change_motion_score",
    "Latest composite motion score (0.0–1.0) from change detection",
    ["drone_id"],
)

CHANGE_DETECTION_LATENCY = _make_histogram(
    "change_detection_latency_ms",
    "Time in ms to process one frame through the two-stage change detection pipeline",
    ["drone_id"],
    buckets=[1, 2, 5, 10, 20, 33, 50, 100, 200],
)

FALSE_POSITIVE_RATE_EST = _make_gauge(
    "change_false_positive_rate_est",
    "Estimated false-positive rate (low-motion events / total events)",
    ["drone_id"],
)

# ── Cognitive frame selector metrics (Task 1.5) ──────────────────────────────

COGNITIVE_FRAMES_EVALUATED_TOTAL = _make_counter(
    "cognitive_frames_evaluated_total",
    "Total frames evaluated by the CognitiveFrameSelector",
    ["drone_id"],
)

COGNITIVE_FRAMES_SELECTED_TOTAL = _make_counter(
    "cognitive_frames_selected_total",
    "Total frames selected (transmitted) by the CognitiveFrameSelector",
    ["drone_id"],
)

COGNITIVE_SELECTION_RATE = _make_gauge(
    "cognitive_selection_rate",
    "Rolling fraction of evaluated frames selected for transmission (0.0–1.0)",
    ["drone_id"],
)

COGNITIVE_SELECTOR_LATENCY = _make_histogram(
    "cognitive_selector_latency_ms",
    "Time in ms to score and decide one frame through the CognitiveFrameSelector",
    ["drone_id"],
    buckets=[0.5, 1, 2, 5, 10, 20, 33, 50, 100],
)
