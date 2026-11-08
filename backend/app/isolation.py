"""Keep reconstructed geometry that projects inside the per-frame AI object masks.

Uses the distorted sparse model (workspace/sparse/0) so projections land in original-image pixels,
which is where the masks live. There is no occlusion test: hidden background that stays inside
every mask silhouette survives, so the result is a proposal to inspect, not a guarantee.
"""
from pathlib import Path
import struct

import numpy as np
from PIL import Image

# COLMAP camera model id -> (name, parameter count). Only undistortion-free and radial/OpenCV models.
CAMERA_MODELS = {0: ("SIMPLE_PINHOLE", 3), 1: ("PINHOLE", 4), 2: ("SIMPLE_RADIAL", 4), 3: ("RADIAL", 5), 4: ("OPENCV", 8)}
MIN_VIEWS, MIN_INSIDE_FRACTION, MIN_COMPONENT_FRACTION = 2, .7, .2


def read_cameras(path: Path) -> dict:
    cameras = {}
    with path.open("rb") as handle:
        for _ in range(struct.unpack("<Q", handle.read(8))[0]):
            camera_id, model_id, width, height = struct.unpack("<iiQQ", handle.read(24))
            if model_id not in CAMERA_MODELS:
                raise ValueError(f"Unsupported COLMAP camera model id {model_id}")
            count = CAMERA_MODELS[model_id][1]
            cameras[camera_id] = (CAMERA_MODELS[model_id][0], width, height, struct.unpack(f"<{count}d", handle.read(8 * count)))
    return cameras


def read_images(path: Path) -> list:
    images = []
    with path.open("rb") as handle:
        for _ in range(struct.unpack("<Q", handle.read(8))[0]):
            _, qw, qx, qy, qz, tx, ty, tz, camera_id = struct.unpack("<idddddddi", handle.read(64))
            name = b""
            while (char := handle.read(1)) != b"\0":
                name += char
            handle.seek(24 * struct.unpack("<Q", handle.read(8))[0], 1)  # Skip 2D observations.
            images.append((name.decode("utf-8"), quaternion_matrix(qw, qx, qy, qz), np.array([tx, ty, tz]), camera_id))
    return images


def quaternion_matrix(w, x, y, z):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def project(points, camera, rotation, translation):
    """Return pixel coordinates (COLMAP convention: pixel centres at +0.5) and an in-front flag."""
    model, _, _, params = camera
    local = points @ rotation.T + translation
    front = local[:, 2] > 1e-9
    depth = np.where(front, local[:, 2], 1)
    u, v = local[:, 0] / depth, local[:, 1] / depth
    if model == "SIMPLE_PINHOLE":
        fx = fy = params[0]; cx, cy = params[1:3]
    elif model == "PINHOLE":
        fx, fy, cx, cy = params
    elif model in ("SIMPLE_RADIAL", "RADIAL"):
        fx = fy = params[0]; cx, cy = params[1:3]
        r2 = u * u + v * v
        radial = params[3] * r2 + (params[4] * r2 * r2 if model == "RADIAL" else 0)
        u, v = u + u * radial, v + v * radial
    else:  # OPENCV
        fx, fy, cx, cy, k1, k2, p1, p2 = params
        r2, uv = u * u + v * v, u * v
        radial = k1 * r2 + k2 * r2 * r2
        u, v = (u + u * radial + 2 * p1 * uv + p2 * (r2 + 2 * u * u),
                v + v * radial + 2 * p2 * uv + p1 * (r2 + 2 * v * v))
    return np.stack([fx * u + cx, fy * v + cy], axis=1), front


def vertex_support(vertices, cameras, images, masks: dict):
    """Count, per vertex, the masked views that see it and how many put it inside the object mask."""
    seen = np.zeros(len(vertices), int)
    inside = np.zeros(len(vertices), int)
    used = 0
    for name, rotation, translation, camera_id in images:
        mask = masks.get(name)
        if mask is None:
            continue
        camera = cameras[camera_id]
        if mask.shape != (camera[2], camera[1]):
            raise ValueError(f"Mask for {name} does not match the registered camera size")
        pixels, front = project(vertices, camera, rotation, translation)
        column, row = np.floor(pixels[:, 0]).astype(int), np.floor(pixels[:, 1]).astype(int)
        visible = front & (column >= 0) & (row >= 0) & (column < camera[1]) & (row < camera[2])
        seen += visible
        inside[visible] += mask[row[visible], column[visible]]
        used += 1
    return seen, inside, used


def face_components(faces, vertex_count):
    """Label faces by vertex-connected component (vectorised min-label propagation; no SciPy/NetworkX)."""
    labels = np.arange(vertex_count)
    while True:
        updated = labels.copy()
        np.minimum.at(updated, faces.ravel(), np.repeat(labels[faces].min(axis=1), 3))
        updated = updated[updated]  # Labels are vertex ids, so pointer jumping shortcuts long chains.
        if np.array_equal(updated, labels):
            return np.unique(labels[faces[:, 0]], return_inverse=True)[1]
        labels = updated


def isolate_mesh(mesh, sparse: Path, masks_dir: Path):
    cameras, images = read_cameras(sparse / "cameras.bin"), read_images(sparse / "images.bin")
    masks = {}
    for name, *_ in images:
        path = masks_dir / f"{name}.png"
        if path.is_file():
            with Image.open(path) as opened:
                masks[name] = np.asarray(opened) > 127
    seen, inside, used = vertex_support(mesh.vertices, cameras, images, masks)
    if used < MIN_VIEWS:
        raise ValueError(f"Only {used} registered photos have object masks; at least {MIN_VIEWS} are needed")
    keep = (seen >= MIN_VIEWS) & (inside >= MIN_INSIDE_FRACTION * np.maximum(seen, 1))
    faces = keep[mesh.faces].all(axis=1)
    isolated = mesh.copy()
    isolated.update_faces(faces)
    isolated.remove_unreferenced_vertices()
    if not len(isolated.faces):
        raise ValueError("No mesh faces fall inside the object masks")
    # Drop small floating fragments; keep pieces comparable in size to the largest.
    labels = face_components(isolated.faces, len(isolated.vertices))
    sizes = np.bincount(labels)
    isolated.update_faces(sizes[labels] >= MIN_COMPONENT_FRACTION * sizes.max())
    isolated.remove_unreferenced_vertices()
    return isolated, dict(masked_views=used, registered_views=len(images), input_faces=int(len(mesh.faces)),
                          kept_faces=int(len(isolated.faces)))
