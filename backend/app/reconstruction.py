"""Bounded adapter around the existing, experimental COLMAP mesh pipeline."""
import os
from pathlib import Path
import subprocess
import threading
import uuid

from .config import Settings, resolve_tool
from .masks import write_colmap_masks

_heavy_job = threading.Lock()
REQUIRED_FLAGS = ("--workspace_path", "--image_path", "--quality", "--single_camera", "--mesher")


def colmap_help(executable: str) -> str:
    result = subprocess.run(
        [executable, "automatic_reconstructor", "-h"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or not all(flag in output for flag in REQUIRED_FLAGS):
        raise RuntimeError("Installed COLMAP does not expose the required automatic_reconstructor options. Run doctor.")
    return output


def run_logged(command: list[str], log_path: Path, timeout: int) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            # Stop children as well as the launcher (COLMAP can be a .bat wrapper).
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=15)
            raise


def reconstruct(images: Path, output: Path, settings: Settings) -> dict:
    if settings.mode != "reconstruct":
        return dict(msg="Capture-only mode: photos saved. Run reconstruction on the partner's GPU laptop.", lvl="ok", done=True, model_ready=False)
    executable = resolve_tool(settings.colmap)
    if not executable:
        return dict(msg="COLMAP not found. Configure its path in config.local.toml and run doctor.", lvl="bad", done=False)
    if not _heavy_job.acquire(blocking=False):
        return dict(msg="Another reconstruction is running. Try Finish again when it completes.", lvl="warn", done=False)
    try:
        help_text = colmap_help(executable)
        if settings.feature_mask_regions and "--mask_path" not in help_text:
            return dict(msg="Installed COLMAP lacks --mask_path, so the configured watermark mask cannot be applied. "
                            "Set feature_mask_regions = [] only for unwatermarked photos.", lvl="bad", done=False)
        # Separate each attempt so a failed rerun cannot reuse a stale mesh.
        workspace = output / "runs" / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        command = [executable, "automatic_reconstructor", "--workspace_path", str(workspace),
                   "--image_path", str(images), "--quality", settings.quality,
                   "--single_camera", "1", "--mesher", "poisson"]
        if settings.feature_mask_regions:
            # Masks only exclude features (pose estimation); dense stereo still sees the full image.
            write_colmap_masks(images, workspace / "masks", settings.feature_mask_regions)
            command += ["--mask_path", str(workspace / "masks")]
        code = run_logged(command, output / "colmap.log", settings.timeout_seconds)
        if code:
            return dict(msg=f"COLMAP exited with code {code}. See colmap.log.", lvl="bad", done=False)
        mesh_path = workspace / "dense" / "0" / "meshed-poisson.ply"
        if not mesh_path.is_file():
            return dict(msg="No dense Poisson mesh was produced. Inspect colmap.log and the installed backend's GPU support.", lvl="bad", done=False)
        import numpy as np
        import trimesh
        mesh = trimesh.load(mesh_path, force="mesh")
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
            raise ValueError("COLMAP output is not a nonempty finite triangle mesh")
        for extension in ("obj", "glb"):
            destination = workspace / f"mesh.{extension}"
            mesh.export(destination)
        reloaded = trimesh.load(workspace / "mesh.glb", force="mesh")
        if not len(reloaded.faces):
            raise ValueError("Exported GLB contains no faces")
        for extension in ("obj", "glb"):
            os.replace(workspace / f"mesh.{extension}", output / f"mesh.{extension}")
        return dict(msg="Experimental mesh exported. Texturing and topology cleanup are not implemented yet.", lvl="ok", done=True, model_ready=True)
    except subprocess.TimeoutExpired:
        return dict(msg="Reconstruction timed out; its process tree was stopped. Photos remain available.", lvl="bad", done=False)
    finally:
        _heavy_job.release()
