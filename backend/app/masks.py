"""Static feature-extraction masks for overlays burned into every frame (e.g. the Camo watermark)."""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def region_mask(width: int, height: int, regions) -> np.ndarray:
    """255 where features may be detected, 0 inside excluded fractional [x0, y0, x1, y1] regions."""
    mask = np.full((height, width), 255, np.uint8)
    for x0, y0, x1, y1 in regions:
        mask[int(y0 * height):int(np.ceil(y1 * height)), int(x0 * width):int(np.ceil(x1 * width))] = 0
    return mask


def write_colmap_masks(images: Path, masks: Path, regions) -> int:
    """Write COLMAP masks (<image name>.png, zero = ignore) matching each JPEG's dimensions."""
    masks.mkdir(parents=True, exist_ok=True)
    count = 0
    for image in sorted(images.glob("*.jpg")):
        with Image.open(image) as opened:
            width, height = opened.size
        if not cv2.imwrite(str(masks / f"{image.name}.png"), region_mask(width, height, regions)):
            raise OSError(f"Could not write mask for {image.name}")
        count += 1
    if not count:
        raise ValueError("No JPEG images found to mask")
    return count
