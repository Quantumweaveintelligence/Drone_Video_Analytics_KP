"""
Model loader module for Drone Suspicious Object Detection System.
Handles automatic download and caching of aerial weights from Hugging Face.
"""

import os
from pathlib import Path
from typing import Optional
from huggingface_hub import hf_hub_download
from ultralytics import YOLO


DEFAULT_REPO_ID = "dronefreak/visdrone-yolov11x"
DEFAULT_FILENAME = "best.pt"
LOCAL_WEIGHTS_PATH = Path(__file__).resolve().parent / "weights" / "visdrone-yolov11x.pt"


def get_model_weights(
    repo_id: str = DEFAULT_REPO_ID,
    filename: str = DEFAULT_FILENAME,
    target_path: Optional[Path | str] = None,
) -> str:
    """
    Ensures model weights exist locally. Downloads only once from Hugging Face if missing.

    :param repo_id: Hugging Face repository ID.
    :param filename: Filename within the repo.
    :param target_path: Optional custom local path to save the weights.
    :return: Absolute file path string to the weights.
    """
    dest = Path(target_path) if target_path else LOCAL_WEIGHTS_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 1000:
        return str(dest.resolve())

    print(f"[ModelLoader] Downloading weights from Hugging Face: {repo_id}/{filename}...")
    downloaded_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(dest.parent),
    )

    # If the downloaded filename differs from target filename, rename/link it
    dl_file = Path(downloaded_path)
    if dl_file != dest and dl_file.exists():
        if not dest.exists():
            import shutil
            shutil.copyfile(dl_file, dest)

    print(f"[ModelLoader] Model weights ready at: {dest}")
    return str(dest.resolve())


def load_aerial_yolo(
    weights_path: Optional[str] = None,
    device: str = "cuda",
) -> YOLO:
    """
    Loads and returns the initialized Ultralytics YOLO model.
    """
    path = weights_path or get_model_weights()
    print(f"[ModelLoader] Loading YOLO model from: {path} onto {device}...")
    model = YOLO(path)
    return model


if __name__ == "__main__":
    weights = get_model_weights()
    print(f"Weights verified at: {weights}")
