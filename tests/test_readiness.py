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

from backend.app import main as server
from backend.app import reconstruction
from backend.app.config import Settings, load_settings
from backend.app.images import normalize_photo


class ConfigurationTests(unittest.TestCase):
    def test_safe_default_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            path = Path(root) / "config.toml"
            self.assertEqual(load_settings(path).mode, "capture")
            for content in ('mode = "cloud"', 'max_photos = 0', 'timeout_seconds = "forever"', 'unknown = 3'):
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


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scans = patch.object(server, "SCANS", Path(self.temp.name))
        self.scans.start(); self.addCleanup(self.scans.stop)
        self.settings = patch.object(server, "SETTINGS", Settings())
        self.settings.start(); self.addCleanup(self.settings.stop)

    @staticmethod
    def jpeg(width=640, height=480):
        return cv2.imencode('.jpg', np.full((height, width, 3), 100, np.uint8))[1].tobytes()

    def test_import_without_camera_and_capture_finish(self):
        with TestClient(server.app) as client, patch.object(reconstruction, "resolve_tool") as resolve:
            self.assertEqual(client.get('/camera.mjs').status_code, 200)
            with client.websocket_connect('/ws') as ws:
                ready = ws.receive_json()
                for _ in range(12):
                    ws.send_text('import')
                    self.assertEqual(ws.receive_json()['type'], 'import')
                    ws.send_bytes(self.jpeg())
                    self.assertTrue(ws.receive_json()['accepted'])
                ws.send_text('finish')
                review = ws.receive_json()
                self.assertEqual(review['type'], 'review')
                ws.send_json({'type': 'confirm', 'selected': review['photos']})
                self.assertEqual(ws.receive_json()['type'], 'busy')
                result = ws.receive_json()
                self.assertTrue(result['done'])
                self.assertFalse(result['model_ready'])
                self.assertNotIn('mesh.glb', result['files'])
            self.assertEqual(len(list((Path(self.temp.name) / ready['scan_id'] / 'images').glob('*.jpg'))), 12)
            resolve.assert_not_called()

    def test_invalid_image_orientation_and_limit(self):
        scan = server.Scan()
        self.assertFalse(scan.import_photo(b'not an image')['accepted'])
        self.assertTrue(scan.import_photo(self.jpeg())['accepted'])
        result = scan.import_photo(self.jpeg(480, 640))
        self.assertFalse(result['accepted'])
        self.assertIn('orientation', result['msg'])
        with patch.object(server, 'SETTINGS', Settings(max_photos=1)):
            self.assertFalse(scan.import_photo(self.jpeg())['accepted'])
        self.assertEqual(len(scan.photos), 1)

    def test_non_jpeg_rejected(self):
        image = cv2.imencode('.png', np.zeros((480, 640, 3), np.uint8))[1].tobytes()
        active = server.Scan(); active.state = 'capturing'
        self.assertFalse(active.process(image)['accepted'])
        scan = server.Scan()
        self.assertTrue(scan.import_photo(image)['accepted'])
        self.assertEqual((scan.out / 'originals/kf_0000.png').read_bytes(), image)
        self.assertTrue((scan.img / 'kf_0000.jpg').is_file())


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
