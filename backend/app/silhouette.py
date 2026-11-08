"""Object reconstruction from silhouettes (visual hull) using COLMAP's camera poses.

Dense stereo needs surface texture, so plain objects (a matte black bottle, a white mug) come out as
holes while the textured table around them dominates the Poisson mesh. Outlines work regardless of
texture: with every camera's pose known, the object is the space that projects inside its outline in
all photos. Steps:

1. Read COLMAP's sparse model (cameras.bin, images.bin) and dense points (fused.ply, or points3D.bin).
2. Find the table: the dominant plane in the dense points, normal facing the cameras. The object sits
   where the cameras' viewing directions converge, dropped onto that plane.
3. Outline the object in each photo with GrabCut (classical computer vision, not ML), told that table
   pixels away from the object are background, then refined once with the first carving result.
   Per-photo masks from elsewhere (e.g. an ML segmenter) can be supplied instead.
4. Carve a voxel grid above the table, mesh it (marching cubes), smooth, and colour vertices from the
   photos that face them.

The result is upright (table plane horizontal) and centred, in COLMAP's arbitrary scale. Concavities
that no outline reveals (inside a cup, between a handle and body) stay filled: a visual hull is an
outer bound of the shape, not a measurement.
"""
from pathlib import Path
import struct

import cv2
import numpy as np

CAMERA_MODELS = {0: ("SIMPLE_PINHOLE", 3), 1: ("PINHOLE", 4), 2: ("SIMPLE_RADIAL", 4), 3: ("RADIAL", 5), 4: ("OPENCV", 8)}
MASK_SCALE = .4            # GrabCut runs on downscaled photos; masks are scaled back up.
GRID = 160                 # Voxels per side of the search box.
BOX_RADIUS, BOX_HEIGHT = .3, .9   # Search box around the object, relative to the camera orbit radius.


# ---- COLMAP model ---------------------------------------------------------
def read_cameras(path: Path) -> dict:
    data, cameras = path.read_bytes(), {}
    offset = 8
    for _ in range(struct.unpack_from("<Q", data)[0]):
        camera_id, model, width, height = struct.unpack_from("<iiQQ", data, offset)
        offset += 24
        if model not in CAMERA_MODELS:
            raise ValueError(f"Unsupported COLMAP camera model {model}")
        count = CAMERA_MODELS[model][1]
        cameras[camera_id] = (CAMERA_MODELS[model][0], int(width), int(height), struct.unpack_from(f"<{count}d", data, offset))
        offset += 8 * count
    return cameras


def quaternion_matrix(w, x, y, z):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def read_images(path: Path) -> list:
    data, images, offset = path.read_bytes(), [], 8
    for _ in range(struct.unpack_from("<Q", data)[0]):
        _, qw, qx, qy, qz, tx, ty, tz, camera_id = struct.unpack_from("<idddddddi", data, offset)
        offset += 64
        end = data.index(b"\0", offset)
        name = data[offset:end].decode("utf-8")
        offset = end + 1 + 8 + 24 * struct.unpack_from("<Q", data, end + 1)[0]
        images.append(dict(name=name, R=quaternion_matrix(qw, qx, qy, qz), t=np.array([tx, ty, tz]), camera=camera_id))
    return images


def read_points3d(path: Path) -> np.ndarray:
    data, points, offset = path.read_bytes(), [], 8
    for _ in range(struct.unpack_from("<Q", data)[0]):
        points.append(struct.unpack_from("<3d", data, offset + 8))
        offset += 8 + 24 + 3 + 8
        offset += 8 + 8 * struct.unpack_from("<Q", data, offset)[0]
    return np.array(points)


def project(points, camera, R, t):
    """Pixel coordinates (COLMAP convention) and an in-front-of-camera flag."""
    model, _, _, p = camera
    local = points @ R.T + t
    front = local[:, 2] > 1e-9
    depth = np.where(front, local[:, 2], 1)
    u, v = local[:, 0] / depth, local[:, 1] / depth
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"):
        fx = fy = p[0]
        cx, cy = p[1], p[2]
        if model != "SIMPLE_PINHOLE":
            r2 = u * u + v * v
            radial = p[3] * r2 + (p[4] * r2 * r2 if model == "RADIAL" else 0)
            u, v = u * (1 + radial), v * (1 + radial)
    else:
        fx, fy, cx, cy = p[:4]
        if model == "OPENCV":
            k1, k2, p1, p2 = p[4:]
            r2, uv = u * u + v * v, u * v
            radial = k1 * r2 + k2 * r2 * r2
            u, v = (u + u * radial + 2 * p1 * uv + p2 * (r2 + 2 * u * u),
                    v + v * radial + 2 * p2 * uv + p1 * (r2 + 2 * v * v))
    return np.stack([fx * u + cx, fy * v + cy], 1), front


