"""Local photo baking. Geometry stays in the reconstruction frame until after baking.

Quadric edge-collapse simplification and xatlas unwrap run entirely on the CPU.
Coverage counts photo-observed surface texels before filling, excluding atlas padding.
Facing and mask tests are not a depth/occlusion test for hidden concave surfaces.
"""
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage
import trimesh


def decimate(mesh, target):
    import fast_simplification
    if not isinstance(target, int) or target < 16:
        raise ValueError("target_triangles must be an integer of at least 16")
    if len(mesh.faces) <= target:
        return mesh.copy()
    vertices, faces = fast_simplification.simplify(
        np.asarray(mesh.vertices), np.asarray(mesh.faces), target_count=target)
    result = trimesh.Trimesh(vertices, faces, process=True)
    if not len(result.faces) or len(result.faces) > target:
        raise ValueError("Simplification did not meet the triangle budget")
    result.fix_normals()
    return result


def unwrap(mesh, size, padding=4):
    import xatlas
    atlas = xatlas.Atlas()
    atlas.add_mesh(np.asarray(mesh.vertices, np.float32), np.asarray(mesh.faces, np.uint32))
    options = xatlas.PackOptions()
    options.resolution = size
    options.padding = padding
    options.bilinear = True
    atlas.generate(pack_options=options)
    mapping, faces, uv = atlas[0]
    out = trimesh.Trimesh(mesh.vertices[mapping], faces, process=False,
                          vertex_normals=mesh.vertex_normals[mapping])
    pixels = uv.copy() * size
    pixels[:, 1] = size - pixels[:, 1]
    # Pixel centres lie at integer coordinates for the rasteriser.
    return out, uv, pixels[faces] - .5


