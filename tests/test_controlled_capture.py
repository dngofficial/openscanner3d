import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app import main as server
from backend.app.capture_quality import frame_problem
from backend.app.config import Settings


def textured(seed=7):
    rng = np.random.default_rng(seed)
    image = np.full((540, 960, 3), 110, np.uint8)
    for _ in range(600):
        x, y = rng.integers([12, 12], [948, 528])
        color = tuple(int(v) for v in rng.integers(30, 255, size=3))
        cv2.circle(image, (int(x), int(y)), int(rng.integers(2, 8)), color, -1)
    return image


def jpeg(image):
    return cv2.imencode('.jpg', image)[1].tobytes()


class ControlledCaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, value in (('SCANS', self.root), ('SETTINGS', Settings())):
            patcher = patch.object(server, target, value)
            patcher.start(); self.addCleanup(patcher.stop)

    def test_start_pause_resume_and_review_enforced_over_socket(self):
        image = jpeg(textured())
        with TestClient(server.app) as client, client.websocket_connect('/ws') as ws:
            ws.receive_json()
            ws.send_bytes(image)
            self.assertEqual(ws.receive_json()['kfs'], 0)
            ws.send_text('start_scan'); self.assertEqual(ws.receive_json()['state'], 'capturing')
            ws.send_bytes(image); self.assertEqual(ws.receive_json()['kfs'], 1)
            ws.send_text('pause'); self.assertEqual(ws.receive_json()['state'], 'paused')
            ws.send_text('capture'); ws.receive_json()
            ws.send_bytes(image); self.assertEqual(ws.receive_json()['kfs'], 1)
            ws.send_text('start_scan'); ws.receive_json()
            ws.send_text('capture'); ws.receive_json()
            ws.send_bytes(image); self.assertEqual(ws.receive_json()['kfs'], 2)
            ws.send_text('finish'); self.assertEqual(ws.receive_json()['type'], 'review')
            ws.send_bytes(image); self.assertEqual(ws.receive_json()['kfs'], 2)
            ws.send_json({'type':'confirm', 'selected':['../../private'] * 12})
            self.assertEqual(ws.receive_json()['type'], 'error')

    def test_continuity_rejects_scene_jump_and_blur_even_for_manual_capture(self):
        scan = server.Scan(); scan.state = 'capturing'
        original = textured()
        with patch.object(server.cv2, 'findEssentialMat', return_value=(None, None)):
            self.assertTrue(scan.process(jpeg(original))['accepted'])
            shifted = cv2.warpAffine(original, np.float32([[1,0,18],[0,1,0]]), (960,540))
            scan.force_capture = True
            self.assertTrue(scan.process(jpeg(shifted))['accepted'])
            scan.force_capture = True
            result = scan.process(jpeg(textured(999)))
            self.assertFalse(result['accepted'])
            self.assertEqual(len(scan.photos), 2)
            scan.force_capture = True
            self.assertFalse(scan.process(jpeg(cv2.GaussianBlur(original, (51,51), 15)))['accepted'])
            self.assertEqual(len(scan.photos), 2)

    def test_quality_rejections_name_one_specific_cause(self):
        gray = lambda image: cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        def problem(image):
            g = gray(image)
            return frame_problem(g, cv2.Laplacian(g, cv2.CV_64F).var(), g.std())
        # A bright white table (mean well above the old 225 cutoff) is fine while it is not clipped.
        noise = np.random.default_rng(3).normal(232, 14, (540, 960, 1))
        bright = np.repeat(np.clip(noise, 0, 249), 3, axis=2).astype(np.uint8)
        self.assertGreater(gray(bright).mean(), 225)
        self.assertIsNone(problem(bright))
        self.assertIsNone(problem(textured()))
        clipped = textured(); clipped[:, :500] = 255
        self.assertTrue(problem(clipped).startswith('Overexposed: 52%'))
        crushed = textured(); crushed[:, :500] = 0
        self.assertTrue(problem(crushed).startswith('Underexposed'))
        self.assertTrue(problem(np.full((540, 960, 3), 12, np.uint8)).startswith('Too dark'))
        blurry = problem(cv2.GaussianBlur(textured(), (51, 51), 15))
        self.assertTrue(blurry.startswith('Blurry (sharpness'))
        self.assertNotIn('exposure', blurry)
        self.assertTrue(problem(np.full((540, 960, 3), 128, np.uint8)).startswith('Blurry'))

    def test_confirm_snapshots_only_selected_and_keeps_excluded_originals(self):
        scan = server.Scan()
        for _ in range(13):
            self.assertTrue(scan.import_photo(jpeg(textured()))['accepted'])
        with self.assertRaises(ValueError):
            scan.finish(scan.photos)
        scan.review()
        with patch.object(server, 'reconstruct', return_value={'done':True, 'model_ready':False}) as runner:
            result = scan.finish(scan.photos[1:])
            received = runner.call_args.args[0]
            self.assertEqual(sorted(p.name for p in received.glob('*.jpg')), scan.photos[1:])
            self.assertEqual(len(list(scan.img.glob('*.jpg'))), 13)
            self.assertEqual(result['selected_count'], 12)
        with TestClient(server.app) as client:
            self.assertEqual(client.get(f'/scans/{scan.id}/images/kf_0000.jpg').status_code, 200)
            self.assertEqual(client.get(f'/scans/{scan.id}/images/selection.json').status_code, 404)
            download = client.get(f'/scans/{scan.id}/reviewed-images.zip')
            self.assertEqual(download.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
                self.assertNotIn('images/kf_0000.jpg', archive.namelist())
                self.assertEqual(len([name for name in archive.namelist() if name.endswith('.jpg')]), 12)


if __name__ == '__main__':
    unittest.main()
