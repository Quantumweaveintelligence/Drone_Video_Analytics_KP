"""
Robust Video Stream Reader for Drone Surveillance.
Provides memory-efficient ingestion supporting 4K/UHD drone videos (e.g. DJI, Autel)
via native PyAV libswscale on-the-fly downscaling, preventing out-of-memory errors
inherent to full-frame OpenCV uncompressed buffers.
"""

from typing import Optional, Tuple
import cv2
import numpy as np

try:
    import av
    HAS_AV = True
except ImportError:
    HAS_AV = False


class RobustVideoReader:
    """
    Unified video capture supporting standard OpenCV and high-efficiency PyAV streaming.
    Automatically switches to PyAV for 4K/UHD feeds to decode and downscale on-the-fly.
    """

    def __init__(
        self,
        source: str,
        render_width: int = 1280,
        render_height: int = 720,
        force_backend: Optional[str] = None,
    ):
        self.source = source
        self.render_width = render_width
        self.render_height = render_height
        self.backend = "cv2"
        self.cap: Optional[cv2.VideoCapture] = None
        self.container: Optional[object] = None
        self.video_stream: Optional[object] = None
        self.frame_generator = None
        self.current_frame_idx = 0
        self.native_width = 0
        self.native_height = 0
        self.fps = 30.0
        self.total_frames = 0
        self.needs_resize = False

        self._initialize(force_backend)

    def _initialize(self, force_backend: Optional[str] = None):
        import os
        is_url = self.source.startswith(("rtsp://", "http://", "https://", "rtmp://"))
        if not is_url and not os.path.exists(self.source):
            return

        # 1. Probe video stream dimensions using PyAV if available, or cv2
        probe_w, probe_h, probe_fps, probe_total = 0, 0, 30.0, 0
        use_pyav = False

        if HAS_AV:
            try:
                c = av.open(self.source)
                if len(c.streams.video) > 0:
                    st = c.streams.video[0]
                    probe_w = st.width
                    probe_h = st.height
                    probe_fps = float(st.average_rate) if st.average_rate else 30.0
                    probe_total = st.frames if st.frames and st.frames > 0 else 0
                c.close()
            except Exception:
                pass

        if probe_w == 0:
            # Fallback probe with OpenCV
            test_cap = cv2.VideoCapture(self.source)
            if test_cap.isOpened():
                probe_w = int(test_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                probe_h = int(test_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                probe_fps = float(test_cap.get(cv2.CAP_PROP_FPS)) or 30.0
                probe_total = int(test_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            test_cap.release()

        self.native_width = probe_w
        self.native_height = probe_h
        self.fps = probe_fps if probe_fps > 0 else 30.0
        self.total_frames = probe_total

        # Determine backend: Use PyAV if 4K (width > 1920 or height > 1080) to avoid OOM
        if force_backend == "av":
            use_pyav = True
        elif force_backend == "cv2":
            use_pyav = False
        else:
            if HAS_AV and (self.native_width > 1920 or self.native_height > 1080):
                use_pyav = True

        if use_pyav and HAS_AV:
            self._init_pyav()
        else:
            self._init_opencv()

    def _init_pyav(self):
        try:
            self.container = av.open(self.source)
            self.video_stream = self.container.streams.video[0]
            # Optimize PyAV threading
            self.video_stream.thread_type = "AUTO"
            self.frame_generator = self.container.decode(video=0)
            self.backend = "av"
            self.needs_resize = (
                self.native_width > self.render_width or self.native_height > self.render_height
            )
            print(
                f"[VideoReader] Initialized PyAV stream: {self.native_width}x{self.native_height} "
                f"-> direct scaling to {self.render_width}x{self.render_height}"
            )
        except Exception as e:
            print(f"[VideoReader WARNING] PyAV init failed ({e}). Falling back to OpenCV...")
            self._init_opencv()

    def _init_opencv(self):
        self.backend = "cv2"
        self.cap = cv2.VideoCapture(self.source)
        self.needs_resize = (
            self.native_width > self.render_width or self.native_height > self.render_height
        )

    def isOpened(self) -> bool:
        if self.backend == "av":
            return self.container is not None
        return self.cap is not None and self.cap.isOpened()

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """
        Reads next frame, downscaling on-the-fly to render dimensions.
        Returns (success, frame_bgr_ndarray).
        """
        if self.backend == "av":
            try:
                frame = next(self.frame_generator)
                self.current_frame_idx += 1
                if self.needs_resize:
                    img = frame.reformat(
                        width=self.render_width,
                        height=self.render_height,
                        format="bgr24",
                    ).to_ndarray()
                else:
                    img = frame.to_ndarray(format="bgr24")
                return True, img
            except StopIteration:
                return False, None
            except Exception:
                return False, None
        else:
            if self.cap is None:
                return False, None
            ret, frame = self.cap.read()
            if not ret or frame is None:
                # If OpenCV fails on frame 0 with memory error, try hot fallback to PyAV
                if self.current_frame_idx == 0 and HAS_AV:
                    print("[VideoReader WARNING] OpenCV decode failed. Hot fallback to PyAV...")
                    self.cap.release()
                    self._init_pyav()
                    return self.read()
                return False, None

            self.current_frame_idx += 1
            if self.needs_resize:
                frame = cv2.resize(
                    frame,
                    (self.render_width, self.render_height),
                    interpolation=cv2.INTER_LINEAR,
                )
            return True, frame

    def get(self, prop_id: int) -> float:
        """Emulates cv2.VideoCapture.get() property access."""
        if prop_id == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self.render_width if self.needs_resize else self.native_width)
        elif prop_id == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self.render_height if self.needs_resize else self.native_height)
        elif prop_id == cv2.CAP_PROP_FPS:
            return float(self.fps)
        elif prop_id == cv2.CAP_PROP_FRAME_COUNT:
            return float(self.total_frames)
        elif prop_id == cv2.CAP_PROP_POS_FRAMES:
            return float(self.current_frame_idx)
        else:
            if self.cap is not None:
                return self.cap.get(prop_id)
            return 0.0

    def set(self, prop_id: int, value: float) -> bool:
        """Emulates cv2.VideoCapture.set() for frame seeking."""
        if prop_id == cv2.CAP_PROP_POS_FRAMES:
            target_idx = int(value)
            if self.backend == "av":
                try:
                    # Seek PyAV stream
                    pts = int(target_idx / max(self.fps, 1.0) / self.video_stream.time_base)
                    self.container.seek(pts, stream=self.video_stream)
                    self.frame_generator = self.container.decode(video=0)
                    self.current_frame_idx = target_idx
                    return True
                except Exception:
                    return False
            else:
                if self.cap:
                    res = self.cap.set(cv2.CAP_PROP_POS_FRAMES, value)
                    self.current_frame_idx = target_idx
                    return res
        return False

    def release(self):
        """Releases underlying video resources."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        if self.container is not None:
            try:
                self.container.close()
            except Exception:
                pass
            self.container = None


def create_video_reader(
    source: str,
    render_width: int = 1280,
    render_height: int = 720,
    force_backend: Optional[str] = None,
) -> RobustVideoReader:
    """Factory helper to instantiate a robust video reader."""
    return RobustVideoReader(
        source=source,
        render_width=render_width,
        render_height=render_height,
        force_backend=force_backend,
    )