def bake(mesh, pixels, scene, photos, masks, size):
    from .silhouette import project

    face_map = np.full((size, size), -1, np.int32)
    bary = np.zeros((size, size, 2), np.float32)
    for i, triangle in enumerate(pixels):
        lo = np.maximum(np.ceil(triangle.min(0)).astype(int), 0)
        hi = np.minimum(np.floor(triangle.max(0)).astype(int), size - 1)
        if np.any(hi < lo):
            continue
        yy, xx = np.mgrid[lo[1]:hi[1] + 1, lo[0]:hi[0] + 1]
        v0, v1 = triangle[1] - triangle[0], triangle[2] - triangle[0]
        determinant = v0[0] * v1[1] - v0[1] * v1[0]
        if abs(determinant) < 1e-10:
            continue
        dx, dy = xx - triangle[0, 0], yy - triangle[0, 1]
        u = (dx * v1[1] - dy * v1[0]) / determinant
        v = (v0[0] * dy - v0[1] * dx) / determinant
        inside = (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)
        rows, cols = yy[inside], xx[inside]
        face_map[rows, cols] = i
        bary[rows, cols] = np.column_stack([u[inside], v[inside]])
    rows, cols = np.nonzero(face_map >= 0)
    texture = np.zeros((size, size, 3), np.uint8)
    observed = np.zeros((size, size), bool)
    prepared = []
    for image in scene.images:
        photo = photos[image['name']]
        shrink = max(3, photo.shape[1] // 200) | 1
        mask = cv2.erode(masks[image['name']].astype(np.uint8), np.ones((shrink, shrink), np.uint8)) > 0
        prepared.append((image, photo, mask))
    frame = np.stack([scene.x, scene.y, scene.up])
    # Bounded sample array: do not allocate photos * entire 2048 atlas * RGB floats.
    for start in range(0, len(rows), 32768):
        r, c = rows[start:start + 32768], cols[start:start + 32768]
        faces = face_map[r, c]
        weights = np.c_[1 - bary[r, c].sum(1), bary[r, c]]
        local = (mesh.triangles[faces] * weights[:, :, None]).sum(1)
        world = scene.world(local)
        normals = mesh.face_normals[faces] @ frame
        samples = np.full((len(prepared), len(r), 3), np.nan, np.float32)
        for j, (image, photo, mask) in enumerate(prepared):
            rotation, translation = image['R'], image['t']
            toward = -rotation.T @ translation - world
            facing = (normals * toward).sum(1) > .3 * np.linalg.norm(toward, axis=1)
            projected, front = project(world, scene.cameras[image['camera']], rotation, translation)
            col, row = np.rint(projected).astype(np.int64).T
            ok = facing & front & (col >= 0) & (row >= 0) & (col < photo.shape[1]) & (row < photo.shape[0])
            ok[ok] &= mask[row[ok], col[ok]]
            samples[j, ok] = photo[row[ok], col[ok], ::-1]
        seen = np.isfinite(samples[:, :, 0]).any(0)
        if seen.any():
            texture[r[seen], c[seen]] = np.nanmedian(samples[:, seen], axis=0).astype(np.uint8)
            observed[r[seen], c[seen]] = True
    if not observed.any():
        raise ValueError("No surface texels received photo colour")
    coverage = float(observed.sum() / len(rows))
    filled = observed.copy()
    # Fill each connected UV island from its own observed texels before bleeding.
    islands, count = ndimage.label(face_map >= 0)
    for label, area in enumerate(ndimage.find_objects(islands), 1):
        inside = islands[area] == label
        known = observed[area] & inside
        if known.any():
            nearest = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
            patch = texture[area]
            patch[inside] = patch[tuple(nearest[:, inside])]
            filled[area] |= inside
    # Includes four-pixel bleed and fills completely unseen islands. Coverage stays honest.
    nearest = ndimage.distance_transform_edt(~filled, return_distances=False, return_indices=True)
    texture[~filled] = texture[tuple(nearest[:, ~filled])]
    return Image.fromarray(texture), coverage


def asset_frame(mesh, height_cm=None):
    from .silhouette import y_up
    out = mesh.copy()
    centre = out.bounds.mean(0)
    out.apply_translation([-centre[0], -centre[1], -out.bounds[0, 2]])
    if height_cm is not None:
        if not np.isfinite(height_cm) or height_cm <= 0:
            raise ValueError("height_cm must be positive and finite")
        out.apply_scale(height_cm / 100 / out.extents[2])
    return y_up(out)


def build_game_asset(mesh, scene, photos, masks, output: Path, stem='object',
                     target_triangles=5000, texture_size=2048, height_cm=None):
    started = time.monotonic()
    if not isinstance(texture_size, int) or not 64 <= texture_size <= 8192:
        raise ValueError("texture_size must be an integer between 64 and 8192")
    low = decimate(mesh, target_triangles)
    asset, uv, pixels = unwrap(low, texture_size)
    texture, coverage = bake(asset, pixels, scene, photos, masks, texture_size)
    material = trimesh.visual.material.PBRMaterial(name=f'{stem}_albedo', baseColorTexture=texture,
                                                   baseColorFactor=[255, 255, 255, 255],
                                                   metallicFactor=0, roughnessFactor=1)
    asset.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    asset = asset_frame(asset, height_cm)
    # Validate bytes before publishing the primary output.
    import io
    data = asset.export(file_type='glb')
    check = trimesh.load(io.BytesIO(data), file_type='glb', force='mesh')
    if (not len(check.faces) or not np.isfinite(check.vertices).all()
            or not isinstance(check.visual, trimesh.visual.TextureVisuals)
            or check.visual.uv is None or check.visual.material.baseColorTexture is None):
        raise ValueError("GLB did not retain UVs and its embedded texture")
    texture.save(output / f'{stem}_albedo.png')
    (output / f'{stem}.glb').write_bytes(data)
    warnings = []
    if not low.is_watertight:
        warnings.append('Reduced geometry is not watertight')
    try:
        obj = asset.copy()
        obj.visual.material = trimesh.visual.material.SimpleMaterial(image=texture, name=f'{stem}_albedo',
                                                                     diffuse=[255, 255, 255, 255])
        text, files = trimesh.exchange.obj.export_obj(obj, return_texture=True, mtl_name=f'{stem}.mtl')
        for name, content in files.items():
            (output / name).write_bytes(content)
        (output / f'{stem}.obj').write_text(text, encoding='utf-8')
    except Exception as error:
        (output / f'{stem}.obj').unlink(missing_ok=True)
        (output / f'{stem}.mtl').unlink(missing_ok=True)
        warnings.append(f'OBJ export skipped: {error}')
    return dict(triangles=len(low.faces), texture_size=[texture_size, texture_size],
                coverage=round(coverage, 4), seconds=round(time.monotonic() - started, 2),
                watertight=bool(low.is_watertight), reduction='quadric edge collapse',
                uv_method='xatlas', warnings=warnings)
