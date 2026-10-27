# DVAS RunPod image releases

One row per image pushed to GHCR (appended by `deploy/runpod/tools/push_ghcr.sh`).
Deploy only tags listed here; roll back by putting the previous tag into the RunPod template.

| Pushed (UTC) | Tag | Digest | Commit | Base image | Size |
|---|---|---|---|---|---|
| 2026-10-27T12:41Z | `20261027-ed8b02c` | `sha256:c8d478a74a26d1c7a65ac2b602fd100e2975821ee0f0a6c58107e86363ebfffc` | `ed8b02c3ee75` | `runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04` | 23.9 GB |
