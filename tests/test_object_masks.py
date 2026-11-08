"""Object-mask plumbing and mask-to-geometry projection on known synthetic camera geometry.

These tests mock SAM 2 and COLMAP processes; they verify orchestration and geometry, not model quality.
"""
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import trimesh
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import isolation, main as server, reconstruction, segmentation
from backend.app.config import Settings
from backend.app.masks import write_colmap_masks

WIDTH, HEIGHT, FOCAL, K1 = 640, 480, 500.0, 0.02


def look_at(center):
    z = -center / np.linalg.norm(center)
    x = np.cross([0, 1, 0], z); x /= np.linalg.norm(x)
    rotation = np.stack([x, np.cross(z, x), z])
    return rotation, -rotation @ center


def quaternion(rotation):
    """Rotation matrix -> (w, x, y, z), stable for 180-degree rotations (eigenvector method)."""
    r = rotation
    k = np.array([[r[0, 0] - r[1, 1] - r[2, 2], r[1, 0] + r[0, 1], r[2, 0] + r[0, 2], r[2, 1] - r[1, 2]],
                  [r[1, 0] + r[0, 1], r[1, 1] - r[0, 0] - r[2, 2], r[2, 1] + r[1, 2], r[0, 2] - r[2, 0]],
                  [r[2, 0] + r[0, 2], r[2, 1] + r[1, 2], r[2, 2] - r[0, 0] - r[1, 1], r[1, 0] - r[0, 1]],
                  [r[2, 1] - r[1, 2], r[0, 2] - r[2, 0], r[1, 0] - r[0, 1], r[0, 0] + r[1, 1] + r[2, 2]]]) / 3
    x, y, z, w = np.linalg.eigh(k)[1][:, -1]
    return w, x, y, z


def write_model(sparse: Path, poses):
    """Write COLMAP binary cameras.bin/images.bin for one SIMPLE_RADIAL camera."""
    sparse.mkdir(parents=True)
    with (sparse / "cameras.bin").open("wb") as handle:
        handle.write(struct.pack("<Q", 1))
        handle.write(struct.pack("<iiQQ", 1, 2, WIDTH, HEIGHT) + struct.pack("<4d", FOCAL, WIDTH / 2, HEIGHT / 2, K1))
    with (sparse / "images.bin").open("wb") as handle:
        handle.write(struct.pack("<Q", len(poses)))
        for index, (name, rotation, translation) in enumerate(poses, 1):
            handle.write(struct.pack("<idddddddi", index, *quaternion(rotation), *translation, 1))
            handle.write(name.encode() + b"\0" + struct.pack("<Q", 1) + struct.pack("<ddq", 1.0, 2.0, -1))


def opencv_pixels(points, rotation, translation):
    camera = np.array([[FOCAL, 0, WIDTH / 2], [0, FOCAL, HEIGHT / 2], [0, 0, 1]])
    pixels, _ = cv2.projectPoints(points, cv2.Rodrigues(rotation)[0], translation, camera, np.array([K1, 0, 0, 0]))
    return pixels.reshape(-1, 2)


def synthetic_scene(root: Path):
    """A unit box (the object) on a large floor grid (the background), seen by six cameras."""
    box = trimesh.creation.box()
    floor = trimesh.creation.box(extents=(6, 0.02, 6))
    floor = floor.subdivide().subdivide().subdivide()
    floor.apply_translation((0, 0.8, 0))
    poses, masks = [], root / "object_masks"
    masks.mkdir()
    for index, azimuth in enumerate(np.linspace(0, 2 * np.pi, 6, endpoint=False)):
        rotation, translation = look_at(np.array([5 * np.sin(azimuth), -2.5, 5 * np.cos(azimuth)]))
        name = f"kf_{index:04d}.jpg"
        poses.append((name, rotation, translation))
        silhouette = cv2.convexHull(opencv_pixels(box.vertices, rotation, translation).astype(np.float32)).astype(np.int32)
        mask = np.zeros((HEIGHT, WIDTH), np.uint8)
        cv2.fillConvexPoly(mask, silhouette, 255)
        cv2.imwrite(str(masks / f"{name}.png"), cv2.dilate(mask, np.ones((5, 5), np.uint8)))  # Loose like a real mask.
    write_model(root / "sparse", poses)
    return box, floor, masks


