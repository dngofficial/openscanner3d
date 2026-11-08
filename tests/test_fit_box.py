"""Search box fitted to the points standing on the table (small object, cameras far back, clutter nearby)."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.app import ai_masks, silhouette
from test_silhouette import build_scene


def scene_with(points):
    with tempfile.TemporaryDirectory() as temp:
        workspace, _ = build_scene(Path(temp))
        cameras = silhouette.read_cameras(workspace / "sparse/0/cameras.bin")
        images = silhouette.read_images(workspace / "sparse/0/images.bin")
    return silhouette.Scene(cameras, images, points)


def table(rng, count=20000):
    return np.c_[rng.uniform(-2, 2, (count, 2)), np.zeros(count)]


def can(rng, centre=(0.0, 0.0), radius=.12, tall=.35, count=1500):
    angle, z = rng.uniform(0, 2 * np.pi, count), rng.uniform(0, tall, count)
    return np.c_[centre[0] + radius * np.cos(angle), centre[1] + radius * np.sin(angle), z]


class FitBoxTests(unittest.TestCase):
    def test_small_object_gets_a_tight_box_and_clutter_is_ignored(self):
        rng = np.random.default_rng(1)
        sofa = np.c_[rng.uniform(1.6, 2.0, (800, 1)), rng.uniform(-.5, .5, (800, 1)), rng.uniform(.1, .8, (800, 1))]
        scene = scene_with(np.vstack([table(rng), can(rng, centre=(.05, -.04)), sofa]))
        self.assertTrue(scene.box_fitted)
        self.assertLess(scene.box_radius, .3 * scene.orbit)        # Tighter than the fixed box.
        self.assertGreater(scene.box_radius, .12)                   # Still wider than the can.
        self.assertGreater(scene.box_height, .35)                   # Taller than the can.
        self.assertLess(np.hypot(*(scene.origin[:2] - [.05, -.04])), .03)  # Centred on the can, not the sofa.
        self.assertIsNotNone(scene.object_points)
        self.assertLess(np.abs(scene.object_points[:, 0]).max(), .3)  # Seed points are the can's, not the sofa's.

    def test_points_only_on_top_keep_the_box_seed_and_few_points_keep_the_fixed_box(self):
        rng = np.random.default_rng(2)
        lid = can(rng, tall=.02) + [0, 0, .5]
        scene = scene_with(np.vstack([table(rng), lid]))
        self.assertIsNone(scene.object_points)                      # A cap says nothing about the body below.
        bare = scene_with(table(rng))
        self.assertFalse(bare.box_fitted)
        self.assertAlmostEqual(bare.box_radius, silhouette.BOX_RADIUS * bare.orbit)

    def test_grow_box_stops_at_the_fixed_box(self):
        rng = np.random.default_rng(3)
        scene = scene_with(np.vstack([table(rng), can(rng)]))
        while scene.grow_box():
            pass
        self.assertAlmostEqual(scene.box_radius, silhouette.BOX_RADIUS * scene.orbit)
        self.assertAlmostEqual(scene.box_height, silhouette.BOX_HEIGHT * scene.orbit)

    def test_measured_camera_angles_match_the_synthetic_rig_and_fill_missing_poses(self):
        from backend.app import coverage
        from backend.app.reconstruction import fill_angles
        with tempfile.TemporaryDirectory() as temp:
            workspace, images = build_scene(Path(temp))
            angles = silhouette.camera_angles(silhouette.load_scene(workspace))
            # Ring 0: 8 cameras 45 degrees apart, 3.0 out and 1.6 up, aimed at height 0.45 (~21 degrees down).
            self.assertEqual(angles["kf_0000.jpg"][0], 0)
            self.assertAlmostEqual(abs(angles["kf_0001.jpg"][0]), 45, delta=2)
            self.assertAlmostEqual(angles["kf_0000.jpg"][1], 21, delta=2)
            self.assertAlmostEqual(angles["kf_0008.jpg"][1], 42, delta=2)   # Ring 1: 2.4 out, 2.6 up.
            scan = Path(temp)
            poses = [dict(name=name, azimuth=None, pitch=None, ring=None, sector=None) for name in sorted(angles)]
            poses[0].update(azimuth=10.0, pitch=30.0, ring=1, sector=0)     # Phone angles are kept.
            (scan / "poses.json").write_text(json.dumps(poses))
            self.assertEqual(fill_angles(scan, workspace), 15)
            filled = json.loads((scan / "poses.json").read_text())
            self.assertEqual(filled[0]["azimuth"], 10.0)
            self.assertEqual(filled[1]["angle_source"], "colmap")
            self.assertEqual(coverage.summarise(filled)["unguided"], 0)

    def test_masks_cached_by_an_older_version_are_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            ai_masks.save_masks({"a.jpg": np.ones((4, 4), bool)}, dict(method="GrabCut"), folder)
            self.assertIsNotNone(ai_masks.load_masks(folder, ["a.jpg"]))
            info = json.loads((folder / "masks.json").read_text())
            info["version"] = 1
            (folder / "masks.json").write_text(json.dumps(info))
            self.assertIsNone(ai_masks.load_masks(folder, ["a.jpg"]))


if __name__ == "__main__":
    unittest.main()
