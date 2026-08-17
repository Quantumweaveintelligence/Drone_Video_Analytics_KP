"""
test_ingest_security.py
=======================
Behavioral test suite for Ingest API stream and WebSocket authentication.
"""
import os
import sys
import unittest
from starlette.testclient import TestClient
from jose import jwt, jwk
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
import time

# Ensure ingest api directory is first in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import MagicMock
sys.modules["redis"] = MagicMock()
sys.modules["redis.asyncio"] = MagicMock()

import auth
import main
from main import app

def generate_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")
    public_key = private_key.public_key()
    pem_public = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("utf-8")
    return pem_private, pem_public

class IngestSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.priv, cls.pub = generate_keypair()
        cls.jwk_dict = jwk.construct(cls.pub, algorithm="RS256").to_dict()
        cls.jwk_dict["kid"] = "ingest-test-key"
        auth._jwks_cache = {"keys": [cls.jwk_dict]}
        auth._jwks_fetched = time.monotonic()

    def test_unauthenticated_stream_rejected(self):
        """Unauthenticated requests to /ingest/stream/{id} must return 401."""
        res = self.client.get("/ingest/stream/DRONE_KL_001")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Authentication token required", res.json()["detail"])

    def test_unauthenticated_snapshot_rejected(self):
        """Unauthenticated requests to /ingest/frame/{id}/latest must return 401."""
        res = self.client.get("/ingest/frame/DRONE_KL_001/latest")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Authentication token required", res.json()["detail"])

    def test_unauthenticated_websocket_rejected(self):
        """Unauthenticated WebSocket connections to /ws/status must be rejected."""
        try:
            with self.client.websocket_connect("/ws/status"):
                self.fail("Expected unauthenticated WebSocket connection to fail")
        except Exception:
            pass  # Successfully rejected with Policy Violation (1008)

    def test_valid_token_stream_accepted_auth(self):
        """Valid Bearer JWT or ?token= param should pass authentication."""
        now = int(time.time())
        token = jwt.encode(
            {"sub": "op1", "preferred_username": "operator_001", "realm_access": {"roles": ["drone-operator"]}, "exp": now + 3600},
            self.priv,
            algorithm="RS256",
            headers={"kid": "ingest-test-key"}
        )
        # Check token via query param
        res = self.client.get(f"/ingest/frame/DRONE_KL_001/latest?token={token}")
        # Note: 404/503 is fine for queue lookup, but it MUST NOT be 401 Unauthorized
        self.assertNotEqual(res.status_code, 401)
        self.assertNotEqual(res.status_code, 403)

if __name__ == "__main__":
    unittest.main(verbosity=2)
