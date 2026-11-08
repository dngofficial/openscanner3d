"""Background jobs, cancellation, restart recovery, texturing plumbing and labelled fallbacks.

COLMAP/OpenMVS/Blender are replaced by fakes here: this checks orchestration and file validation,
not real reconstruction or texture quality.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import trimesh
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import jobs, main as server, reconstruction, texturing
from backend.app.config import Settings

HELP = "--workspace_path --image_path --quality --single_camera --mesher --mask_path"


def alive(pid: int) -> bool:
    if os.name == "nt":
        output = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in output
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.1)
    return False


def write_images(folder: Path, count=2):
    folder.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        Image.new("RGB", (64, 48), (90, 90, 90)).save(folder / f"kf_{index:04d}.jpg")


def textured_obj(folder: Path) -> Path:
    """What TextureMesh writes: OBJ + MTL + texture atlas."""
    folder.mkdir(parents=True, exist_ok=True)
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.TextureVisuals(uv=np.random.default_rng(0).random((len(mesh.vertices), 2)),
                                                image=Image.new("RGB", (32, 32), (180, 60, 40)))
    mesh.export(folder / "textured.obj")
    return folder / "textured.obj"


class JobTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "20261107_120000_0000abcd"; self.folder.mkdir()
        self.jobs = jobs.Jobs()

    def test_cancel_stops_wrapper_and_child_process_tree(self):
        child = self.root / "child.py"
        child.write_text("import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
        launcher = self.root / ("fake_colmap.bat" if os.name == "nt" else "fake_colmap.sh")
        pid_file = self.root / "pid.txt"
        if os.name == "nt":
            launcher.write_text(f'@echo off\r\n"{sys.executable}" "{child}" "{pid_file}"\r\n')
        else:
            launcher.write_text(f'#!/bin/sh\n"{sys.executable}" "{child}" "{pid_file}"\n'); launcher.chmod(0o755)
        work = lambda progress, cancel: reconstruction.reconstruct(self.root / "images", self.folder, Settings(
            mode="reconstruct", colmap=str(launcher), feature_mask_regions=()), None, progress, cancel)
        write_images(self.root / "images")
        with patch.object(reconstruction, "colmap_help", return_value=HELP):
            self.assertTrue(self.jobs.start(self.folder.name, self.folder, work))
            self.assertFalse(self.jobs.start("other", self.folder, work))  # One heavy job at a time.
            self.assertTrue(wait_for(pid_file.exists))
            pid = int(pid_file.read_text())
            self.assertEqual(jobs.read_status(self.folder)["stage"], "COLMAP automatic reconstruction")
            started = time.monotonic()
            self.assertTrue(self.jobs.cancel(self.folder.name))
            self.jobs.wait(30)
        self.assertLess(time.monotonic() - started, 15)
        status = jobs.read_status(self.folder)
        self.assertEqual(status["state"], "cancelled", status)
        self.assertTrue(wait_for(lambda: not alive(pid), 10), "child process survived cancellation")
        self.assertFalse(reconstruction._heavy_job.locked())
        self.assertIsNone(self.jobs.current)

    def test_ready_failed_and_restart_recovery(self):
        self.assertTrue(self.jobs.start("a", self.folder, lambda progress, cancel: (progress("exporting mesh"),
                                                                                  dict(msg="ok", done=True, model_ready=True))[1]))
        self.jobs.wait(10)
        self.assertEqual(jobs.read_status(self.folder)["state"], "ready")
        self.assertTrue(self.jobs.start("a", self.folder, lambda progress, cancel: dict(msg="COLMAP exited with code 1.", done=False)))
        self.jobs.wait(10)
        self.assertEqual(jobs.read_status(self.folder)["message"], "COLMAP exited with code 1.")
        self.assertEqual(jobs.read_status(self.folder)["state"], "failed")
        jobs.write_status(self.folder, state="running", stage="dense stereo")
        self.assertEqual(jobs.recover(self.root), 1)
        self.assertEqual(jobs.read_status(self.folder)["state"], "interrupted")

    def test_colmap_stage_comes_from_log_text(self):
        log = self.folder / "colmap.log"
        log.write_text("==\nFeature extraction\n==\n...\n==\nExhaustive feature matching\n==\n")
        self.assertEqual(jobs.colmap_stage(log), "feature matching")
        log.write_text(log.read_text() + "Image undistortion\nPatchMatch stereo\n")
        self.assertEqual(jobs.colmap_stage(log), "dense stereo")
        self.assertIsNone(jobs.colmap_stage(self.folder / "missing.log"))


class ApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "scans"; self.root.mkdir()  # mark_fallback resolves ROOT / "scans".
        for target, value in (("SCANS", self.root), ("JOBS", jobs.Jobs())):
            patcher = patch.object(server, target, value); patcher.start(); self.addCleanup(patcher.stop)

    def test_status_list_cancel_and_fallback_label(self):
        folder = self.root / "20261107_120000_0000abcd"; folder.mkdir()
        trimesh.creation.box().export(folder / "mesh.glb")
        jobs.write_status(folder, state="ready", stage="finished", message="Experimental mesh exported.")
        (self.root / "20261107_120500_0000beef").mkdir()  # Capture-only scan with no outputs: not listed.
        with TestClient(server.app) as client:
            listed = client.get("/api/scans").json()
            self.assertEqual([item["scan_id"] for item in listed], [folder.name])
            self.assertEqual(listed[0]["files"], ["mesh.glb"])
            self.assertEqual(client.get(f"/api/scans/{folder.name}").json()["state"], "ready")
            self.assertEqual(client.get("/api/scans/../etc").status_code, 404)
            self.assertEqual(client.post(f"/api/scans/{folder.name}/cancel").status_code, 409)
            self.assertEqual(client.post(f"/api/scans/{folder.name}/cancel", headers={"origin": "http://evil.example"}).status_code, 403)
            self.assertEqual(client.get("/vendor/three/three.module.min.js").status_code, 200)
            self.assertEqual(client.get("/viewer.mjs").status_code, 200)
            from scripts import mark_fallback
            with patch.object(mark_fallback, "ROOT", self.root.parent), \
                 patch.object(sys, "argv", ["mark_fallback", folder.name, "--label", "Box test"]):
                self.assertEqual(mark_fallback.main(), 0)
            fallback = client.get(f"/api/scans/{folder.name}").json()["fallback"]
            self.assertEqual(fallback["label"], "Box test")
            self.assertIn("mesh.glb", fallback["models"])

    def test_interrupted_job_marked_on_startup(self):
        folder = self.root / "20261107_120000_0000abcd"; folder.mkdir()
        jobs.write_status(folder, state="running", stage="dense stereo")
        with TestClient(server.app) as client:
            self.assertEqual(client.get(f"/api/scans/{folder.name}").json()["state"], "interrupted")

    def test_confirm_in_reconstruct_mode_starts_background_job(self):
        settings = Settings(mode="reconstruct")
        def fake(images, output, settings_, masks, progress, cancel):
            progress("exporting mesh")
            trimesh.creation.box().export(output / "mesh.glb")
            return dict(msg="Experimental mesh exported.", done=True, model_ready=True)
        with patch.object(server, "SETTINGS", settings), patch.object(server, "reconstruct", side_effect=fake), \
                TestClient(server.app) as client, client.websocket_connect("/ws") as ws:
            ready = ws.receive_json()
            for seed in range(12):
                ws.send_text("import"); ws.receive_json()
                image = np.random.default_rng(seed).integers(0, 255, (120, 160, 3), dtype=np.uint8)
                path = self.root / "x.jpg"; Image.fromarray(image).save(path)
                ws.send_bytes(path.read_bytes()); self.assertTrue(ws.receive_json()["accepted"])
            ws.send_text("finish"); photos = ws.receive_json()["photos"]
            ws.send_json({"type": "confirm", "selected": photos}); ws.receive_json()
            started = ws.receive_json()
            self.assertTrue(started["job_started"], started)
            self.assertTrue(wait_for(lambda: client.get(f"/api/scans/{ready['scan_id']}").json().get("state") == "ready"))
            self.assertIn("mesh.glb", client.get(f"/api/scans/{ready['scan_id']}").json()["files"])


class TexturingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_openmvs_steps_and_trimesh_glb_keep_texture(self):
        dense = self.root / "dense"; (dense / "sparse").mkdir(parents=True)
        commands = []
        def run(command, log):
            commands.append(command)
            if "--export-type" in command:
                textured_obj(Path(command[command.index("-w") + 1]))
            return 0
        settings = Settings(glb_exporter="trimesh")
        with patch.object(texturing, "texturing_tools", return_value=("InterfaceCOLMAP", "TextureMesh", None)):
            obj = texturing.textured_obj("colmap", dense, self.root / "mesh.ply", self.root / "mvs", settings, run, self.root)
        self.assertEqual([c[0] for c in commands], ["colmap", "InterfaceCOLMAP", "TextureMesh"])
        self.assertIn("TXT", commands[0])
        info = texturing.export_textured_glb(obj, self.root / "textured.glb", settings, run, self.root)
        self.assertEqual((info["exporter"], info["faces"], info["texture_size"]), ("trimesh", 12, [32, 32]))

    def test_untextured_glb_and_missing_tools_are_rejected(self):
        trimesh.creation.box().export(self.root / "plain.glb")
        with self.assertRaises(ValueError):
            texturing.validate_textured_glb(self.root / "plain.glb")
        self.assertIn("not found", texturing.texturing_tools(Settings(openmvs_bin=str(self.root)))[2])
        with self.assertRaises(RuntimeError):
            texturing.export_textured_glb(self.root / "x.obj", self.root / "y.glb", Settings(blender="no-such-blender", glb_exporter="blender"),
                                          lambda c, l: 0, self.root)

    def test_reconstruction_publishes_textured_glb_and_drops_stale_one_on_failure(self):
        write_images(self.root / "images")
        def colmap(command, log, timeout, cancel=None):
            workspace = Path(command[command.index("--workspace_path") + 1])
            (workspace / "dense/0").mkdir(parents=True)
            trimesh.creation.icosphere().export(workspace / "dense/0/meshed-poisson.ply")
            return 0
        def tools(command, log, timeout, cancel=None):
            if "--export-type" in command:
                textured_obj(Path(command[command.index("-w") + 1]))
            return 0
        settings = Settings(mode="reconstruct", feature_mask_regions=(), openmvs_bin="openmvs", glb_exporter="trimesh")
        run = lambda command, *rest: (colmap if "automatic_reconstructor" in command else tools)(command, *rest)
        with patch.object(reconstruction, "resolve_tool", return_value="colmap"), \
             patch.object(reconstruction, "colmap_help", return_value=HELP), \
             patch.object(reconstruction, "run_logged", side_effect=run), \
             patch.object(texturing, "texturing_tools", return_value=("InterfaceCOLMAP", "TextureMesh", None)):
            result = reconstruction.reconstruct(self.root / "images", self.root, settings)
            self.assertTrue(result["textured_ready"], result["msg"])
            self.assertEqual(texturing.validate_textured_glb(self.root / "textured.glb")["texture_size"], [32, 32])
            failing = lambda command, *rest: colmap(command, *rest) if "automatic_reconstructor" in command else 1
            with patch.object(reconstruction, "run_logged", side_effect=failing):
                result = reconstruction.reconstruct(self.root / "images", self.root, settings)
        self.assertTrue(result["model_ready"]); self.assertFalse(result["textured_ready"])
        self.assertIn("Texturing failed", result["msg"])
        self.assertFalse((self.root / "textured.glb").exists())


if __name__ == "__main__":
    unittest.main()
