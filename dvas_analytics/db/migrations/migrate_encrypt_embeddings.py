#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-time migration: encrypt existing plaintext face embeddings.
================================================================

This script:
  1. Adds the `encrypted_embedding` BYTEA column (if not exists)
  2. Encrypts all existing plaintext embeddings in batches
  3. Verifies a sample decrypt matches the original
  4. Optionally drops the old `embedding` column (--drop-old flag)

Usage:
    # Set required env vars
    export DATABASE_URL="postgresql://dvas:dvas_secret@postgres:5432/dvas"
    export FACE_VECTOR_ENCRYPTION_KEY="<64 hex chars>"

    # Run migration (keeps old column for safety)
    python migrate_encrypt_embeddings.py

    # After verifying, drop old column
    python migrate_encrypt_embeddings.py --drop-old

Security Issue Reference: #11
"""
import argparse
import asyncio
import os
import struct
import sys
import time

import asyncpg


def _get_key() -> bytes:
    key_hex = os.environ.get("FACE_VECTOR_ENCRYPTION_KEY", "")
    if not key_hex:
        print("ERROR: FACE_VECTOR_ENCRYPTION_KEY environment variable is required.", file=sys.stderr)
        sys.exit(1)
    key = bytes.fromhex(key_hex)
    if len(key) != 32:
        print(f"ERROR: Key must be 32 bytes (64 hex chars), got {len(key)} bytes.", file=sys.stderr)
        sys.exit(1)
    return key


async def migrate(drop_old: bool = False):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is required.", file=sys.stderr)
        sys.exit(1)

    key = _get_key()

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(key)

    pool = await asyncpg.create_pool(database_url)

    # ── Step 1: Add new column ────────────────────────────────────────────
    print("[1/4] Adding encrypted_embedding column...")
    await pool.execute("""
        ALTER TABLE face_embeddings
        ADD COLUMN IF NOT EXISTS encrypted_embedding bytea;
    """)
    print("      ✅ Column ready.")

    # ── Step 2: Count rows to migrate ─────────────────────────────────────
    count_row = await pool.fetchrow(
        "SELECT COUNT(*) as cnt FROM face_embeddings "
        "WHERE encrypted_embedding IS NULL AND embedding IS NOT NULL"
    )
    total = count_row["cnt"] if count_row else 0
    print(f"[2/4] Found {total} embeddings to encrypt.")

    if total == 0:
        print("      Nothing to migrate.")
    else:
        # ── Step 3: Encrypt in batches ────────────────────────────────────
        print("[3/4] Encrypting embeddings...")
        batch_size = 100
        migrated = 0
        t_start = time.monotonic()

        # Fetch all IDs + embeddings
        rows = await pool.fetch(
            "SELECT id, embedding FROM face_embeddings "
            "WHERE encrypted_embedding IS NULL AND embedding IS NOT NULL"
        )

        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for row in batch:
                        vec = list(row["embedding"])
                        plaintext = struct.pack(f">{len(vec)}f", *vec)
                        nonce = os.urandom(12)
                        encrypted = nonce + aesgcm.encrypt(nonce, plaintext, None)
                        await conn.execute(
                            "UPDATE face_embeddings SET encrypted_embedding = $1 WHERE id = $2",
                            encrypted,
                            row["id"],
                        )
                        migrated += 1

            elapsed = time.monotonic() - t_start
            rate = migrated / elapsed if elapsed > 0 else 0
            print(f"      {migrated}/{total} encrypted ({rate:.0f} rows/sec)")

        print(f"      ✅ All {migrated} embeddings encrypted in {time.monotonic() - t_start:.1f}s.")

    # ── Step 4: Verify a sample ───────────────────────────────────────────
    print("[4/4] Verifying sample decrypt...")
    sample = await pool.fetchrow(
        "SELECT embedding, encrypted_embedding FROM face_embeddings "
        "WHERE encrypted_embedding IS NOT NULL AND embedding IS NOT NULL LIMIT 1"
    )
    if sample:
        original = list(sample["embedding"])
        enc_data = sample["encrypted_embedding"]
        nonce = enc_data[:12]
        decrypted_bytes = aesgcm.decrypt(nonce, enc_data[12:], None)
        restored = list(struct.unpack(f">{len(original)}f", decrypted_bytes))

        # Compare with tolerance for float precision
        match = all(abs(a - b) < 1e-6 for a, b in zip(original, restored))
        if match:
            print("      ✅ Verification PASSED: decrypt(encrypt(vector)) == original")
        else:
            print("      ❌ Verification FAILED: decrypted vector does not match original!", file=sys.stderr)
            sys.exit(1)
    else:
        print("      ⚠️  No rows with both columns populated — skipping verification.")

    # ── Optional: Drop old column ─────────────────────────────────────────
    if drop_old:
        print("[CLEANUP] Dropping old 'embedding' column...")
        await pool.execute("ALTER TABLE face_embeddings DROP COLUMN IF EXISTS embedding;")
        print("      ✅ Old column dropped.")
    else:
        print("\n💡 Old 'embedding' column kept for safety. Run with --drop-old to remove it.")

    await pool.close()
    print("\n🎉 Migration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Encrypt face embeddings in Postgres")
    parser.add_argument(
        "--drop-old",
        action="store_true",
        help="Drop the old plaintext 'embedding' column after migration",
    )
    args = parser.parse_args()
    asyncio.run(migrate(drop_old=args.drop_old))
