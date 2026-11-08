"""COLMAP adapter: mesh export, missing tool, exclusive execution, failed reruns, subprocess limits."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.app import reconstruction
from backend.app.config import Settings


class ReconstructionTests(unittest.TestCase):
    def test_actual_mesh_export_with_synthetic_colmap_output(self):
        import trimesh
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            def produce_mesh(command, log, timeout):
                workspace = Path(command[command.index('--workspace_path') + 1])
                target = workspace / 'dense/0/meshed-poisson.ply'
                target.parent.mkdir(parents=True)
                trimesh.creation.box().export(target)
                return 0
            with patch.object(reconstruction, 'resolve_tool', return_value='colmap'), \
                 patch.object(reconstruction, 'colmap_help'), \
                 patch.object(reconstruction, 'run_logged', side_effect=produce_mesh):
                result = reconstruction.reconstruct(output / 'images', output, Settings(mode='reconstruct'))
            self.assertTrue(result['model_ready'])
            self.assertGreater(len(trimesh.load(output / 'mesh.glb', force='mesh').faces), 0)
            self.assertTrue((output / 'mesh.obj').is_file())

    def test_object_failure_keeps_scene_mesh_and_drops_stale_object(self):
        import trimesh
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            (output / 'object.glb').write_bytes(b'from an earlier run')
            def produce_mesh(command, log, timeout):
                target = Path(command[command.index('--workspace_path') + 1]) / 'dense/0/meshed-poisson.ply'
                target.parent.mkdir(parents=True)
                trimesh.creation.box().export(target)
                return 0  # No sparse model, so the silhouette step cannot run.
            with patch.object(reconstruction, 'resolve_tool', return_value='colmap'),                  patch.object(reconstruction, 'colmap_help'),                  patch.object(reconstruction, 'run_logged', side_effect=produce_mesh):
                result = reconstruction.reconstruct(output / 'images', output, Settings(mode='reconstruct'))
            self.assertTrue(result['model_ready'])
            self.assertIn('Object reconstruction failed', result['msg'])
            self.assertFalse((output / 'object.glb').exists())

    def test_empty_poisson_mesh_still_runs_object_step(self):
        def empty_mesh(command, log, timeout):
            workspace = Path(command[command.index('--workspace_path') + 1])
            (workspace / 'sparse/0').mkdir(parents=True)
            (workspace / 'sparse/0/images.bin').write_bytes(b'poses')
            target = workspace / 'dense/0/meshed-poisson.ply'
            target.parent.mkdir(parents=True)
            target.write_text('ply\nformat ascii 1.0\nelement vertex 0\nproperty float x\nproperty float y\n'
                              'property float z\nelement face 0\nproperty list uchar int vertex_indices\nend_header\n')
            return 0
        info = dict(photos=30, faces=100, clipped=False, warnings=[], masks={}, game_asset=None)
        for object_ok in (True, False):
            with self.subTest(object_ok=object_ok), tempfile.TemporaryDirectory() as root:
                output = Path(root)
                (output / 'mesh.glb').write_bytes(b'older scene')
                (output / 'object.glb').write_bytes(b'older object')
                step = dict(return_value=info) if object_ok else dict(side_effect=ValueError('no table'))
                with patch.object(reconstruction, 'resolve_tool', return_value='colmap'), \
                     patch.object(reconstruction, 'colmap_help'), \
                     patch.object(reconstruction, 'run_logged', side_effect=empty_mesh), \
                     patch.object(reconstruction, 'object_step', **step):
                    result = reconstruction.reconstruct(output / 'images', output, Settings(mode='reconstruct'))
                self.assertIn('Scene mesh skipped', result['msg'])
                if object_ok:
                    self.assertTrue(result['model_ready'])
                    self.assertFalse((output / 'mesh.glb').exists())  # Not paired with the new object.
                else:
                    self.assertFalse(result['done'])
                    self.assertEqual((output / 'mesh.glb').read_bytes(), b'older scene')
                    self.assertEqual((output / 'object.glb').read_bytes(), b'older object')

    def test_scan_zip_folder_keeps_phone_angles_and_tap(self):
        import json
        from scripts.reconstruct import carry_poses
        with tempfile.TemporaryDirectory() as root:
            extracted, output = Path(root) / 'zip', Path(root) / 'scan'
            (extracted / 'images').mkdir(parents=True); output.mkdir()
            files = [extracted / 'images' / name for name in ('kf_0000.jpg', 'kf_0001.jpg', 'extra.jpg')]
            (extracted / 'poses.json').write_text(json.dumps([
                dict(name='kf_0000.jpg', azimuth=0, pitch=35, ring=1, sector=0, tap=[.5, .4]),
                dict(name='kf_0001.jpg', azimuth=30, pitch=35, ring=1, sector=1)]))
            carry_poses(extracted / 'images', files, output)
            poses = json.loads((output / 'poses.json').read_text())
            self.assertEqual([pose['name'] for pose in poses], ['kf_0000.jpg', 'kf_0001.jpg', 'kf_0002.jpg'])
            self.assertEqual(poses[0]['tap'], [.5, .4])
            self.assertIsNone(poses[2]['azimuth'])  # A photo the phone did not record gets no invented angle.

    def test_missing_executable_and_exclusive_execution(self):
        with patch.object(reconstruction, 'resolve_tool', return_value=None):
            result = reconstruction.reconstruct(Path('images'), Path('output'), Settings(mode='reconstruct'))
            self.assertFalse(result['done'])
        with patch.object(reconstruction, 'resolve_tool', return_value='colmap'):
            reconstruction._heavy_job.acquire()
            try:
                result = reconstruction.reconstruct(Path('images'), Path('output'), Settings(mode='reconstruct'))
                self.assertIn('Another reconstruction', result['msg'])
            finally:
                reconstruction._heavy_job.release()

    def test_failed_rerun_preserves_successful_export(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)
            (output / 'mesh.glb').write_bytes(b'previous export')
            with patch.object(reconstruction, 'resolve_tool', return_value='colmap'), \
                 patch.object(reconstruction, 'colmap_help'), \
                 patch.object(reconstruction, 'run_logged', return_value=1):
                result = reconstruction.reconstruct(output / 'images', output, Settings(mode='reconstruct'))
            self.assertFalse(result['done'])
            self.assertEqual((output / 'mesh.glb').read_bytes(), b'previous export')
            self.assertFalse(reconstruction._heavy_job.locked())

    def test_real_subprocess_logs_and_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            log = Path(root) / 'process.log'
            code = reconstruction.run_logged([sys.executable, '-c', 'print("logged")'], log, 5)
            self.assertEqual(code, 0)
            self.assertIn('logged', log.read_text())
            with self.assertRaises(subprocess.TimeoutExpired):
                reconstruction.run_logged([sys.executable, '-c', 'import time; time.sleep(30)'], log, 1)


if __name__ == '__main__':
    unittest.main()
