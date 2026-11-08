"""SAM 2 object masks for the silhouette step, prompted from geometry and an optional phone tap.

The rough first carving shows where the object is, so every photo gets its own prompt: a box around
the projected object and a point on its centre. The centre comes from the tap made on the phone at the
start of the scan (that camera ray, met with the object's vertical axis), or from the rough carving
when there was no tap. SAM 2 (pretrained, run locally in its own environment) then predicts each
photo's mask independently: no mask is copied from one view to another.

A SAM 2 mask is used only when it broadly agrees with that photo's GrabCut outline (overlap and size);
otherwise the photo keeps GrabCut, so one bad prediction cannot carve the object away.
"""
import json
from pathlib import Path
import subprocess
import sys
import time

import cv2
import numpy as np

from .config import ROOT, project_path, resolve_tool

MIN_IOU, MAX_AREA_RATIO = .35, 3.0
BOX_PAD = .06


def sam2_tools(settings):
    """(python, checkpoint, problem). An unset interpreter means .venv-sam2 in the repository, if present."""
    if settings.segmentation_python:
        python = resolve_tool(settings.segmentation_python)
    else:
        default = ROOT / ".venv-sam2" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        python = str(default) if default.is_file() else None
    checkpoint = project_path(settings.sam2_checkpoint)
    if not python:
        return None, checkpoint, "SAM 2 environment not found (.venv-sam2 or segmentation_python)"
    if not checkpoint.is_file():
        return python, checkpoint, f"SAM 2 checkpoint not found at {settings.sam2_checkpoint}"
    return python, checkpoint, None


def intrinsics(camera):
    model, _, _, p = camera
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        return p[0], p[0], p[1], p[2]
    return p[0], p[1], p[2], p[3]


def object_centre(scene, poses: list, region_world):
    """3D object centre from the phone tap (if any), else the centroid of the rough carving."""
    fallback = region_world.mean(0)
    tapped = next((pose for pose in poses if pose.get("tap")), None)
    image = next((im for im in scene.images if tapped and im["name"] == tapped["name"]), None)
    if image is None:
        return fallback, None
    camera = scene.cameras[image["camera"]]
    fx, fy, cx, cy = intrinsics(camera)
    x, y = tapped["tap"][0] * camera[1], tapped["tap"][1] * camera[2]
    # Lens distortion is ignored: the tap is a hint about which object, not a measurement.
    ray = image["R"].T @ np.array([(x - cx) / fx, (y - cy) / fy, 1.0])
    ray /= np.linalg.norm(ray)
    origin = -image["R"].T @ image["t"]
    # The rough carving's points that the tap ray passes through (closest 3%): their mean is the
    # tapped part of the object. A ray that misses the carving falls back to its centroid.
    offset = region_world - origin
    along = offset @ ray
    distance = np.linalg.norm(offset - np.outer(along, ray), axis=1)
    distance[along <= 0] = np.inf
    if not np.isfinite(distance).any() or distance.min() > .05 * scene.orbit:
        return fallback, None
    near = distance <= np.percentile(distance[np.isfinite(distance)], 3)
    return region_world[near].mean(0), tapped["name"]


def prompts(scene, photos: dict, region_world, centre):
    """Per photo: box around the projected rough object (padded) and the projected centre point."""
    from .silhouette import project
    items = []
    for image in scene.images:
        camera, (height, width) = scene.cameras[image["camera"]], photos[image["name"]].shape[:2]
        pixels, front = project(region_world, camera, image["R"], image["t"])
        pixels = pixels[front]
        if len(pixels) < 3:
            continue
        low, high = pixels.min(0), pixels.max(0)
        pad = BOX_PAD * (high - low)
        limit = [width - 1, height - 1]
        low, high = np.clip(low - pad, 0, limit), np.clip(high + pad, 0, limit)
        if (high - low).min() < 8:
            continue
        point, ok = project(centre[None], camera, image["R"], image["t"])
        points = [point[0].tolist()] if ok[0] and (low <= point[0]).all() and (point[0] <= high).all() else []
        items.append(dict(name=image["name"], box=[*low.tolist(), *high.tolist()], points=points))
    return items


