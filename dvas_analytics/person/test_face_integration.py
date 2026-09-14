# -*- coding: utf-8 -*-
"""
test_face_integration.py
─────────────────────────────────────────────────────────────────────────────
Comprehensive Integration & Hardening Test Suite for Face Recognition (Phase 8).
"""
import hashlib
import json
import logging
import os
import sys
import time
import unittest
import numpy as np
import cv2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_face_integration")

from person.face_recognizer import (
    ArcFaceRecognizer,
    WatchlistMatcher,
    AlertDeduplicator,
    compute_sharpness,
    enhance_face_crop,
    compute_sha256,
    build_evidence_metadata,
)
from shared.config import cfg


class TestFaceRecognitionPipeline(unittest.TestCase):
    """Test suite covering Phase 8 Integration & Hardening specifications."""

    @classmethod
    def setUpClass(cls):
        model_path = cfg.ARCFACE_MODEL_PATH
        if not os.path.exists(model_path):
            model_path = "/analytics/person/models/arcface_r100.onnx"
        cls.recognizer = ArcFaceRecognizer(model_path)
        logger.info(f"ArcFaceRecognizer initialized: ready={cls.recognizer.ready}, provider={cls.recognizer._provider}")

    def test_01_model_loaded(self):
        """Verify ArcFace model is loaded and ready."""
        self.assertTrue(self.recognizer.ready, "ArcFace model should be ready")
        self.assertIsNotNone(self.recognizer._session, "InferenceSession should exist")

    def test_02_embedding_dimension(self):
        """Verify embedding dimension is exactly 512 and L2-normalized."""
        face = np.random.randint(50, 200, (112, 112, 3), dtype=np.uint8)
        cv2.putText(face, "TEST", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

        emb = self.recognizer.extract_embedding(face, quality_check=False)
        self.assertIsNotNone(emb, "Embedding should be extracted")
        self.assertEqual(len(emb), 512, "ArcFace embedding must be 512-dim")

        norm = np.linalg.norm(emb)
        self.assertAlmostEqual(norm, 1.0, places=4, msg="Embedding must be L2-normalized")

    def test_03_quality_gates(self):
        """Verify size and blur gates reject poor quality images."""
        small_face = np.full((20, 20, 3), 128, dtype=np.uint8)
        emb_small = self.recognizer.extract_embedding(small_face, quality_check=True)
        self.assertIsNone(emb_small, "Crop < 30px should be rejected by size gate")

        blur_face = np.full((112, 112, 3), 128, dtype=np.uint8)
        emb_blur = self.recognizer.extract_embedding(blur_face, quality_check=True)
        self.assertIsNone(emb_blur, "Uniform/blurry crop should be rejected by blur gate")

        emb_none = self.recognizer.extract_embedding(None)
        self.assertIsNone(emb_none, "None input must return None safely")

    def test_04_enhancement_pipeline(self):
        """Verify Tier-1 CLAHE + Lanczos enhancement."""
        small_crop = np.random.randint(80, 180, (40, 40, 3), dtype=np.uint8)
        enhanced = enhance_face_crop(small_crop)
        self.assertIsNotNone(enhanced)
        self.assertGreaterEqual(enhanced.shape[0], 56, "Crop < 56px should be upscaled")

        enhanced_alias = self.recognizer.enhance_face_crop(small_crop)
        self.assertEqual(enhanced.shape, enhanced_alias.shape)

    def test_05_alert_deduplicator(self):
        """Verify 30s temporal deduplication cooldown."""
        dedup = AlertDeduplicator(cooldown_seconds=30)
        subject_id = "test-subject-001"

        self.assertTrue(dedup.should_alert(subject_id), "Initial sighting must trigger alert")
        self.assertFalse(dedup.should_alert(subject_id), "Second sighting within 30s must be suppressed")
        self.assertTrue(dedup.should_alert("test-subject-002"), "Different subject must trigger alert")

    def test_06_watchlist_matcher_double_buffer(self):
        """Verify in-memory gallery matching and double-buffer snapshotting."""
        matcher = WatchlistMatcher()
        self.assertEqual(matcher.gallery_size, 0)

        vec_a = np.random.randn(512)
        vec_a = vec_a / np.linalg.norm(vec_a)

        self.assertIsNone(matcher.match(vec_a.tolist()))
        self.assertEqual(matcher.match_batch([vec_a.tolist()]), [None])

    def test_07_section_65b_metadata(self):
        """Verify Section 65B digital evidence packet format."""
        raw_bytes = b"fake_evidence_image_data_2026"
        sha = compute_sha256(raw_bytes)
        expected_sha = hashlib.sha256(raw_bytes).hexdigest()
        self.assertEqual(sha, expected_sha)

        meta = build_evidence_metadata(
            alert_id="ALT-12345",
            watchlist_id="WCH-67890",
            name="Test Subject",
            category="wanted",
            drone_id="DRONE_KL_001",
            mission_id="MSN-TEST-ALPHA",
            tracker_id=42,
            confidence=0.8845,
            face_crop_sha256=sha,
            frame_sha256=sha,
            detected_at_iso="2026-09-11T18:00:00Z",
        )

        self.assertEqual(meta["section_65b"]["act"], "Indian Evidence Act, 1872 — Section 65B")
        self.assertEqual(meta["subject_name"], "Test Subject")
        self.assertEqual(meta["integrity"]["face_crop_sha256"], sha)
        self.assertEqual(meta["integrity"]["frame_sha256"], sha)
        self.assertEqual(meta["integrity"]["hash_algorithm"], "SHA-256")

    def test_08_error_hardening(self):
        """Verify error handling when corrupted or empty inputs are provided."""
        empty_arr = np.array([])
        self.assertIsNone(self.recognizer.extract_embedding(empty_arr))

        none_crop = enhance_face_crop(None)
        self.assertIsNone(none_crop)

        sample = np.zeros((112, 112, 3), dtype=np.uint8)
        preprocessed = self.recognizer.preprocess_face(sample)
        self.assertEqual(preprocessed.shape, (1, 3, 112, 112))


if __name__ == "__main__":
    unittest.main()
