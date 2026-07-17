"""
scripts/extract_evidence_clip.py
==================================
Demonstration script: extract an evidence clip from the rolling buffer.

Usage (from the project root with venv activated):
    python scripts/extract_evidence_clip.py \
        --start_offset_s 30 \
        --duration_s 10 \
        --output ./evidence_clip.mp4

This script:
  1. Reads the existing buffer slab from buffer_storage/
  2. Extracts frames in the requested time window
  3. Re-encodes them to an MP4 file using OpenCV VideoWriter

It is intended to be called after GPU event confirmation in the full
pipeline (Task 4.4 → 4.5 in the proposal).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Make sure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2

from config.config import get_config
from src.buffer.rolling_frame_buffer import RollingFrameBuffer
from src.utils.logger import get_logger, setup_logging

setup_logging(level="INFO")
log = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extract an evidence clip from the rolling buffer.")
    p.add_argument(
        "--start_offset_s",
        type=float,
        default=30.0,
        help="Start of the clip window in seconds relative to NOW (default: 30 s ago)",
    )
    p.add_argument(
        "--duration_s",
        type=float,
        default=10.0,
        help="Duration of the clip in seconds (default: 10 s)",
    )
    p.add_argument(
        "--output",
        type=str,
        default="./evidence_clip.mp4",
        help="Output MP4 file path",
    )
    p.add_argument(
        "--fps",
        type=float,
        default=15.0,
        help="Output video frame rate (default: 15)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = get_config()

    now_ms = int(time.time() * 1_000)
    start_ms = now_ms - int(args.start_offset_s * 1_000)
    end_ms   = start_ms + int(args.duration_s * 1_000)

    log.info(
        "extracting_clip",
        start_ms=start_ms,
        end_ms=end_ms,
        output=args.output,
    )

    buf = RollingFrameBuffer(
        drone_id="drone_01",
        duration_s=cfg.buffer.duration_s,
        storage_dir=cfg.buffer.storage_dir,
        jpeg_quality=cfg.buffer.jpeg_quality,
        max_frames=cfg.buffer.max_frames,
        mmap_slab_size_bytes=cfg.buffer.mmap_slab_size_bytes,
    )

    frames = buf.extract_segment(start_ms, end_ms)
    buf.close()

    if not frames:
        log.warning("no_frames_in_window", start_ms=start_ms, end_ms=end_ms)
        sys.exit(1)

    log.info("frames_found", count=len(frames))

    # Decode first frame to determine resolution
    first_decoded = frames[0].decode()
    h, w = first_decoded.shape[:2]

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_path = str(Path(args.output).resolve())
    writer = cv2.VideoWriter(out_path, fourcc, args.fps, (w, h))

    for bf in frames:
        try:
            bgr = bf.decode()
            writer.write(bgr)
        except Exception as exc:
            log.warning("frame_decode_failed", frame_index=bf.frame_index, error=str(exc))

    writer.release()
    log.info("clip_written", path=out_path, frames=len(frames))
    print(f"\n✓ Evidence clip saved to: {out_path}  ({len(frames)} frames)")


if __name__ == "__main__":
    main()
