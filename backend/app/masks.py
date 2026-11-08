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


def write_colmap_masks(images: Path, masks: Path, regions, object_masks: Path | None = None) -> int:
    """Write COLMAP masks (<image name>.png, zero = ignore) matching each JPEG's dimensions.

    With object_masks, each image's own AI mask (same name, 255 = object) is combined with the regions,
    so features come only from the object. A missing or mis-sized object mask is an error, never reused.
    """
    masks.mkdir(parents=True, exist_ok=True)
    count = 0
    for image in sorted(images.glob("*.jpg")):
        with Image.open(image) as opened:
            width, height = opened.size
        mask = region_mask(width, height, regions)
        if object_masks is not None:
            source = object_masks / f"{image.name}.png"
            if not source.is_file():
                raise ValueError(f"No object mask for {image.name}")
            with Image.open(source) as opened:
                if opened.size != (width, height):
                    raise ValueError(f"Object mask for {image.name} does not match the image dimensions")
                mask[np.asarray(opened.convert("L")) <= 127] = 0
        if not cv2.imwrite(str(masks / f"{image.name}.png"), mask):
            raise OSError(f"Could not write mask for {image.name}")
        count += 1
    if not count:
        raise ValueError("No JPEG images found to mask")
    return count
