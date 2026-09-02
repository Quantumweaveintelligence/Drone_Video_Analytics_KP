# -*- coding: utf-8 -*-
"""
DVAS Analytics — AES-256-GCM Vector Encryption
================================================
Encrypts/decrypts 512-dimensional face embedding vectors for DPDP compliance.

The module auto-detects legacy plaintext vectors vs encrypted vectors based
on payload size, enabling a seamless migration path.

Usage:
    from shared.crypto import encrypt_vector, decrypt_vector

    encrypted = encrypt_vector([0.1, 0.2, ...])   # -> bytes
    vector    = decrypt_vector(encrypted)           # -> List[float]

Security Issue Reference: #11
"""
import logging
import os
import struct
from typing import List

log = logging.getLogger(__name__)

_KEY_HEX = os.environ.get("FACE_VECTOR_ENCRYPTION_KEY", "")
_KEY: bytes = b""

if _KEY_HEX:
    try:
        _KEY = bytes.fromhex(_KEY_HEX)
        if len(_KEY) != 32:
            log.error(
                "FACE_VECTOR_ENCRYPTION_KEY must be exactly 64 hex chars (32 bytes), "
                "got %d bytes. Face vectors will use PLAINTEXT (legacy mode).",
                len(_KEY),
            )
            _KEY = b""
    except ValueError:
        log.error("FACE_VECTOR_ENCRYPTION_KEY is not valid hex. Using PLAINTEXT (legacy mode).")
        _KEY = b""
else:
    log.warning(
        "FACE_VECTOR_ENCRYPTION_KEY not set. "
        "Face vectors will be stored in PLAINTEXT (legacy mode)."
    )


def encrypt_vector(vector: List[float]) -> bytes:
    """
    Encrypt a float vector using AES-256-GCM.
    Returns:  nonce (12 bytes) + ciphertext + GCM tag (16 bytes)
    Falls back to raw struct packing if no key is configured (legacy mode).
    """
    plaintext = struct.pack(f">{len(vector)}f", *vector)

    if not _KEY:
        # Legacy/unconfigured mode — pack as raw bytes
        return plaintext

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)  # 96-bit nonce for GCM
    aesgcm = AESGCM(_KEY)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ciphertext  # 12 + len(plaintext) + 16 (GCM tag)


def decrypt_vector(data: bytes, expected_dim: int = 512) -> List[float]:
    """
    Decrypt an AES-256-GCM encrypted vector.
    Auto-detects legacy plaintext format based on payload size.

    Args:
        data:         Raw bytes (either plaintext struct or encrypted blob)
        expected_dim: Expected vector dimensionality (default 512 for ResNet50)

    Returns:
        List of floats representing the face embedding vector.
    """
    raw_size = expected_dim * 4  # 4 bytes per float32

    if len(data) == raw_size:
        # Legacy plaintext format (no encryption) — transparent backward compat
        return list(struct.unpack(f">{expected_dim}f", data))

    if not _KEY:
        raise ValueError(
            "Encrypted vector found but FACE_VECTOR_ENCRYPTION_KEY not configured"
        )

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = data[:12]
    ciphertext = data[12:]
    aesgcm = AESGCM(_KEY)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    dim = len(plaintext) // 4
    return list(struct.unpack(f">{dim}f", plaintext))
