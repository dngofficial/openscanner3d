"""Relative-depth inference and local camera-frame point clouds."""
import base64
import importlib.util
import os
from pathlib import Path
from threading import Lock, RLock

import cv2
import numpy as np


def write_ply(path, xyz, rgb):
    xyz = np.asarray(xyz, np.float32)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    valid = np.isfinite(xyz).all(axis=1)
    xyz, rgb = xyz[valid], rgb[valid]
    data = np.empty(len(xyz), dtype=[("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
                                     ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    for i, key in enumerate(("x", "y", "z")): data[key] = xyz[:, i]
    for i, key in enumerate(("red", "green", "blue")): data[key] = rgb[:, i]
    header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {len(data)}\n"
              "property float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    with Path(path).open("wb") as f:
        f.write(header.encode())
        data.tofile(f)


def sample_cloud(xyz, rgb, limit=3500):
    if not len(xyz): return []
    ids = np.linspace(0, len(xyz) - 1, min(limit, len(xyz)), dtype=int)
    return np.column_stack((np.round(xyz[ids], 4), rgb[ids])).tolist()


def local_cloud(inverse, rgb, step=4, mask=None):
    # Display-only relative units. Final fusion recalibrates against SfM landmarks.
    height, width = inverse.shape
    lo, hi = np.percentile(inverse[np.isfinite(inverse)], [3, 97])
    normalized = np.clip((inverse - lo) / max(hi - lo, 1e-6), 0, 1)
    depth = 1 / (.15 + normalized)
    yy, xx = np.mgrid[2:height-2:step, 2:width-2:step]
    z = depth[yy, xx]
    xyz = np.stack(((xx + .5 - width / 2) * z / (.85 * width),
                    (yy + .5 - height / 2) * z / (.85 * width), z), axis=-1).reshape(-1, 3)
    colors = rgb[yy, xx].reshape(-1, 3)
    good = np.isfinite(xyz).all(axis=1)
    if mask is not None: good &= mask[yy,xx].ravel()>0
    return xyz[good], colors[good]


def depth_picture(inverse):
    finite = inverse[np.isfinite(inverse)]
    lo, hi = np.percentile(finite, [3, 97])
    gray = np.uint8(np.clip((inverse - lo) / max(hi - lo, 1e-6), 0, 1) * 255)
    colored = cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
    encoded = cv2.imencode(".jpg", colored, [cv2.IMWRITE_JPEG_QUALITY, 75])[1]
    return base64.b64encode(encoded).decode()


class DepthEngine:
    def __init__(self):
        self.status_lock = RLock()
        self.inference_lock = Lock()
        self.state = "unloaded"
        self.error = ""
        self.model = None
        self.processor = None
        self.device = "cpu"

    def status(self):
        with self.status_lock:
            return dict(state=self.state, error=self.error, device=self.device,
                        installed=all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers")))

    def load(self):
        with self.inference_lock:
            if self.model is not None: return
            with self.status_lock:
                self.state = "loading"; self.error = ""
            try:
                import torch
                from transformers import AutoImageProcessor, AutoModelForDepthEstimation
                model_id = os.environ.get("DEPTH_MODEL", "depth-anything/Depth-Anything-V2-Small-hf")
                self.device = os.environ.get("PREVIEW_DEVICE", "cpu")
                if self.device == "cpu":
                    torch.set_num_threads(min(4, os.cpu_count() or 1))
                processor = AutoImageProcessor.from_pretrained(model_id, use_fast=False)
                model = AutoModelForDepthEstimation.from_pretrained(model_id, use_safetensors=True)
                if getattr(model.config, "depth_estimation_type", "relative") != "relative":
                    raise RuntimeError("This pipeline expects relative inverse depth; use the default Depth Anything V2 Small model.")
                model.to(self.device).eval()
                self.processor, self.model = processor, model
                with self.status_lock: self.state = "ready"
            except Exception as error:
                with self.status_lock:
                    self.state = "error"
                    self.error = ("Could not load the depth model. Install requirements-depth.txt and check the first-download "
                                  "internet connection or DEPTH_MODEL local path. " + str(error))
                raise RuntimeError(self.error) from error

    def predict(self, rgb):
        with self.inference_lock:
            if self.model is None:
                raise RuntimeError("Load the depth model before starting capture.")
            import torch
            from PIL import Image
            inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with torch.inference_mode():
                raw = self.model(**inputs).predicted_depth
                inverse = torch.nn.functional.interpolate(
                    raw.unsqueeze(1), size=rgb.shape[:2], mode="bicubic", align_corners=False
                ).squeeze().float().cpu().numpy()
            if inverse.shape != rgb.shape[:2] or not np.isfinite(inverse).all():
                raise RuntimeError("Depth inference returned invalid values.")
            return inverse.astype(np.float32)


ENGINE = DepthEngine()
