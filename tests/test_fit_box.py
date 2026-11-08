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
