"""SAM 2 mask orchestration: prompts, tap geometry, per-photo fallback, caching, previews.

SAM 2 itself is replaced by a stand-in here (its output was validated on a real 36-photo scan; see
README). These tests check the geometry and the fallback rules around it on a synthetic scene.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import trimesh

from backend.app import ai_masks, silhouette
from backend.app.config import Settings
from backend.app.game_asset import decimate
from test_silhouette import build_scene


def dark_pixels(images: Path, items):
    """Stand-in for SAM 2: the synthetic object is the only near-black region in each photo."""
    masks = {}
    for item in items:
        photo = cv2.imread(str(images / item["name"]))
        mask = cv2.morphologyEx((photo.max(2) < 36).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        masks[item["name"]] = mask > 0
    return masks


class MergeTests(unittest.TestCase):
    def test_sam2_mask_used_only_where_it_agrees_with_grabcut(self):
        grabcut = np.zeros((100, 100), bool)
        grabcut[30:70, 40:60] = True
        close = np.zeros_like(grabcut)
        close[28:72, 38:62] = True
        everything = np.ones_like(grabcut)
        elsewhere = np.zeros_like(grabcut)
        elsewhere[0:10, 0:10] = True
        chosen, source = ai_masks.merge({"a": close, "b": everything, "c": elsewhere},
                                        {"a": grabcut, "b": grabcut, "c": grabcut, "d": grabcut})
        self.assertEqual(source, {"a": "sam2", "b": "grabcut", "c": "grabcut", "d": "grabcut"})
        self.assertIs(chosen["a"], close)
        self.assertIs(chosen["b"], grabcut)

    def test_missing_environment_is_reported_not_raised(self):
        settings = Settings(segmentation_python="no/such/python.exe")
        python, _, problem = ai_masks.sam2_tools(settings)
        self.assertIsNone(python)
        self.assertIn("SAM 2 environment not found", problem)


class SyntheticSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.workspace, cls.images = build_scene(cls.root)
        cameras = silhouette.read_cameras(cls.workspace / "sparse/0/cameras.bin")
        images = silhouette.read_images(cls.workspace / "sparse/0/images.bin")
        points = np.asarray(trimesh.load(cls.workspace / "dense/0/fused.ply").vertices)
        cls.scene = silhouette.Scene(cameras, images, points)
        # Rough object region: the synthetic cylinder's volume (radius .3, height 1) sampled coarsely.
        grid = np.stack(np.meshgrid(np.linspace(-.3, .3, 9), np.linspace(-.3, .3, 9), np.linspace(0, 1, 11)), -1).reshape(-1, 3)
        cls.region = grid[np.hypot(grid[:, 0], grid[:, 1]) <= .3]
        cls.photos = {im["name"]: cv2.imread(str(cls.images / im["name"])) for im in images}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_tap_ray_finds_the_object_and_background_taps_are_ignored(self):
        first = self.scene.images[0]
        camera = self.scene.cameras[first["camera"]]
        pixel, _ = silhouette.project(np.array([[0, 0, .7]]), camera, first["R"], first["t"])
        tap = [pixel[0, 0] / camera[1], pixel[0, 1] / camera[2]]
        centre, used = ai_masks.object_centre(self.scene, [dict(name=first["name"], tap=tap)], self.region)
        self.assertEqual(used, first["name"])
        self.assertLess(np.hypot(*centre[:2]), .12)  # On the object's axis (the ray crosses its depth).
        _, used = ai_masks.object_centre(self.scene, [dict(name=first["name"], tap=[.02, .02])], self.region)
        self.assertIsNone(used)

    def test_prompts_are_per_photo_boxes_containing_the_projected_centre(self):
        items = ai_masks.prompts(self.scene, self.photos, self.region, self.region.mean(0))
        self.assertEqual(len(items), 16)
        for item in items:
            x0, y0, x1, y1 = item["box"]
            self.assertTrue(0 <= x0 < x1 < 360 and 0 <= y0 < y1 < 480)
            self.assertEqual(len(item["points"]), 1)
            x, y = item["points"][0]
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1)

    def test_pipeline_uses_sam2_masks_caches_them_and_falls_back_when_unavailable(self):
        out = self.root / "out"
        cache = self.root / "cache"
        settings = Settings(timeout_seconds=60)
        calls = []

        def fake_sam2(settings_, images, items, work, timeout):
            calls.append(len(items))
            return dark_pixels(images, items), dict(device="cpu", checkpoint="fake.pt", scores={})

        with patch.object(ai_masks, "run_sam2", side_effect=fake_sam2):
            info = silhouette.reconstruct_object(self.workspace, self.images, out, target_triangles=800, texture_size=256,
                                                 segment=ai_masks.make_segmenter(settings, self.root),
                                                 mask_cache=cache, previews=out / "previews")
        self.assertEqual(calls, [16])
        self.assertEqual(info["masks"]["method"], "SAM 2")
        self.assertEqual(info["masks"]["sam2"], 16)
        self.assertFalse(info["clipped"])
        width, tall, _ = trimesh.load(out / "object_hires.glb", force="mesh").extents
        self.assertAlmostEqual(tall, 1.0, delta=.15)
        self.assertAlmostEqual(width, .6, delta=.15)
        self.assertEqual(info["game_asset"]["triangles"], 800)
        self.assertTrue(info["game_asset"]["watertight"])
        self.assertEqual(len(list((out / "previews").glob("kf_*.jpg"))), 16)
        self.assertEqual(json.loads((out / "previews" / "masks.json").read_text())["sam2"], 16)

        # A detail-only rebuild reuses the cached masks; SAM 2 does not run again.
        with patch.object(ai_masks, "run_sam2", side_effect=AssertionError("SAM 2 should not run")):
            again = silhouette.reconstruct_object(self.workspace, self.images, self.root / "again", target_triangles=300,
                                                  texture_size=256, segment=ai_masks.make_segmenter(settings, self.root),
                                                  mask_cache=cache)
        self.assertTrue(again["masks"]["reused"])
        self.assertEqual(again["game_asset"]["triangles"], 300)

        # No SAM 2 environment: GrabCut outlines, with a visible note.
        missing = Settings(segmentation_python="no/such/python.exe", timeout_seconds=60)
        fallback = silhouette.reconstruct_object(self.workspace, self.images, self.root / "fallback", target_triangles=300,
                                                 texture_size=256, segment=ai_masks.make_segmenter(missing, self.root))
        self.assertEqual(fallback["masks"]["method"], "GrabCut")
        self.assertTrue(any("SAM 2 unavailable" in warning for warning in fallback["warnings"]))


class DecimationTests(unittest.TestCase):
    def test_low_poly_budgets_stay_closed(self):
        mesh = trimesh.creation.torus(1.0, .25, major_sections=160, minor_sections=64)
        for target in (300, 1000, 5000):
            low = decimate(mesh, target)
            self.assertLessEqual(len(low.faces), target)
            self.assertTrue(low.is_watertight, target)


if __name__ == "__main__":
    unittest.main()
