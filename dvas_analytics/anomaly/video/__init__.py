"""
Video stream ingestion and decoding subsystem.
Provides robust memory-efficient readers for 4K/UHD drone feeds and standard video.
"""

from .stream_reader import RobustVideoReader, create_video_reader

__all__ = ["RobustVideoReader", "create_video_reader"]
