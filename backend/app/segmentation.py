"""Adapter that runs scripts/segment.py (SAM 2) as a bounded subprocess and validates its masks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import numpy as np
from PIL import Image

from .config import ROOT, Settings, resolve_tool
from .reconstruction import _heavy_job, run_logged

EMPTY_MASK_FRACTION = .002


def segmentation_tools(settings: Settings):
    """Return (python, checkpoint, problem) without importing torch in the server process."""
    python = resolve_tool(settings.segmentation_python) if settings.segmentation_python else sys.executable
    checkpoint = Path(settings.sam2_checkpoint)
    checkpoint = checkpoint if checkpoint.is_absolute() else ROOT / checkpoint
    if not python:
        return None, checkpoint, "Segmentation Python not found. Check segmentation_python in config.local.toml."
    if not checkpoint.is_file():
        return python, checkpoint, f"SAM 2 checkpoint not found at {settings.sam2_checkpoint}. See models/README.md."
    return python, checkpoint, None


def validate_masks(images: Path, masks: Path, frames: list[str]):
    for name in frames:
        path = masks / f"{name}.png"
        if not path.is_file():
            raise ValueError(f"No mask was produced for {name}")
        with Image.open(images / name) as image, Image.open(path) as mask:
            if mask.size != image.size:
                raise ValueError(f"Mask for {name} does not match the image dimensions")
            if not set(np.unique(np.asarray(mask))) <= {0, 255}:
                raise ValueError(f"Mask for {name} is not binary")


def run_segmentation(images: Path, frames: list[str], prompts: list[dict], destination: Path, settings: Settings) -> dict:
    python, checkpoint, problem = segmentation_tools(settings)
    if problem:
        return dict(msg=problem, lvl="bad", done=False)
    if not _heavy_job.acquire(blocking=False):
        return dict(msg="Another heavy task is running. Try AI selection again when it completes.", lvl="warn", done=False)
    work = destination.parent / f".segment-{uuid.uuid4().hex}"
    try:
        work.mkdir(parents=True)
        (work / "request.json").write_text(json.dumps(dict(frames=frames, prompts=prompts)), encoding="utf-8")
        command = [python, str(ROOT / "scripts" / "segment.py"), "--images", str(images), "--request", str(work / "request.json"),
                   "--output", str(work / "result"), "--checkpoint", str(checkpoint), "--config", settings.sam2_config,
                   "--device", settings.segmentation_device]
        log = destination.parent / "segmentation.log"
        code = run_logged(command, log, settings.timeout_seconds)
        if code:
            return dict(msg=f"AI selection failed (exit code {code}). See segmentation.log.", lvl="bad", done=False)
        report = json.loads((work / "result" / "segmentation.json").read_text(encoding="utf-8"))
        if set(report["frames"]) != set(frames):
            raise ValueError("Segmentation output does not cover the requested frames")
        validate_masks(images, work / "result" / "masks", frames)
        # Replace the previous selection only after the new one is complete and valid.
        if destination.exists():
            stale = work / "previous"
            os.replace(destination, stale)
        os.replace(work / "result", destination)
        empty = [name for name, area in report["frames"].items() if area < EMPTY_MASK_FRACTION]
        message = f"AI selection finished on {len(frames)} photos in {report['seconds']} s ({report['device']})."
        if empty:
            message += f" No object found in {len(empty)}; untick those or add a prompt on one of them."
        return dict(msg=message, lvl="warn" if empty else "ok", done=True, frames=report["frames"], empty=empty)
    except subprocess.TimeoutExpired:
        return dict(msg="AI selection timed out; its process was stopped.", lvl="bad", done=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _heavy_job.release()
