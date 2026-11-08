"""Bounded image decoding; preserve original upload bytes separately."""
import io
import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_FRAME_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Imported photos keep most of their detail; COLMAP's quality preset decides how much it uses.
MAX_WORKING_EDGE = 3200


def check_dimensions(image):
    if image.width * image.height > 16_000_000 or max(image.size) > 8192 or min(image.size) < 32:
        raise ValueError("Use an image of 32–8192 pixels per side and at most 16 megapixels (try your phone's 12 MP mode).")


def decode_jpeg(data: bytes, expected_shape=None, max_bytes=MAX_FRAME_BYTES):
    if len(data) > max_bytes:
        raise ValueError("Frame too large; reduce the capture resolution.")
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "JPEG":
            raise ValueError("Expected a JPEG camera frame.")
        check_dimensions(image)
        shape = image.size
    if expected_shape is not None and shape != expected_shape:
        raise ValueError("Camera resolution or orientation changed. Start a new scan to keep calibration consistent.")
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), 1)
    if decoded is None:
        raise ValueError("Invalid JPEG received.")
    return decoded, shape


def normalize_photo(data: bytes):
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Photo exceeds 25 MB.")
    with Image.open(io.BytesIO(data)) as original:
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(original.format)
        if not suffix:
            raise ValueError("Import JPEG, PNG, or WebP. HEIC is not supported yet.")
        check_dimensions(original)
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((MAX_WORKING_EDGE, MAX_WORKING_EDGE))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=95)
        return output.getvalue(), suffix