def run_sam2(settings, images: Path, items, work: Path, timeout: int) -> tuple[dict, dict]:
    python, checkpoint, problem = sam2_tools(settings)
    if problem:
        raise RuntimeError(problem)
    work.mkdir(parents=True, exist_ok=True)
    request, output = work / "sam2_request.json", work / "sam2"
    request.write_text(json.dumps(dict(images=str(images.resolve()), items=items)), encoding="utf-8")
    command = [python, str(ROOT / "scripts" / "sam2_masks.py"), "--request", str(request), "--output", str(output),
               "--checkpoint", str(checkpoint), "--config", settings.sam2_config, "--device", settings.segmentation_device]
    with (work / "sam2.log").open("w", encoding="utf-8") as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout).returncode
    if code:
        raise RuntimeError(f"SAM 2 exited with code {code} (see sam2.log)")
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    masks = {}
    for item in items:
        mask = cv2.imread(str(output / f"{item['name']}.png"), cv2.IMREAD_GRAYSCALE)
        if mask is not None:
            masks[item["name"]] = mask > 127
    return masks, report


def merge(ai: dict, grabcut: dict) -> tuple[dict, dict]:
    """Per photo: SAM 2 when it broadly agrees with the GrabCut outline, otherwise GrabCut."""
    chosen, source = {}, {}
    for name, fallback in grabcut.items():
        mask = ai.get(name)
        ok = mask is not None and mask.shape == fallback.shape and bool(mask.any())
        if ok:
            union = (mask | fallback).sum()
            iou = (mask & fallback).sum() / union if union else 0
            ratio = mask.sum() / max(1, fallback.sum())
            ok = iou >= MIN_IOU and 1 / MAX_AREA_RATIO <= ratio <= MAX_AREA_RATIO
        chosen[name] = mask if ok else fallback
        source[name] = "sam2" if ok else "grabcut"
    return chosen, source


def make_segmenter(settings, work: Path):
    """Callable for reconstruct_object: (scene, photos, images, region_world, grabcut) -> (masks, info)."""

    def segment(scene, photos, images: Path, region_world, grabcut):
        if settings.masks == "grabcut":
            return grabcut, dict(method="GrabCut", sam2=0, grabcut=len(grabcut), note="masks = grabcut in config")
        started = time.monotonic()
        try:
            poses = json.loads((images.parent / "poses.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            poses = []
        centre, tapped = object_centre(scene, poses, region_world)
        items = prompts(scene, photos, region_world, centre)
        try:
            ai, report = run_sam2(settings, images, items, work, settings.timeout_seconds)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
            note = f"SAM 2 unavailable ({error}); GrabCut outlines used."
            if settings.masks == "sam2":
                note = "masks = sam2 but " + note
            return grabcut, dict(method="GrabCut", sam2=0, grabcut=len(grabcut), note=note)
        chosen, source = merge(ai, grabcut)
        used = sum(value == "sam2" for value in source.values())
        info = dict(method="SAM 2" if used == len(source) else "SAM 2 + GrabCut" if used else "GrabCut",
                    sam2=used, grabcut=len(source) - used, prompt="a box from the rough 3D shape and the object point tapped on the phone" if tapped
                    else "a box and centre point from the rough 3D shape",
                    device=report.get("device"), checkpoint=report.get("checkpoint"),
                    checkpoint_sha256=report.get("checkpoint_sha256"), seconds=round(time.monotonic() - started, 1),
                    per_photo=source, scores=report.get("scores"))
        if not used:
            info["note"] = "SAM 2 masks disagreed with the photo outlines; GrabCut outlines used."
        return chosen, info

    return segment


def save_masks(masks: dict, info: dict, folder: Path):
    """Full-size masks, so a detail-only rebuild can skip segmentation."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, mask in masks.items():
        cv2.imwrite(str(folder / f"{name}.png"), mask.astype(np.uint8) * 255)
    (folder / "masks.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def load_masks(folder: Path, names) -> tuple[dict, dict] | None:
    try:
        info = json.loads((folder / "masks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    masks = {}
    for name in names:
        mask = cv2.imread(str(folder / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            return None
        masks[name] = mask > 127
    return masks, info


def save_previews(photos: dict, masks: dict, info: dict, folder: Path):
    """Small overlays for the dashboard: object tinted, background dimmed."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, photo in photos.items():
        mask = masks[name]
        overlay = photo.astype(np.float32)
        overlay[~mask] *= .3
        overlay[mask] = overlay[mask] * .7 + np.array([120, 200, 80], np.float32) * .3  # BGR green tint.
        scale = min(1.0, 1280 / max(photo.shape[:2]))  # Large enough to inspect the outline edge when enlarged.
        small = cv2.resize(overlay.astype(np.uint8), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(folder / name), small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    summary = {key: value for key, value in info.items() if key != "scores"}
    (folder / "masks.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