class ProjectionTests(unittest.TestCase):
    def test_binary_model_round_trip_and_projection_match_opencv(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            box, _, _ = synthetic_scene(root)
            cameras = isolation.read_cameras(root / "sparse/cameras.bin")
            images = isolation.read_images(root / "sparse/images.bin")
            self.assertEqual(cameras[1][:3], ("SIMPLE_RADIAL", WIDTH, HEIGHT))
            self.assertEqual([image[0] for image in images], [f"kf_{i:04d}.jpg" for i in range(6)])
            points = np.vstack([box.vertices, np.random.default_rng(2).uniform(-1, 1, (50, 3))])
            for name, rotation, translation, camera_id in images:
                pixels, front = isolation.project(points, cameras[camera_id], rotation, translation)
                self.assertTrue(front.all())
                np.testing.assert_allclose(pixels, opencv_pixels(points, rotation, translation), atol=1e-6)

    def test_isolation_keeps_object_and_drops_background(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            box, floor, masks = synthetic_scene(root)
            scene = trimesh.util.concatenate([box, floor])
            isolated, stats = isolation.isolate_mesh(scene, root / "sparse", masks)
            self.assertEqual(stats["masked_views"], 6)
            # Every box vertex survives; at most a sliver of the floor (hidden under the box) remains.
            kept = {tuple(v) for v in np.round(isolated.vertices, 6)}
            self.assertTrue({tuple(v) for v in np.round(box.vertices, 6)} <= kept)
            self.assertLess(len(isolated.faces) - len(box.faces), 0.05 * len(floor.faces))

    def test_isolation_needs_masks_for_two_views(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            box, _, masks = synthetic_scene(root)
            for path in sorted(masks.iterdir())[1:]:
                path.unlink()
            with self.assertRaises(ValueError):
                isolation.isolate_mesh(box, root / "sparse", masks)


class MaskFileTests(unittest.TestCase):
    def test_object_and_watermark_masks_combine_per_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir(); (root / "objects").mkdir()
            for index in range(2):
                cv2.imwrite(str(root / f"images/kf_{index:04d}.jpg"), np.full((720, 1280, 3), 90, np.uint8))
                obj = np.zeros((720, 1280), np.uint8); obj[100 + index * 100:400, 200:1250] = 255
                cv2.imwrite(str(root / f"objects/kf_{index:04d}.jpg.png"), obj)
            write_colmap_masks(root / "images", root / "masks", Settings().feature_mask_regions, root / "objects")
            first = cv2.imread(str(root / "masks/kf_0000.jpg.png"), cv2.IMREAD_GRAYSCALE)
            second = cv2.imread(str(root / "masks/kf_0001.jpg.png"), cv2.IMREAD_GRAYSCALE)
            self.assertEqual((first[150, 600], second[150, 600]), (255, 0))  # Each view keeps its own mask.
            self.assertEqual((first[50, 600], first[300, 1180]), (0, 255))
            (root / "objects/kf_0001.jpg.png").unlink()
            with self.assertRaises(ValueError):
                write_colmap_masks(root / "images", root / "masks2", (), root / "objects")


def fake_segment(frames_area):
    """Stand-in for the SAM 2 subprocess: writes the files scripts/segment.py produces."""
    def run(command, log, timeout):
        images, output = Path(command[command.index("--images") + 1]), Path(command[command.index("--output") + 1])
        frames = json.loads(Path(command[command.index("--request") + 1]).read_text())["frames"]
        (output / "masks").mkdir(parents=True); (output / "previews").mkdir()
        for name in frames:
            with Image.open(images / name) as image:
                size = image.size
            mask = np.zeros(size[::-1], np.uint8); mask[: max(1, int(size[1] * frames_area)), :] = 255
            Image.fromarray(mask).save(output / "masks" / f"{name}.png")
            Image.new("RGB", (8, 8)).save(output / "previews" / name)
        report = dict(frames={name: frames_area for name in frames}, device="cpu", seconds=1.0)
        (output / "segmentation.json").write_text(json.dumps(report))
        log.write_text("fake segmentation")
        return 0
    return run


class SegmentationAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "images").mkdir()
        for index in range(3):
            cv2.imwrite(str(self.root / f"images/kf_{index:04d}.jpg"), np.full((90, 160, 3), 120, np.uint8))
        self.frames = [f"kf_{index:04d}.jpg" for index in range(3)]
        checkpoint = self.root / "sam.pt"; checkpoint.write_bytes(b"weights")
        self.settings = Settings(sam2_checkpoint=str(checkpoint))

    def run_segmentation(self, runner):
        with patch.object(segmentation, "run_logged", side_effect=runner):
            return segmentation.run_segmentation(self.root / "images", self.frames, [{"frame": "kf_0001.jpg", "points": [[.5, .5, 1]]}],
                                                 self.root / "object_masks", self.settings)

    def test_valid_masks_replace_previous_and_release_lock(self):
        (self.root / "object_masks").mkdir(); (self.root / "object_masks/old").write_text("old")
        result = self.run_segmentation(fake_segment(.3))
        self.assertTrue(result["done"], result["msg"])
        self.assertFalse((self.root / "object_masks/old").exists())
        self.assertEqual(len(list((self.root / "object_masks/masks").iterdir())), 3)
        self.assertFalse(reconstruction._heavy_job.locked())

    def test_empty_masks_warn_and_bad_output_keeps_previous(self):
        result = self.run_segmentation(fake_segment(0.0))
        self.assertEqual((result["lvl"], len(result["empty"])), ("warn", 3))
        def missing_mask(command, log, timeout):
            fake_segment(.3)(command, log, timeout)
            next((Path(command[command.index("--output") + 1]) / "masks").iterdir()).unlink()
            return 0
        with self.assertRaises(ValueError):
            self.run_segmentation(missing_mask)
        self.assertEqual(len(list((self.root / "object_masks/masks").iterdir())), 3)  # Previous run untouched.
        self.assertFalse(reconstruction._heavy_job.locked())

    def test_missing_checkpoint_and_busy(self):
        result = segmentation.run_segmentation(self.root / "images", self.frames, [], self.root / "out", Settings(sam2_checkpoint="missing.pt"))
        self.assertIn("checkpoint not found", result["msg"])
        reconstruction._heavy_job.acquire()
        try:
            self.assertIn("Another heavy task", self.run_segmentation(fake_segment(.3))["msg"])
        finally:
            reconstruction._heavy_job.release()


def textured(seed):
    rng = np.random.default_rng(seed)
    image = np.full((540, 960, 3), 110, np.uint8)
    for _ in range(600):
        x, y = rng.integers([12, 12], [948, 528])
        cv2.circle(image, (int(x), int(y)), int(rng.integers(2, 8)), tuple(int(v) for v in rng.integers(30, 255, 3)), -1)
    return cv2.imencode(".jpg", image)[1].tobytes()


class TurnedObjectFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        checkpoint = self.root / "sam.pt"; checkpoint.write_bytes(b"weights")
        for target, value in (("SCANS", self.root), ("SETTINGS", Settings(sam2_checkpoint=str(checkpoint)))):
            patcher = patch.object(server, target, value); patcher.start(); self.addCleanup(patcher.stop)

    def test_turn_mode_saves_changed_views_without_scene_continuity(self):
        scan = server.Scan()
        scan.set_motion("turn"); scan.state = "capturing"
        self.assertTrue(scan.process(textured(1))["accepted"])
        scan.last_photo["time"] -= 1
        repeat = scan.process(textured(1))
        self.assertFalse(repeat["accepted"]); self.assertIn("Turn the object a little more", repeat["msg"])
        # A completely different view fails orbit continuity but is a valid turned-object view.
        self.assertTrue(scan.process(textured(99))["accepted"])
        with self.assertRaises(ValueError):
            scan.set_motion("orbit")

    def test_turned_capture_requires_masks_and_ships_them_in_zip(self):
        with TestClient(server.app) as client, patch.object(segmentation, "run_logged", side_effect=fake_segment(.25)), \
                patch.object(server, "reconstruct", return_value=dict(msg="saved", lvl="ok", done=True, model_ready=False)) as rebuild:
            with client.websocket_connect("/ws") as ws:
                ready = ws.receive_json()
                self.assertIsNone(ready["segmentation_problem"])
                ws.send_text("motion:turn"); self.assertEqual(ws.receive_json()["motion"], "turn")
                for seed in range(12):
                    ws.send_text("import"); ws.receive_json()
                    ws.send_bytes(textured(seed)); self.assertTrue(ws.receive_json()["accepted"])
                ws.send_text("finish"); photos = ws.receive_json()["photos"]
                ws.send_json({"type": "confirm", "selected": photos})
                self.assertEqual(ws.receive_json()["type"], "busy")
                refused = ws.receive_json()
                self.assertIn("need AI object masks", refused["msg"])
                ws.send_json({"type": "segment", "selected": photos, "prompts": [{"frame": photos[3], "points": [[0.5, 0.5, 1], [0.1, 0.1, 0]]}]})
                self.assertEqual(ws.receive_json()["type"], "segmenting")
                segmented = ws.receive_json()
                self.assertTrue(segmented["done"], segmented["msg"])
                self.assertEqual(client.get(f"/scans/{ready['scan_id']}/object_masks/{photos[0]}").status_code, 200)
                ws.send_json({"type": "confirm", "selected": photos})
                ws.receive_json(); result = ws.receive_json()
                self.assertTrue(result["done"], result["msg"])
        masks_argument = rebuild.call_args.args[3]
        self.assertEqual(len(list(masks_argument.glob("*.png"))), 12)
        with zipfile.ZipFile(self.root / ready["scan_id"] / "reviewed-images.zip") as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("selection.json"))
        self.assertEqual(len([n for n in names if n.startswith("object_masks/")]), 12)
        self.assertIn("segmentation.json", names)
        self.assertEqual((manifest["capture_motion"], manifest["ai_object_masks"]), ("turn", True))
        self.assertNotIn("sam2_checkpoint", manifest["settings"])  # No machine paths in the shared ZIP.
        self.assertNotIn("colmap", manifest["settings"])

    def test_segment_request_validation(self):
        with TestClient(server.app) as client, client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "segment", "selected": ["kf_0000.jpg", "kf_0001.jpg"], "prompts": [{"frame": "kf_0000.jpg", "points": [[1.5, 0.5, 1]]}]})
            self.assertEqual(ws.receive_json()["type"], "error")
            ws.send_json({"type": "segment", "selected": ["kf_0000.jpg", "kf_0001.jpg"], "prompts": [{"frame": "kf_0000.jpg", "points": [[0.5, 0.5, 1]]}]})
            ws.receive_json()  # segmenting
            self.assertIn("review", ws.receive_json()["msg"])


