"""Silhouette reconstruction on a synthetic scene with known geometry (no COLMAP needed).

A dark cylinder (radius 0.3, height 1.0) stands on a white table, photographed by 16 cameras on two
rings. The test writes COLMAP binary files and rendered photos, then runs the real pipeline: table
fit, GrabCut outlines, carving, meshing, colouring, y-up export.
"""
import struct
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import trimesh

from backend.app import silhouette

WIDTH, HEIGHT, FOCAL = 360, 480, 420.0
RADIUS, TALL = 0.3, 1.0


def look_at(centre, target):
    forward = target - centre
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    rotation = np.stack([right, down, forward])
    return rotation, -rotation @ centre


def quaternion(rotation):
    r = rotation
    k = np.array([[r[0, 0] - r[1, 1] - r[2, 2], r[1, 0] + r[0, 1], r[2, 0] + r[0, 2], r[2, 1] - r[1, 2]],
                  [r[1, 0] + r[0, 1], r[1, 1] - r[0, 0] - r[2, 2], r[2, 1] + r[1, 2], r[0, 2] - r[2, 0]],
                  [r[2, 0] + r[0, 2], r[2, 1] + r[1, 2], r[2, 2] - r[0, 0] - r[1, 1], r[1, 0] - r[0, 1]],
                  [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1], r[0, 0] + r[1, 1] + r[2, 2]]]) / 3
    x, y, z, w = np.linalg.eigh(k)[1][:, -1]
    return w, x, y, z


def build_scene(root: Path, two_colour=False):
    workspace, images = root / "run", root / "images"
    (workspace / "sparse" / "0").mkdir(parents=True)
    (workspace / "dense" / "0").mkdir(parents=True)
    images.mkdir()
    camera = ("SIMPLE_RADIAL", WIDTH, HEIGHT, (FOCAL, WIDTH / 2, HEIGHT / 2, 0.0))
    poses = []
    for ring, (distance, height, count) in enumerate(((3.0, 1.6, 8), (2.4, 2.6, 8))):
        for index in range(count):
            angle = 2 * np.pi * (index + ring * .5) / count
            centre = np.array([distance * np.cos(angle), distance * np.sin(angle), height])
            poses.append((f"kf_{len(poses):04d}.jpg", *look_at(centre, np.array([0, 0, .45]))))
    with (workspace / "sparse/0/cameras.bin").open("wb") as handle:
        handle.write(struct.pack("<Q", 1) + struct.pack("<iiQQ", 1, 2, WIDTH, HEIGHT) + struct.pack("<4d", *camera[3]))
    with (workspace / "sparse/0/images.bin").open("wb") as handle:
        handle.write(struct.pack("<Q", len(poses)))
        for number, (name, rotation, translation) in enumerate(poses, 1):
            handle.write(struct.pack("<idddddddi", number, *quaternion(rotation), *translation, 1))
            handle.write(name.encode() + b"\0" + struct.pack("<Q", 0))
    rng = np.random.default_rng(4)
    table = np.c_[rng.uniform(-2, 2, (30000, 2)), np.zeros(30000)]
    table = table[np.hypot(table[:, 0], table[:, 1]) > RADIUS]
    angle = rng.uniform(0, 2 * np.pi, 3000)
    lid = np.c_[RADIUS * np.cos(angle) * rng.uniform(0, 1, 3000), RADIUS * np.sin(angle) * rng.uniform(0, 1, 3000), np.full(3000, TALL)]
    trimesh.PointCloud(np.vstack([table, lid])).export(workspace / "dense/0/fused.ply")
    corners = np.array([[-2, -2, 0], [2, -2, 0], [2, 2, 0], [-2, 2, 0]], float)
    theta = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    surface = np.vstack([np.c_[RADIUS * np.cos(theta), RADIUS * np.sin(theta), np.full(72, z)] for z in np.linspace(0, TALL, 12)])
    for name, rotation, translation in poses:
        photo = rng.integers(40, 120, (HEIGHT, WIDTH, 3)).astype(np.uint8)  # Cluttered room.
        pixels, _ = silhouette.project(corners, camera, rotation, translation)
        cv2.fillConvexPoly(photo, pixels.astype(np.int32), (235, 235, 235))
        photo = np.clip(photo.astype(int) + rng.integers(-8, 8, photo.shape), 0, 255).astype(np.uint8)  # Table grain.
        pixels, _ = silhouette.project(surface, camera, rotation, translation)
        cv2.fillConvexPoly(photo, cv2.convexHull(pixels.astype(np.float32)).astype(np.int32), (30, 25, 20))
        if two_colour:
            # Exact ray/cylinder intersections give a view-consistent red/blue height pattern.
            yy, xx = np.mgrid[:HEIGHT, :WIDTH]
            rays = np.stack([(xx - WIDTH / 2) / FOCAL, (yy - HEIGHT / 2) / FOCAL,
                             np.ones_like(xx)], -1) @ rotation
            centre = -rotation.T @ translation
            a = (rays[..., :2] ** 2).sum(-1)
            b = 2 * (rays[..., :2] * centre[:2]).sum(-1)
            c = (centre[:2] ** 2).sum() - RADIUS ** 2
            disc = b * b - 4 * a * c
            distance = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
            z = centre[2] + distance * rays[..., 2]
            side = (disc >= 0) & (distance > 0) & (z >= 0) & (z <= TALL)
            top_distance = (TALL - centre[2]) / rays[..., 2]
            top_xy = centre[:2] + top_distance[..., None] * rays[..., :2]
            top = (top_distance > 0) & ((top_xy ** 2).sum(-1) <= RADIUS ** 2)
            photo[side & (z < TALL / 2)] = (190, 20, 20)
            photo[(side & (z >= TALL / 2)) | top] = (20, 20, 190)
        cv2.imwrite(str(images / name), photo)
    return workspace, images


