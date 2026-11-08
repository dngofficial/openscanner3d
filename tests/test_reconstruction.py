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
