import os
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import reconstruction
from backend.app.config import Settings, load_settings
from backend.app.images import normalize_photo


class ConfigurationTests(unittest.TestCase):
    def test_safe_default_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            path = Path(root) / "config.toml"
            self.assertEqual(load_settings(path).mode, "capture")
            for content in ('mode = "cloud"', 'max_photos = 0', 'timeout_seconds = "forever"', 'unknown = 3',
                            'lan = "yes"', 'port = 80', 'tls_cert = ""'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    load_settings(path)
            path.write_text('mode = "reconstruct"\ncolmap = \'C:\\Tools\\COLMAP.exe\'')
            self.assertEqual(load_settings(path).colmap, r"C:\Tools\COLMAP.exe")

    def test_import_orientation_normalization_and_dimension_limit(self):
        image = Image.new('RGB', (640, 480))
        exif = image.getexif()
        exif[274] = 6
        data = io.BytesIO()
        image.save(data, format='JPEG', exif=exif)
        normalized, suffix = normalize_photo(data.getvalue())
        self.assertEqual(suffix, '.jpg')
        with Image.open(io.BytesIO(normalized)) as working:
            self.assertEqual(working.size, (480, 640))
        data = io.BytesIO()
        Image.new('RGB', (9000, 32)).save(data, format='JPEG')
        with self.assertRaises(ValueError):
            normalize_photo(data.getvalue())


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