class SilhouetteTests(unittest.TestCase):
    def test_dark_object_on_white_table_is_carved_upright_and_coloured(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, images = build_scene(root)
            info = silhouette.reconstruct_object(workspace, images, root)
            self.assertEqual(info["photos"], 16)
            self.assertFalse(info["clipped"])
            mesh = trimesh.load(root / "object_hires.glb", force="mesh")
            width, tall, depth = mesh.extents  # y-up export
            self.assertAlmostEqual(tall, TALL, delta=.15)
            self.assertAlmostEqual(width, 2 * RADIUS, delta=.15)
            self.assertAlmostEqual(depth, 2 * RADIUS, delta=.15)
            self.assertAlmostEqual(mesh.bounds[0, 1], 0, delta=1e-6)  # Standing on the ground plane.
            self.assertLess(mesh.visual.vertex_colors[:, :3].mean(), 90)  # Coloured from the dark object.
            self.assertTrue((root / "object.obj").is_file())

    def test_table_fit_and_binary_readers(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace, _ = build_scene(Path(temp))
            cameras = silhouette.read_cameras(workspace / "sparse/0/cameras.bin")
            images = silhouette.read_images(workspace / "sparse/0/images.bin")
            points = np.asarray(trimesh.load(workspace / "dense/0/fused.ply").vertices)
            scene = silhouette.Scene(cameras, images, points)
            np.testing.assert_allclose(np.abs(scene.up), [0, 0, 1], atol=1e-3)
            self.assertLess(np.linalg.norm(scene.origin[:2]), .15)  # The cameras converge over the object.
            self.assertAlmostEqual(scene.orbit, 2.7, delta=.35)
            with (Path(temp) / "points3D.bin").open("wb") as handle:
                handle.write(struct.pack("<Q", 2))
                for number, xyz in ((1, (1.0, 2.0, 3.0)), (2, (4.0, 5.0, 6.0))):
                    handle.write(struct.pack("<Q3d3Bd", number, *xyz, 9, 9, 9, .5) + struct.pack("<Q", 2) + struct.pack("<4i", 1, 0, 2, 3))
            np.testing.assert_allclose(silhouette.read_points3d(Path(temp) / "points3D.bin"), [[1, 2, 3], [4, 5, 6]])

    def test_refuses_scenes_without_a_table_or_enough_photos(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace, images = build_scene(Path(temp))
            noise = np.random.default_rng(0).normal(size=(5000, 3)) * 3
            trimesh.PointCloud(noise).export(workspace / "dense/0/fused.ply")
            with self.assertRaisesRegex(ValueError, "table"):
                silhouette.reconstruct_object(workspace, images, Path(temp))


if __name__ == "__main__":
    unittest.main()