# ---- scene frame ----------------------------------------------------------
def convergence_point(centres, directions):
    """Least-squares point closest to every camera's viewing axis."""
    a = sum(np.eye(3) - np.outer(d, d) for d in directions)
    b = sum((np.eye(3) - np.outer(d, d)) @ c for c, d in zip(centres, directions))
    return np.linalg.solve(a, b)


def fit_table(points, centres, scale, rng):
    """Dominant plane (RANSAC + least-squares refit), normal pointing toward the cameras."""
    sample = points[rng.choice(len(points), min(len(points), 60000), replace=False)]
    threshold, best = .005 * scale, (0, None)
    for _ in range(300):
        a, b, c = sample[rng.choice(len(sample), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        if np.linalg.norm(normal) < 1e-12:
            continue
        normal /= np.linalg.norm(normal)
        count = int((np.abs((sample - a) @ normal) < threshold).sum())
        if count > best[0]:
            best = (count, (normal, a))
    if best[1] is None or best[0] < .15 * len(sample):
        raise ValueError("No supporting surface (table) found in the dense points")
    normal, anchor = best[1]
    inliers = points[np.abs((points - anchor) @ normal) < threshold]
    centroid = inliers.mean(0)
    normal = np.linalg.svd(inliers - centroid, full_matrices=False)[2][-1]
    if ((centres - centroid) @ normal).mean() < 0:
        normal = -normal
    return normal, centroid, inliers


class Scene:
    """Table-aligned frame: origin under the object, z up from the table, units of COLMAP."""

    def __init__(self, cameras, images, points, seed=0):
        self.cameras, self.images = cameras, images
        centres = np.array([-im["R"].T @ im["t"] for im in images])
        directions = np.array([im["R"].T @ [0, 0, 1] for im in images])
        target = convergence_point(centres, directions)
        rough = float(np.median(np.linalg.norm(centres - target, axis=1)))
        self.up, anchor, table = fit_table(points, centres, rough, np.random.default_rng(seed))
        self.origin = target - ((target - anchor) @ self.up) * self.up
        seed_axis = np.array([1.0, 0, 0]) if abs(self.up[0]) < .9 else np.array([0, 1.0, 0])
        self.x = np.cross(self.up, seed_axis)
        self.x /= np.linalg.norm(self.x)
        self.y = np.cross(self.up, self.x)
        offsets = centres - self.origin
        self.orbit = float(np.median(np.linalg.norm(offsets - np.outer(offsets @ self.up, self.up), axis=1)))
        local = np.stack([(table - self.origin) @ self.x, (table - self.origin) @ self.y], 1).astype(np.float32)
        self.table_outline = cv2.convexHull(local).reshape(-1, 2)

    def world(self, local):
        local = np.asarray(local, float)
        return self.origin + np.outer(local[:, 0], self.x) + np.outer(local[:, 1], self.y) + np.outer(local[:, 2], self.up)

    def local(self, world):
        offset = np.asarray(world) - self.origin
        return np.stack([offset @ self.x, offset @ self.y, offset @ self.up], 1)


# ---- masks ----------------------------------------------------------------
def _outline(points, camera, R, t, shape):
    pixels, front = project(points, camera, R, t)
    mask = np.zeros(shape, np.uint8)
    pixels = pixels[front] * MASK_SCALE
    if len(pixels) >= 3:
        cv2.fillConvexPoly(mask, cv2.convexHull(pixels.astype(np.float32)).astype(np.int32), 1)
    return mask


def grabcut_masks(scene, photos: dict, region) -> dict:
    """Object masks seeded by geometry: region = 3D points around the object (world coordinates)."""
    table = scene.world(np.c_[scene.table_outline, np.zeros(len(scene.table_outline))])
    masks = {}
    for image in scene.images:
        camera, R, t = scene.cameras[image["camera"]], image["R"], image["t"]
        full = photos[image["name"]]
        small = cv2.resize(full, None, fx=MASK_SCALE, fy=MASK_SCALE, interpolation=cv2.INTER_AREA)
        height, width = small.shape[:2]
        likely = _outline(region, camera, R, t, (height, width))
        if not likely.any():
            masks[image["name"]] = np.zeros(full.shape[:2], bool)
            continue
        near = cv2.dilate(likely, np.ones((int(.04 * width) | 1,) * 2, np.uint8))
        state = np.full((height, width), cv2.GC_PR_BGD, np.uint8)
        state[(_outline(table, camera, R, t, (height, width)) == 1) & (near == 0)] = cv2.GC_BGD  # Table: background.
        state[likely == 1] = cv2.GC_PR_FGD
        rows, cols = np.nonzero(near)
        pad = int(.05 * width)
        far = np.ones_like(state, bool)
        far[max(0, rows.min() - pad):rows.max() + pad, max(0, cols.min() - pad):cols.max() + pad] = False
        state[far] = cv2.GC_BGD
        if (state == cv2.GC_BGD).any() and (state == cv2.GC_PR_FGD).any():
            cv2.grabCut(small, state, None, np.zeros((1, 65)), np.zeros((1, 65)), 4, cv2.GC_INIT_WITH_MASK)
            chosen = np.isin(state, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.uint8)
        else:
            chosen = likely
        # Remove specks, then scale up smoothly so the outline has no pixel staircase at full size.
        chosen = cv2.morphologyEx(chosen, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        chosen = cv2.morphologyEx(chosen, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        smooth = cv2.GaussianBlur(chosen.astype(np.float32), (5, 5), 0)
        masks[image["name"]] = cv2.resize(smooth, full.shape[1::-1], interpolation=cv2.INTER_LINEAR) > .5
    return masks


# ---- carving and meshing ----------------------------------------------------
def carve(scene, masks: dict, grid=GRID):
    """Voxels above the table inside the outline in all but a couple of the photos that see them."""
    radius, height = BOX_RADIUS * scene.orbit, BOX_HEIGHT * scene.orbit
    steps = (np.arange(grid) + .5) / grid
    local = np.stack(np.meshgrid(-radius + 2 * radius * steps, -radius + 2 * radius * steps, height * steps, indexing="ij"), -1).reshape(-1, 3)
    points = scene.world(local)
    seen, inside = np.zeros(len(points), np.int32), np.zeros(len(points), np.int32)
    for image in scene.images:
        mask = masks.get(image["name"])
        if mask is None:
            continue
        pixels, front = project(points, scene.cameras[image["camera"]], image["R"], image["t"])
        col, row = np.floor(pixels[:, 0]).astype(int), np.floor(pixels[:, 1]).astype(int)
        visible = front & (col >= 0) & (row >= 0) & (col < mask.shape[1]) & (row < mask.shape[0])
        seen += visible
        inside[visible] += mask[row[visible], col[visible]]
    views = len(masks)
    tolerance = max(1, round(.06 * views))
    missing = seen - inside
    enough = seen >= max(3, views // 4)
    # Soft score: positive inside (at most `tolerance` outlines disagree), negative outside. Meshing this
    # blurred score instead of yes/no voxels gives a smooth surface without a staircase.
    score = np.where(enough, tolerance + .5 - missing, -tolerance - .5).astype(np.float32).reshape(grid, grid, grid)
    from scipy import ndimage
    from skimage import measure
    labels = measure.label(score > 0)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    if not sizes.max():
        raise ValueError("The outlines do not agree on any object volume")
    volume = labels == sizes.argmax()
    if volume.mean() > .5:
        raise ValueError("The outlines did not isolate an object (more than half the search box is filled)")
    keep = ndimage.binary_dilation(volume, iterations=2)
    score = np.where(keep, score, -tolerance - .5)
    return volume, ndimage.gaussian_filter(score, 1.0), (radius, height)


def mesh_volume(score, box, smooth=8):
    import trimesh
    from skimage import measure
    radius, height = box
    grid = score.shape[0]
    vertices, faces, _, _ = measure.marching_cubes(np.pad(score, 1, constant_values=score.min()), 0.0)
    vertices = (vertices - .5) / grid * [2 * radius, 2 * radius, height] + [-radius, -radius, 0]
    mesh = trimesh.Trimesh(vertices, faces, process=True)
    parts = mesh.split(only_watertight=False) if len(mesh.faces) else [mesh]
    mesh = max(parts, key=lambda part: len(part.faces))
    trimesh.smoothing.filter_taubin(mesh, iterations=smooth)
    return mesh


def colour(mesh, scene, photos: dict, masks: dict):
    """Median colour per vertex over photos that face it and see it well inside the object outline.

    Outlines are shrunk first and the median ignores the odd background pixel near the silhouette edge.
    """
    world = scene.world(mesh.vertices)
    normals = mesh.vertex_normals @ np.stack([scene.x, scene.y, scene.up])
    samples = np.full((len(scene.images), len(world), 3), np.nan, np.float32)
    for index, image in enumerate(scene.images):
        photo = photos[image["name"]]
        shrink = max(3, photo.shape[1] // 120)
        mask = cv2.erode(masks[image["name"]].astype(np.uint8), np.ones((shrink, shrink), np.uint8)) > 0
        R, t = image["R"], image["t"]
        centre = -R.T @ t
        towards = centre - world
        facing = (normals * towards).sum(1) / (np.linalg.norm(towards, axis=1) + 1e-12) > .3
        pixels, front = project(world, scene.cameras[image["camera"]], R, t)
        col, row = np.round(pixels[:, 0]).astype(int), np.round(pixels[:, 1]).astype(int)
        ok = facing & front & (col >= 0) & (row >= 0) & (col < photo.shape[1]) & (row < photo.shape[0])
        ok[ok] &= mask[row[ok], col[ok]]
        samples[index, ok] = photo[row[ok], col[ok], ::-1]
    seen = ~np.isnan(samples[..., 0]).all(0)
    rgb = np.full((len(world), 3), 128.0)
    if seen.any():
        rgb[seen] = np.nanmedian(samples[:, seen], axis=0)
    mesh.visual.vertex_colors = np.c_[np.clip(rgb, 0, 255), np.full(len(rgb), 255)].astype(np.uint8)
    return float(seen.mean())


def y_up(mesh):
    """Our frame is z-up; glTF and Blender's OBJ import expect y-up."""
    out = mesh.copy()
    out.vertices = out.vertices[:, [0, 2, 1]] * [1, 1, -1]
    return out


def reconstruct_object(workspace: Path, images: Path, output: Path, stem="object", masks_dir: Path | None = None) -> dict:
    """Silhouette reconstruction for a finished COLMAP workspace; writes <stem>.glb and <stem>.obj."""
    sparse, dense = workspace / "sparse" / "0", workspace / "dense" / "0"
    cameras, model_images = read_cameras(sparse / "cameras.bin"), read_images(sparse / "images.bin")
    if len(model_images) < 6:
        raise ValueError(f"Only {len(model_images)} photos registered; at least 6 are needed")
    if (dense / "fused.ply").is_file():
        import trimesh
        points = np.asarray(trimesh.load(dense / "fused.ply").vertices)
    else:
        points = read_points3d(sparse / "points3D.bin")
    scene = Scene(cameras, model_images, points)
    photos = {image["name"]: cv2.imread(str(images / image["name"])) for image in model_images}
    missing = [name for name, photo in photos.items() if photo is None]
    if missing:
        raise ValueError(f"Cannot read {len(missing)} registered photos (e.g. {missing[0]})")
    if masks_dir:
        masks = {name: cv2.imread(str(masks_dir / f"{name}.png"), cv2.IMREAD_GRAYSCALE) > 127 for name in photos}
        source = "supplied masks"
    else:
        box = [[BOX_RADIUS * scene.orbit * np.cos(a), BOX_RADIUS * scene.orbit * np.sin(a), z]
               for a in np.linspace(0, 2 * np.pi, 24, endpoint=False) for z in (0, BOX_HEIGHT * scene.orbit)]
        masks = grabcut_masks(scene, photos, scene.world(box))
        volume, _, extent = carve(scene, masks, grid=96)
        steps = (np.arange(96) + .5) / 96
        r, h = extent
        cells = np.argwhere(volume)
        region = np.c_[-r + 2 * r * steps[cells[:, 0]], -r + 2 * r * steps[cells[:, 1]], h * steps[cells[:, 2]]]
        masks = grabcut_masks(scene, photos, scene.world(region[:: max(1, len(region) // 4000)]))
        source = "GrabCut outlines (2 passes)"
    volume, score, extent = carve(scene, masks)
    mesh = mesh_volume(score, extent)
    coloured = colour(mesh, scene, photos, masks)
    mesh.apply_translation([0, 0, -mesh.bounds[0, 2]])  # Stand it on z = 0.
    export = y_up(mesh)
    for extension in ("glb", "obj"):
        export.export(output / f"{stem}.{extension}")
    touching = bool(volume[[0, -1]].any() or volume[:, [0, -1]].any() or volume[:, :, -1].any())
    return dict(faces=int(len(mesh.faces)), photos=len(model_images), masks=source, coloured_fraction=round(coloured, 3),
                size_relative_to_orbit=[round(float(v), 3) for v in mesh.extents / scene.orbit], clipped=touching)
