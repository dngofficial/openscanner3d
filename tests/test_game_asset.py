"""Real simplification, UV unwrapping and photo baking on a rendered COLMAP scene."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh

from backend.app import silhouette
from backend.app.game_asset import asset_frame, decimate
from test_silhouette import build_scene


class GameAssetTests(unittest.TestCase):
    def test_photo_bake_and_exports(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, photos = build_scene(root, two_colour=True)
            with patch.object(silhouette, 'GRID', 96):
                # carve's default is bound at import, so set it explicitly via a wrapper.
                original = silhouette.carve
                with patch.object(silhouette, 'carve', side_effect=lambda s, m, grid=96: original(s, m, grid)):
                    info = silhouette.reconstruct_object(workspace, photos, root,
                                                         target_triangles=1000, texture_size=512, height_cm=25)
            self.assertEqual(info['warnings'], [])
            stats = info['game_asset']
            self.assertLessEqual(stats['triangles'], 1000)
            self.assertGreater(stats['coverage'], .75)
            self.assertTrue(stats['watertight'])
            mesh = trimesh.load(root / 'object.glb', force='mesh')
            self.assertIsInstance(mesh.visual, trimesh.visual.TextureVisuals)
            self.assertEqual(mesh.visual.uv.shape, (len(mesh.vertices), 2))
            texture = np.asarray(mesh.visual.material.baseColorTexture)
            self.assertEqual(texture.shape[:2], (512, 512))
            self.assertAlmostEqual(mesh.extents[1], .25, places=5)
            self.assertAlmostEqual(mesh.bounds[0, 1], 0, places=6)
            np.testing.assert_allclose(mesh.bounds.mean(0)[[0, 2]], 0, atol=1e-6)
            uv = mesh.visual.uv[mesh.faces].mean(1)
            col = np.clip((uv[:, 0] * 512).astype(int), 0, 511)
            row = np.clip(((1 - uv[:, 1]) * 512).astype(int), 0, 511)
            rgb = texture[row, col, :3].astype(float)
            height = mesh.triangles_center[:, 1] / mesh.extents[1]
            top = rgb[(height > .65) & (height < .9)].mean(0)
            bottom = rgb[(height > .15) & (height < .35)].mean(0)
            self.assertGreater(top[0], top[2] * 3, top)
            self.assertGreater(bottom[2], bottom[0] * 3, bottom)
            self.assertTrue((root / 'object_hires.glb').is_file())
            self.assertTrue((root / 'object_albedo.png').is_file())
            self.assertTrue((root / 'object.mtl').is_file())
            obj = trimesh.load(root / 'object.obj', force='mesh')
            self.assertIsInstance(obj.visual, trimesh.visual.TextureVisuals)
            self.assertIsNotNone(obj.visual.material.image)
            self.assertLess(time.monotonic() - started, 30)

    def test_decimation_shape_and_scaling(self):
        mesh = trimesh.creation.icosphere(subdivisions=4)
        low = decimate(mesh, 500)
        self.assertLessEqual(len(low.faces), 500)
        self.assertTrue(low.is_watertight)
        np.testing.assert_allclose(low.extents, mesh.extents, atol=.08)
        self.assertAlmostEqual(asset_frame(low, 30).extents[1], .3)
        with self.assertRaises(ValueError):
            asset_frame(low, -1)
        with self.assertRaises(ValueError):
            decimate(mesh, 0)

    def test_bake_failure_retains_hires_and_removes_stale_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace, photos = build_scene(root)
            for name in ('object.glb', 'object.obj', 'object.mtl', 'object_albedo.png'):
                (root / name).write_bytes(b'stale')
            original = silhouette.carve
            with patch.object(silhouette, 'carve', side_effect=lambda s, m, grid=96: original(s, m, grid)), \
                 patch('backend.app.game_asset.build_game_asset', side_effect=RuntimeError('bake failed')):
                info = silhouette.reconstruct_object(workspace, photos, root)
            self.assertIsNone(info['game_asset'])
            self.assertIn('bake failed', info['warnings'][0])
            self.assertGreater(len(trimesh.load(root / 'object_hires.glb', force='mesh').faces), 0)
            for name in ('object.glb', 'object.obj', 'object.mtl', 'object_albedo.png'):
                self.assertFalse((root / name).exists())


if __name__ == '__main__':
    unittest.main()
