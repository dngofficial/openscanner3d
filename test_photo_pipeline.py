"""Run: python -m pip install httpx && python -m unittest -v test_photo_pipeline.py"""
from io import BytesIO
import json
from pathlib import Path
import struct
import sys
import tempfile
from threading import Event, Timer
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import numpy as np
import server
import reconstruction as pipeline


def photo_bytes(seed=1, oriented=False):
    image = Image.fromarray(np.random.default_rng(seed).integers(0, 255, (480, 640, 3), dtype=np.uint8))
    output = BytesIO()
    exif = Image.Exif()
    if oriented: exif[274] = 6
    image.save(output, "JPEG", exif=exif)
    return output.getvalue()


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(server, "SCANS", Path(self.temp.name))
        self.storage.start()
        server.SESSIONS.clear()
        self.client = TestClient(server.app)
        self.scan = self.client.post("/api/scans").json()
        self.path = "/api/scans/" + self.scan["id"]

    def tearDown(self):
        self.client.close()
        self.storage.stop()
        self.temp.cleanup()
        server.SESSIONS.clear()

    def upload(self, seed=1, oriented=False, name="test.jpg"):
        return self.client.post(self.path + "/photos", content=photo_bytes(seed, oriented), params={"name": name})

    def test_upload_duplicate_orientation_and_removal(self):
        first = self.upload(oriented=True, name="../outside.jpg").json()
        self.assertFalse(first["duplicate"])
        self.assertEqual((first["photo"]["width"], first["photo"]["height"]), (480, 640))
        self.assertTrue(first["photo"]["feature_points"])
        self.assertEqual(self.upload(oriented=True).json()["duplicate"], True)
        photo_id = first["photo"]["id"]
        self.assertEqual(self.client.get(self.path + "/thumbs/" + photo_id).status_code, 200)
        self.assertEqual(self.client.get(self.path + "/photos/" + photo_id + "/image").status_code, 200)
        root = Path(self.temp.name) / self.scan["id"]
        self.assertEqual(len(list((root / "images").glob("*.jpg"))), 1)
        self.assertFalse((Path(self.temp.name) / "outside.jpg").exists())
        result = self.client.delete(self.path + "/photos/" + photo_id)
        self.assertEqual(result.json()["photos"], [])
        self.assertFalse(list((root / "images").glob("*.jpg")))

    def test_rejection_and_origin(self):
        self.assertEqual(self.client.post(self.path + "/photos", content=b"bad").status_code, 400)
        self.assertEqual(self.client.post(self.path + "/build", json={}).status_code, 400)
        self.assertEqual(self.client.post("/api/scans", headers={"Origin": "https://unrelated.example"}).status_code, 403)
        self.assertEqual(self.client.get(self.path + "/files/session.json").status_code, 404)
        small = BytesIO()
        Image.new("RGB", (100, 100)).save(small, "PNG")
        self.assertEqual(self.client.post(self.path + "/photos", content=small.getvalue()).status_code, 400)

    def test_build_cancel_lock_and_restart(self):
        for i in range(3): self.upload(i)
        with patch.object(server, "find_colmap", return_value=None):
            result = self.client.post(self.path + "/build", json={})
            self.assertEqual(result.status_code, 400)
            self.assertIn("COLMAP", result.json()["detail"])
        with patch.object(server, "find_colmap", return_value="colmap"), patch.object(server.POOL, "submit") as submit:
            result = self.client.post(self.path + "/build", json={"dense": False})
            self.assertEqual(result.json()["state"], "queued")
            self.assertTrue(submit.called)
        self.assertEqual(self.upload(8).status_code, 409)
        self.assertEqual(self.client.post(self.path + "/build", json={}).status_code, 409)
        self.client.post(self.path + "/cancel")
        session = server.get_session(self.scan["id"])
        self.assertTrue(session.cancel.is_set())
        server.SESSIONS.clear()
        restored = self.client.get(self.path).json()
        self.assertEqual(restored["stage"], "interrupted")
        self.assertEqual(len(restored["photos"]), 3)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "images").mkdir()
        self.calls = []
        self.updates = []

    def tearDown(self):
        self.temp.cleanup()

    def fake_command(self, exe, command, args, log, cancel):
        args = list(map(str, args))
        self.calls.append((command, args))
        log.write_text("Fixture COLMAP command log\n")
        def output(option): return Path(args[args.index(option) + 1])
        if command == "mapper":
            for index, count in enumerate((3, 5)):
                model = output("--output_path") / str(index)
                model.mkdir()
                (model / "images.bin").write_bytes(struct.pack("<Q", count))
                (model / "points3D.bin").write_bytes(struct.pack("<Q", 10))
                (model / "cameras.bin").write_bytes(b"fixture")
        elif command == "model_converter":
            if args[-1] == "PLY":
                output("--output_path").write_text("ply\nformat ascii 1.0\nelement vertex 0\nend_header\n")
            else:
                (output("--output_path") / "points3D.txt").write_text(
                    "# 3D points\n" + "".join(f"{i} {i} {i+1} {i+2} 100 150 200 0.2 1 0 2 0\n" for i in range(10)))
        elif command == "stereo_fusion":
            output("--output_path").write_text("ply\nformat ascii 1.0\nelement vertex 0\nend_header\n")
        elif command == "poisson_mesher":
            import trimesh
            trimesh.creation.icosphere(subdivisions=1).export(output("--output_path"))

    def run_pipeline(self, dense=False, command=None, modern=True):
        options = ("--FeatureExtraction.use_gpu --FeatureExtraction.max_image_size --FeatureMatching.use_gpu"
                   if modern else "--SiftExtraction.use_gpu --SiftExtraction.max_image_size --SiftMatching.use_gpu")
        with patch.object(pipeline, "find_colmap", return_value="fixture-colmap"),              patch.object(pipeline, "help_options", return_value=options),              patch.object(pipeline, "execute", side_effect=command or self.fake_command):
            pipeline.reconstruct(self.root, dense, False, Event(), lambda **state: self.updates.append(state))

    def test_cpu_pipeline_largest_model_and_preview(self):
        self.run_pipeline()
        self.assertEqual(self.updates[-1]["state"], "completed")
        self.assertEqual(self.updates[-1]["registered_images"], 5)
        self.assertEqual(json.loads((self.root / "preview.json").read_text())["total"], 10)
        self.assertTrue((self.root / "results.zip").is_file())
        self.assertFalse((self.root / "mesh.glb").exists())
        extract = self.calls[0][1]
        self.assertEqual(extract[extract.index("--FeatureExtraction.use_gpu") + 1], "0")

    def test_legacy_colmap_options(self):
        self.run_pipeline(modern=False)
        self.assertIn("--SiftExtraction.use_gpu", self.calls[0][1])
        self.assertIn("--SiftMatching.use_gpu", self.calls[1][1])

    def test_dense_mesh_exports(self):
        self.run_pipeline(dense=True)
        self.assertEqual(self.updates[-1]["state"], "completed")
        for name in ("mesh.ply", "mesh.obj", "mesh.glb", "dense.ply"):
            self.assertGreater((self.root / name).stat().st_size, 0)

    def test_dense_failure_preserves_sparse(self):
        def fail_depth(*args):
            if args[1] == "patch_match_stereo": raise RuntimeError("No CUDA device")
            self.fake_command(*args)
        self.run_pipeline(dense=True, command=fail_depth)
        self.assertEqual(self.updates[-1]["state"], "partial")
        self.assertTrue((self.root / "sparse.ply").is_file())
        self.assertTrue((self.root / "results.zip").is_file())
        self.assertFalse((self.root / "mesh.glb").exists())

    def test_cancellation_stops_subprocess(self):
        cancel = Event()
        timer = Timer(.25, cancel.set)
        timer.start()
        start = time.monotonic()
        try:
            with self.assertRaises(pipeline.Cancelled):
                pipeline.execute(sys.executable, "-c", ["import time; time.sleep(30)"],
                                 self.root / "log.txt", cancel)
        finally: timer.join()
        self.assertLess(time.monotonic() - start, 5)


if __name__ == "__main__":
    unittest.main()