class ReconstructionWithMasksTests(unittest.TestCase):
    def test_isolated_object_exported_and_stale_object_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "images").mkdir()
            scene_root = root / "scene"; scene_root.mkdir()
            box, floor, masks = synthetic_scene(scene_root)
            for path in masks.iterdir():
                cv2.imwrite(str(root / "images" / path.name[:-4]), np.full((HEIGHT, WIDTH, 3), 100, np.uint8))
            def produce(command, log, timeout):
                workspace = Path(command[command.index("--workspace_path") + 1])
                self.assertIn("--mask_path", command)
                (workspace / "dense/0").mkdir(parents=True)
                trimesh.util.concatenate([box, floor]).export(workspace / "dense/0/meshed-poisson.ply")
                (scene_root / "sparse").replace(workspace / "sparse")
                (workspace / "sparse").rename(workspace / "sparse0"); (workspace / "sparse").mkdir()
                (workspace / "sparse0").rename(workspace / "sparse/0")
                return 0
            help_text = "--workspace_path --image_path --quality --single_camera --mesher --mask_path"
            with patch.object(reconstruction, "resolve_tool", return_value="colmap"), \
                 patch.object(reconstruction, "colmap_help", return_value=help_text), \
                 patch.object(reconstruction, "run_logged", side_effect=produce):
                result = reconstruction.reconstruct(root / "images", root, Settings(mode="reconstruct"), masks)
            self.assertTrue(result["object_ready"], result["msg"])
            self.assertLess(result["isolation"]["kept_faces"], result["isolation"]["input_faces"])
            self.assertGreater(len(trimesh.load(root / "object.glb", force="mesh").faces), 0)
            # A rerun whose isolation fails must not leave the earlier object beside a new mesh.
            def no_sparse(command, log, timeout):
                workspace = Path(command[command.index("--workspace_path") + 1])
                (workspace / "dense/0").mkdir(parents=True)
                box.export(workspace / "dense/0/meshed-poisson.ply")
                return 0
            with patch.object(reconstruction, "resolve_tool", return_value="colmap"), \
                 patch.object(reconstruction, "colmap_help", return_value=help_text), \
                 patch.object(reconstruction, "run_logged", side_effect=no_sparse):
                result = reconstruction.reconstruct(root / "images", root, Settings(mode="reconstruct"), masks)
            self.assertTrue(result["model_ready"]); self.assertFalse(result["object_ready"])
            self.assertIn("AI isolation failed", result["msg"])
            self.assertFalse((root / "object.glb").exists())


if __name__ == "__main__":
    unittest.main()
